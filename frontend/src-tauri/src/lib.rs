mod local;

use local::{Entry, Link, Scan, SentFile, Skipped, TreeItem};
use serde::Serialize;
use sha2::{Digest, Sha256};
use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::Mutex;
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

/// Ends the app: forgets everything held in memory (picked places, scans), then exits.
#[tauri::command]
fn quit_app(app: AppHandle, state: State<'_, Shared>) {
    state.pending.lock().unwrap().clear();
    state.scans.lock().unwrap().clear();
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
            quit_app
        ])
        .run(tauri::generate_context!())
        .expect("error while running the application");
}
