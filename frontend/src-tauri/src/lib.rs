mod local;
mod setup;

use local::{Entry, Link, Scan, SentFile, Skipped, TreeItem};
use serde::Serialize;
use sha2::{Digest, Sha256};
use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use tauri::{AppHandle, Manager, State};
use tauri_plugin_dialog::DialogExt;

/// In-memory state. Local paths stay in here and in the private file of the app data folder.
#[derive(Default)]
struct Shared {
    /// Places the person just picked, waiting to be linked to a server source.
    pending: Mutex<HashMap<String, (PathBuf, String)>>,
    /// The last scan per server source: (scan id, scan).
    scans: Mutex<HashMap<String, (String, Scan)>>,
    counter: Mutex<u64>,
    /// Serialises changes to the stored links.
    store: Mutex<()>,
    /// The admin application of the bundled program: one child for the whole session, started on first use.
    api: Arc<Mutex<Option<setup::ApiSession>>>,
}

impl Shared {
    fn next_id(&self) -> String {
        let mut c = self.counter.lock().unwrap();
        *c += 1;
        let nanos = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_nanos())
            .unwrap_or(0);
        let digest = Sha256::digest(format!("{nanos}:{}:{}", *c, std::process::id()).as_bytes());
        format!("{digest:x}")[..24].to_string()
    }
}

fn data_dir(app: &AppHandle) -> Result<PathBuf, String> {
    app.path().app_data_dir().map_err(|e| format!("Der App-Ordner ist nicht erreichbar: {e}"))
}

fn find_link(app: &AppHandle, server_id: &str) -> Result<Link, String> {
    local::load_links(&data_dir(app)?)?
        .into_iter()
        .find(|l| l.server_id == server_id)
        .ok_or_else(|| "Für diese Quelle ist auf diesem Computer kein Ordner und keine Datei hinterlegt.".to_string())
}

#[derive(Serialize)]
struct LinkView {
    server_id: String,
    kind: String,
    display: String,
    reachable: bool,
}

/// The places stored on this computer, with a display text for this window only.
#[tauri::command]
fn local_list(app: AppHandle) -> Result<Vec<LinkView>, String> {
    Ok(local::load_links(&data_dir(&app)?)?
        .iter()
        .map(|l| LinkView {
            server_id: l.server_id.clone(),
            kind: l.kind.clone(),
            display: local::display_of(l),
            reachable: local::reachable(l),
        })
        .collect())
}

#[derive(Serialize)]
struct Picked {
    token: String,
    name: String,
    kind: String,
}

/// Opens the system dialog. The path stays here; the window gets only a token and the name.
#[tauri::command]
async fn local_pick(app: AppHandle, state: State<'_, Shared>, kind: String) -> Result<Option<Picked>, String> {
    if kind != "folder" && kind != "file" {
        return Err("Unbekannte Art.".into());
    }
    let dialog = app.dialog().file();
    let chosen = if kind == "folder" {
        dialog.blocking_pick_folder()
    } else {
        dialog.add_filter("Textdateien", &["md", "txt"]).blocking_pick_file()
    };
    let Some(path) = chosen.and_then(|p| p.into_path().ok()) else { return Ok(None) };
    let meta = std::fs::symlink_metadata(&path).map_err(|e| format!("Nicht lesbar: {e}"))?;
    if meta.file_type().is_symlink() {
        return Err("Eine Verknüpfung kann nicht als Quelle dienen. Wähle den echten Ordner oder die echte Datei.".into());
    }
    if (kind == "folder" && !meta.is_dir()) || (kind == "file" && !meta.is_file()) {
        return Err("Das ist nicht, was gewählt werden sollte.".into());
    }
    let name = local::base_name(&path);
    let token = state.next_id();
    state.pending.lock().unwrap().insert(token.clone(), (path, kind.clone()));
    Ok(Some(Picked { token, name, kind }))
}

/// Connects a picked place to a source of the server. Replaces an older link of the same source.
#[tauri::command]
fn local_link(app: AppHandle, state: State<'_, Shared>, token: String, server_id: String) -> Result<(), String> {
    let (path, kind) = state
        .pending
        .lock()
        .unwrap()
        .remove(&token)
        .ok_or("Die Auswahl ist nicht mehr gültig. Bitte noch einmal wählen.")?;
    let _guard = state.store.lock().unwrap();
    let dir = data_dir(&app)?;
    let mut links = local::load_links(&dir)?;
    links.retain(|l| l.server_id != server_id);
    links.push(Link { server_id: server_id.clone(), kind, path: path.display().to_string() });
    local::save_links(&dir, &links)?;
    state.scans.lock().unwrap().remove(&server_id);
    Ok(())
}

/// Forgets the place of a source. Nothing on the computer is touched.
#[tauri::command]
fn local_unlink(app: AppHandle, state: State<'_, Shared>, server_id: String) -> Result<(), String> {
    let _guard = state.store.lock().unwrap();
    let dir = data_dir(&app)?;
    let mut links = local::load_links(&dir)?;
    links.retain(|l| l.server_id != server_id);
    local::save_links(&dir, &links)?;
    state.scans.lock().unwrap().remove(&server_id);
    Ok(())
}

#[derive(Serialize)]
struct ScanOut {
    scan_id: String,
    entries: Vec<Entry>,
    skipped: Vec<Skipped>,
    /// True when the place was read successfully and holds no text file at all.
    empty: bool,
}

/// Reads the whole place and returns the manifest. Any problem aborts: no partial manifest.
#[tauri::command]
async fn local_scan(app: AppHandle, state: State<'_, Shared>, server_id: String) -> Result<ScanOut, String> {
    let link = find_link(&app, &server_id)?;
    let result = tauri::async_runtime::spawn_blocking(move || local::scan_link(&link))
        .await
        .map_err(|_| "Das Lesen wurde unterbrochen.".to_string())??;
    let scan_id = state.next_id();
    let empty = result.entries.is_empty();
    state.scans.lock().unwrap().insert(server_id, (scan_id.clone(), result.scan));
    Ok(ScanOut { scan_id, entries: result.entries, skipped: result.skipped, empty })
}

/// Content of files from the last scan, checked against the scanned hash.
#[tauri::command]
async fn local_read(
    state: State<'_, Shared>,
    server_id: String,
    scan_id: String,
    paths: Vec<String>,
) -> Result<Vec<SentFile>, String> {
    let scans = state.scans.lock().unwrap();
    let (current, scan) = scans.get(&server_id).ok_or("Es gibt kein aktuelles Lesen. Bitte neu abgleichen.")?;
    if current != &scan_id {
        return Err("Das Lesen ist veraltet. Bitte neu abgleichen.".into());
    }
    local::read_scanned(scan, &paths)
}

/// One level of a folder, for the tree.
#[tauri::command]
async fn local_tree(app: AppHandle, server_id: String, rel: String) -> Result<Vec<TreeItem>, String> {
    let link = find_link(&app, &server_id)?;
    tauri::async_runtime::spawn_blocking(move || local::list_level(&link, &rel))
        .await
        .map_err(|_| "Das Lesen wurde unterbrochen.".to_string())?
}

/// Starts the bundled server program with `setup <args>` and returns its JSON answer (see setup.rs).
async fn run_setup(app: AppHandle, args: Vec<String>, stdin_line: Option<String>) -> Result<serde_json::Value, String> {
    let program = setup::locate(app.path().resource_dir().ok(), setup::dev_override())?;
    let dir = data_dir(&app)?;
    tauri::async_runtime::spawn_blocking(move || setup::run(&program, &dir, &args, stdin_line.as_deref()))
        .await
        .map_err(|_| "Der Schritt wurde unterbrochen.".to_string())?
}

fn program_of(app: &AppHandle) -> Result<PathBuf, String> {
    setup::locate(app.path().resource_dir().ok(), setup::dev_override())
}

/// What is already set up on this computer: mailbox settings, stored password, Claude Desktop entry.
#[tauri::command]
async fn setup_status(app: AppHandle) -> Result<serde_json::Value, String> {
    let program = program_of(&app)?;
    run_setup(app, setup::claude_args("status", &program), None).await
}

/// Checks the sign-in at the provider and, only if it works, stores the password in the keychain.
/// The password goes to the child process on standard input and nowhere else.
#[tauri::command]
async fn setup_save_mailbox(
    app: AppHandle,
    email: String,
    host: String,
    port: u16,
    security: String,
    password: String,
) -> Result<serde_json::Value, String> {
    run_setup(app, setup::save_mailbox_args(&email, &host, port, &security), Some(password)).await
}

/// Sends one test mail to the person's own address; optionally marks it as an own address.
#[tauri::command]
async fn setup_send_test(app: AppHandle, to: String, mark_own: bool, lang: Option<String>) -> Result<serde_json::Value, String> {
    run_setup(app, setup::send_test_args(&to, mark_own, lang.as_deref()), None).await
}

/// What connecting Claude Desktop would change. Changes nothing.
#[tauri::command]
async fn setup_preview_claude(app: AppHandle) -> Result<serde_json::Value, String> {
    let program = program_of(&app)?;
    run_setup(app, setup::claude_args("preview-claude", &program), None).await
}

/// Adds or updates only the entry of this program in Claude Desktop's configuration (backup first).
#[tauri::command]
async fn setup_connect_claude(app: AppHandle) -> Result<serde_json::Value, String> {
    let program = program_of(&app)?;
    run_setup(app, setup::claude_args("connect-claude", &program), None).await
}

/// Removes only the entry of this program from Claude Desktop's configuration (backup first). No entry is fine.
#[tauri::command]
async fn setup_disconnect_claude(app: AppHandle) -> Result<serde_json::Value, String> {
    run_setup(app, vec!["disconnect-claude".to_string()], None).await
}

/// One-time link to register a passkey. Default: on the local approval page (needs Claude Desktop running the
/// server). With `phone`: on the public page, to be opened on the phone (needs the background service).
#[tauri::command]
async fn setup_approval_link(app: AppHandle, phone: Option<bool>) -> Result<serde_json::Value, String> {
    run_setup(app, setup::approval_link_args(phone.unwrap_or(false)), None).await
}

/// Phone use: is the service set up and running, and is there a real sign-in passphrase yet? Never the passphrase.
#[tauri::command]
async fn setup_remote_status(app: AppHandle) -> Result<serde_json::Value, String> {
    let program = program_of(&app)?;
    run_setup(app, setup::claude_args("remote-status", &program), None).await
}

/// Makes a new sign-in passphrase for the phone and returns it exactly once. It is shown in the window and
/// kept nowhere else; the program stores only its hash.
#[tauri::command]
async fn setup_passphrase_new(app: AppHandle) -> Result<serde_json::Value, String> {
    let program = program_of(&app)?;
    run_setup(app, setup::claude_args("passphrase-new", &program), None).await
}

/// Phone use, tunnel card: Tailscale's state and the public address, shown before anything changes.
#[tauri::command]
async fn setup_tunnel_status(app: AppHandle) -> Result<serde_json::Value, String> {
    run_setup(app, vec!["tunnel-status".to_string()], None).await
}

/// Starts the service, switches the Funnel on and tries the address. `address` is the one the person
/// confirmed; the program refuses it when it is no longer Tailscale's real address.
#[tauri::command]
async fn setup_tunnel_enable(app: AppHandle, address: String) -> Result<serde_json::Value, String> {
    let program = program_of(&app)?;
    run_setup(app, setup::tunnel_enable_args(&program, &address), None).await
}

/// The way back to "only this computer": Funnel off, background service removed, phone sign-ins ended.
/// Mailbox, recipients and passkeys stay.
#[tauri::command]
async fn setup_tunnel_disable(app: AppHandle) -> Result<serde_json::Value, String> {
    run_setup(app, vec!["tunnel-disable".to_string()], None).await
}

/// Local administration: the bundled program's `local` command, one JSON answer. No port, no token.
/// Only the actions listed in `setup::local_admin_args` get through.
#[tauri::command]
async fn local_admin(
    app: AppHandle,
    action: String,
    reference: Option<String>,
    name: Option<String>,
    address: Option<String>,
    value: Option<String>,
    own: Option<bool>,
) -> Result<serde_json::Value, String> {
    let args = setup::local_admin_args(
        &action,
        reference.as_deref(),
        name.as_deref(),
        address.as_deref(),
        value.as_deref(),
        own.unwrap_or(false),
    )?;
    let program = program_of(&app)?;
    let dir = data_dir(&app)?;
    tauri::async_runtime::spawn_blocking(move || setup::run_command(&program, "local", &dir, &args, None))
        .await
        .map_err(|_| "Der Schritt wurde unterbrochen.".to_string())?
}

/// One request to the admin application of the bundled program (see `local_api.py`). The program signs in to
/// itself; the window never sees a token. Starts the child on first use and again if it has ended.
#[tauri::command]
async fn local_api(
    app: AppHandle,
    state: State<'_, Shared>,
    method: String,
    path: String,
    body: Option<serde_json::Value>,
) -> Result<serde_json::Value, String> {
    let request = setup::api_request(&method, &path, body)?;
    let program = program_of(&app)?;
    let dir = data_dir(&app)?;
    let slot = Arc::clone(&state.api);
    tauri::async_runtime::spawn_blocking(move || {
        let mut guard = slot.lock().map_err(|_| "Die lokale Verwaltung ist gestört.".to_string())?;
        for attempt in 0..2 {
            if guard.is_none() {
                *guard = Some(setup::ApiSession::start(&program, &dir)?);
            }
            match guard.as_mut().unwrap().ask(&request) {
                Ok(answer) => return Ok(answer),
                Err(e) => {
                    *guard = None; // the child is gone: a new one for the next try
                    if attempt == 1 {
                        return Err(e);
                    }
                }
            }
        }
        Err("Die lokale Verwaltung antwortet nicht.".to_string())
    })
    .await
    .map_err(|_| "Der Schritt wurde unterbrochen.".to_string())?
}

/// Opens the passkey enrollment link, a Tailscale page or Claude's connector settings in the default browser.
/// Refuses every other address.
#[tauri::command]
fn open_link(url: String) -> serde_json::Value {
    match setup::open_in_browser(&url) {
        Ok(()) => serde_json::json!({ "ok": true }),
        Err(reason) => serde_json::json!({ "ok": false, "reason": reason }),
    }
}

/// Ends the app: forgets everything held in memory (picked places, scans), then exits.
#[tauri::command]
fn quit_app(app: AppHandle, state: State<'_, Shared>) {
    state.pending.lock().unwrap().clear();
    state.scans.lock().unwrap().clear();
    *state.api.lock().unwrap() = None;
    app.exit(0);
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .manage(Shared::default())
        .invoke_handler(tauri::generate_handler![
            local_list,
            local_pick,
            local_link,
            local_unlink,
            local_scan,
            local_read,
            local_tree,
            setup_status,
            setup_save_mailbox,
            setup_send_test,
            setup_preview_claude,
            setup_connect_claude,
            setup_disconnect_claude,
            setup_approval_link,
            setup_remote_status,
            setup_passphrase_new,
            setup_tunnel_status,
            setup_tunnel_enable,
            setup_tunnel_disable,
            local_admin,
            local_api,
            open_link,
            quit_app
        ])
        .run(tauri::generate_context!())
        .expect("error while running the application");
}
