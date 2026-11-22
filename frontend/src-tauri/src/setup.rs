//! The setup wizard's side of the desktop app: it starts the bundled server program with the `setup`
//! command and passes on its one line of JSON. The work itself (keychain, sign-in check, test mail,
//! Claude Desktop configuration) is done by that program; see `setup_cli.py` in the server.
//!
//! The mail password travels only through the standard input of that one child process: never as an
//! argument, never in a log line, never in an error text. Values from the window are passed as
//! `--name=value`, so a value that starts with a dash can never act as an option.

use serde_json::Value;
use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, ChildStdout, Command, Stdio};

/// Name of the bundled program inside the app's resource folder `server/`.
#[cfg(windows)]
const PROGRAM: &str = "mail-assistant-server.exe";
#[cfg(not(windows))]
const PROGRAM: &str = "mail-assistant-server";

/// While developing there is no bundle; the environment variable points at a built program.
const DEV_OVERRIDE: &str = "MAIL_ASSISTANT_SERVER";

pub fn program_in(resource_dir: &Path) -> PathBuf {
    resource_dir.join("server").join(PROGRAM)
}

pub fn locate(resource_dir: Option<PathBuf>, override_path: Option<String>) -> Result<PathBuf, String> {
    let path = match override_path.filter(|p| !p.trim().is_empty()) {
        Some(p) => PathBuf::from(p),
        None => program_in(&resource_dir.ok_or("Der Programm-Ordner der App ist nicht erreichbar.")?),
    };
    if !path.is_absolute() {
        return Err("Der Pfad des Mail-Assistant-Programms ist nicht eindeutig.".into());
    }
    if !path.is_file() {
        return Err("Das Mail-Assistant-Programm fehlt in dieser Installation. Bitte die App neu installieren.".into());
    }
    Ok(path)
}

pub fn dev_override() -> Option<String> {
    std::env::var(DEV_OVERRIDE).ok()
}

/// The last non-empty line of the program's output must be one JSON object.
pub fn parse_reply(stdout: &str) -> Result<Value, String> {
    let line = stdout
        .lines()
        .rev()
        .find(|l| !l.trim().is_empty())
        .ok_or("Das Hilfsprogramm hat nicht geantwortet.")?;
    match serde_json::from_str::<Value>(line) {
        Ok(v) if v.is_object() => Ok(v),
        _ => Err("Das Hilfsprogramm hat eine unverständliche Antwort gegeben.".into()),
    }
}

/// Runs `<program> setup <args…>` with the data folder in the environment. `stdin_line` (the password)
/// is written as one line and the pipe is closed. Blocking: call it from `spawn_blocking`.
pub fn run(program: &Path, data_dir: &Path, args: &[String], stdin_line: Option<&str>) -> Result<Value, String> {
    run_command(program, "setup", data_dir, args, stdin_line)
}

/// Same, for any of the program's own commands (`setup`, `local`).
pub fn run_command(
    program: &Path,
    command: &str,
    data_dir: &Path,
    args: &[String],
    stdin_line: Option<&str>,
) -> Result<Value, String> {
    let mut child = Command::new(program)
        .arg(command)
        .args(args)
        .env("OUTBOX_DATA_DIR", data_dir)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null()) // nothing from the child's error output reaches the window or a log
        .spawn()
        .map_err(|_| "Das Mail-Assistant-Programm lässt sich nicht starten.".to_string())?;
    if let Some(mut stdin) = child.stdin.take() {
        if let Some(line) = stdin_line {
            let _ = writeln!(stdin, "{line}");
        }
        // dropping `stdin` closes the pipe
    }
    let out = child
        .wait_with_output()
        .map_err(|_| "Das Mail-Assistant-Programm wurde unterbrochen.".to_string())?;
    parse_reply(&String::from_utf8_lossy(&out.stdout))
}

pub fn save_mailbox_args(email: &str, host: &str, port: u16, security: &str) -> Vec<String> {
    vec![
        "save-mailbox".into(),
        format!("--email={email}"),
        format!("--host={host}"),
        format!("--port={port}"),
        format!("--security={security}"),
    ]
}

pub fn send_test_args(to: &str, mark_own: bool) -> Vec<String> {
    let mut v = vec!["send-test".to_string(), format!("--to={to}")];
    if mark_own {
        v.push("--mark-own".into());
    }
    v
}

/// Only the local approval page's enrollment link may be opened from the window, nothing else.
pub fn is_enroll_link(url: &str) -> bool {
    let rest = match url.strip_prefix("http://localhost:") {
        Some(r) => r,
        None => return false,
    };
    let (port, path) = match rest.split_once('/') {
        Some(x) => x,
        None => return false,
    };
    let token = match path.strip_prefix("passkey/enroll/") {
        Some(t) => t,
        None => return false,
    };
    !port.is_empty()
        && port.len() <= 5
        && port.chars().all(|c| c.is_ascii_digit())
        && !token.is_empty()
        && token.len() <= 100
        && token.chars().all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_')
}

/// Opens the link in the computer's default browser.
pub fn open_in_browser(url: &str) -> Result<(), String> {
    if !is_enroll_link(url) {
        return Err("Dieser Link darf von hier aus nicht geöffnet werden.".into());
    }
    #[cfg(target_os = "macos")]
    let status = Command::new("open").arg(url).status();
    #[cfg(target_os = "windows")]
    let status = Command::new("rundll32").arg("url.dll,FileProtocolHandler").arg(url).status();
    #[cfg(not(any(target_os = "macos", target_os = "windows")))]
    let status = Command::new("xdg-open").arg(url).status();
    match status {
        Ok(s) if s.success() => Ok(()),
        _ => Err("Der Browser lässt sich nicht öffnen. Kopiere den Link und öffne ihn selbst.".into()),
    }
}

/// The admin application of the program, as one child process that stays open for the whole app session
/// (`<program> local api`): one JSON request per line in, one answer per line out. No port is opened.
pub struct ApiSession {
    child: Child,
    stdin: ChildStdin,
    stdout: BufReader<ChildStdout>,
}

impl ApiSession {
    pub fn start(program: &Path, data_dir: &Path) -> Result<Self, String> {
        let mut child = Command::new(program)
            .arg("local")
            .arg("api")
            .env("OUTBOX_DATA_DIR", data_dir)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()
            .map_err(|_| "Das Mail-Assistant-Programm lässt sich nicht starten.".to_string())?;
        let stdin = child.stdin.take().ok_or("Das Mail-Assistant-Programm lässt sich nicht starten.")?;
        let stdout = BufReader::new(child.stdout.take().ok_or("Das Mail-Assistant-Programm lässt sich nicht starten.")?);
        let mut session = ApiSession { child, stdin, stdout };
        let hello = session.read_line()?;
        match parse_reply(&hello) {
            Ok(v) if v.get("ready").and_then(Value::as_bool) == Some(true) => Ok(session),
            _ => Err("Die lokale Verwaltung ist nicht angesprungen.".into()),
        }
    }

    fn read_line(&mut self) -> Result<String, String> {
        let mut line = String::new();
        match self.stdout.read_line(&mut line) {
            Ok(n) if n > 0 => Ok(line),
            _ => Err("Die lokale Verwaltung antwortet nicht mehr.".into()),
        }
    }

    /// One request, one answer. An error here means the child is gone; the caller starts a new one.
    pub fn ask(&mut self, request: &Value) -> Result<Value, String> {
        let line = serde_json::to_string(request).map_err(|_| "Die Anfrage ist nicht lesbar.".to_string())?;
        writeln!(self.stdin, "{line}").map_err(|_| "Die lokale Verwaltung antwortet nicht mehr.".to_string())?;
        self.stdin.flush().map_err(|_| "Die lokale Verwaltung antwortet nicht mehr.".to_string())?;
        parse_reply(&self.read_line()?)
    }
}

impl Drop for ApiSession {
    fn drop(&mut self) {
        // Closing the pipe ends the program by itself; the kill only makes sure nothing stays behind.
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

/// The window may only ask for what the admin application serves: GET, POST or PUT below /admin/.
pub fn api_request(method: &str, path: &str, body: Option<Value>) -> Result<Value, String> {
    if !matches!(method, "GET" | "POST" | "PUT") {
        return Err("Diese Anfrage ist nicht erlaubt.".into());
    }
    if !path.starts_with("/admin/") || path.contains("..") || path.contains('\n') || path.len() > 2000 {
        return Err("Diese Anfrage ist nicht erlaubt.".into());
    }
    let route = path.split('?').next().unwrap_or("");
    if route == "/admin/login" || route == "/admin/logout" {
        return Err("Diese Anfrage ist nicht erlaubt.".into());
    }
    Ok(serde_json::json!({ "method": method, "path": path, "body": body }))
}

/// What the administration screen may ask the program to do. Every value travels as `--name=value`, so a value
/// that starts with a dash is still only a value. Anything else is refused here, before a process starts.
pub fn local_admin_args(
    action: &str,
    reference: Option<&str>,
    name: Option<&str>,
    address: Option<&str>,
    value: Option<&str>,
    own: bool,
) -> Result<Vec<String>, String> {
    let need = |v: Option<&str>| -> Result<String, String> {
        match v {
            Some(x) if !x.trim().is_empty() && x.len() <= 320 && !x.contains('\n') && !x.contains('\0') => Ok(x.to_string()),
            _ => Err("Es fehlt eine Angabe.".to_string()),
        }
    };
    let mut args = vec![action.to_string()];
    match action {
        "snapshot" => {}
        "approve" => {
            args.push(format!("--ref={}", need(reference)?));
            if let Some(n) = name.filter(|n| !n.trim().is_empty()) {
                args.push(format!("--name={}", need(Some(n))?));
            }
            if own {
                args.push("--own".into());
            }
        }
        "reject" | "block" | "unblock" | "remove" => args.push(format!("--ref={}", need(reference)?)),
        "scope" => {
            let v = need(value)?;
            if v != "own" && v != "external" {
                return Err("Unbekannte Angabe.".into());
            }
            args.push(format!("--ref={}", need(reference)?));
            args.push(format!("--value={v}"));
        }
        "add" => {
            args.push(format!("--name={}", need(name)?));
            args.push(format!("--address={}", need(address)?));
            if own {
                args.push("--own".into());
            }
        }
        "passkey-remove" => {
            let id = need(reference)?;
            if id.len() > 64 || !id.chars().all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_') {
                return Err("Unbekannte Angabe.".into());
            }
            args.push(format!("--id={id}"));
        }
        _ => return Err("Diese Aktion gibt es nicht.".into()),
    }
    Ok(args)
}

pub fn claude_args(action: &str, program: &Path) -> Vec<String> {
    vec![action.to_string(), format!("--command={}", program.display())]
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_the_enrollment_link_may_be_opened() {
        assert!(is_enroll_link("http://localhost:47821/passkey/enroll/abc-DEF_123"));
        for bad in [
            "https://localhost:47821/passkey/enroll/abc",
            "http://localhost:47821/other/abc",
            "http://localhost:47821/passkey/enroll/",
            "http://localhost:47821/passkey/enroll/a/b",
            "http://localhost.evil.com:1/passkey/enroll/abc",
            "http://localhost:4x/passkey/enroll/abc",
            "file:///etc/passwd",
            "http://localhost:47821/passkey/enroll/abc?x=1",
        ] {
            assert!(!is_enroll_link(bad), "{bad}");
        }
        assert!(open_in_browser("https://example.org").is_err());
    }

    #[test]
    fn reads_the_last_json_line_and_ignores_noise_before_it() {
        let v = parse_reply("some log\n{\"ok\": false, \"reason\": \"x\"}\n\n").unwrap();
        assert_eq!(v["ok"], false);
        assert_eq!(v["reason"], "x");
    }

    #[test]
    fn refuses_empty_and_non_object_replies() {
        assert!(parse_reply("").is_err());
        assert!(parse_reply("   \n").is_err());
        assert!(parse_reply("not json").is_err());
        assert!(parse_reply("[1,2]").is_err());
    }

    #[test]
    fn values_always_travel_as_name_equals_value() {
        let a = save_mailbox_args("--evil@example.org", "h.example.org", 587, "starttls");
        assert!(a.iter().all(|x| x == "save-mailbox" || x.starts_with("--") && x.contains('=')));
        assert!(a.contains(&"--email=--evil@example.org".to_string()));
        assert_eq!(send_test_args("a@b.de", true), vec!["send-test", "--to=a@b.de", "--mark-own"]);
        assert_eq!(send_test_args("a@b.de", false), vec!["send-test", "--to=a@b.de"]);
    }

    #[test]
    fn the_password_is_never_part_of_the_arguments() {
        let a = save_mailbox_args("me@example.org", "smtp.example.org", 587, "starttls");
        assert_eq!(a.len(), 5);
    }

    #[test]
    fn locate_needs_an_absolute_existing_file() {
        assert!(locate(None, None).is_err());
        assert!(locate(None, Some("relative/server".into())).is_err());
        assert!(locate(None, Some("/definitely/not/here".into())).is_err());
        let dir = std::env::temp_dir().join(format!("ma-setup-test-{}", std::process::id()));
        std::fs::create_dir_all(dir.join("server")).unwrap();
        std::fs::write(program_in(&dir), b"x").unwrap();
        assert_eq!(locate(Some(dir.clone()), None).unwrap(), program_in(&dir));
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[cfg(unix)]
    #[test]
    fn runs_a_child_sends_the_password_on_stdin_only_and_parses_the_answer() {
        use std::os::unix::fs::PermissionsExt;
        let dir = std::env::temp_dir().join(format!("ma-setup-run-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let script = dir.join("fake");
        // Echoes whether it got the password on stdin, and which arguments it saw.
        std::fs::write(
            &script,
            "#!/bin/sh\nread pw\nprintf '{\"ok\": true, \"got\": \"%s\", \"args\": \"%s\", \"dir\": \"%s\"}\\n' \"$pw\" \"$*\" \"$OUTBOX_DATA_DIR\"\n",
        )
        .unwrap();
        std::fs::set_permissions(&script, std::fs::Permissions::from_mode(0o755)).unwrap();
        let args = save_mailbox_args("me@example.org", "h.example.org", 587, "starttls");
        let v = run(&script, &dir, &args, Some("secret-pw")).unwrap();
        assert_eq!(v["got"], "secret-pw");
        assert!(!v["args"].as_str().unwrap().contains("secret-pw"));
        assert!(v["args"].as_str().unwrap().starts_with("setup save-mailbox"));
        assert_eq!(v["dir"], dir.to_str().unwrap());
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn administration_accepts_only_known_actions_and_keeps_values_as_values() {
        let a = local_admin_args("approve", Some("--own"), Some("Velo"), None, None, true).unwrap();
        assert_eq!(a, vec!["approve", "--ref=--own", "--name=Velo", "--own"]);
        assert_eq!(local_admin_args("snapshot", None, None, None, None, false).unwrap(), vec!["snapshot"]);
        assert_eq!(
            local_admin_args("scope", Some("a@b.de"), None, None, Some("own"), false).unwrap(),
            vec!["scope", "--ref=a@b.de", "--value=own"]
        );
        assert!(local_admin_args("scope", Some("a@b.de"), None, None, Some("alles"), false).is_err());
        assert!(local_admin_args("serve", None, None, None, None, false).is_err());
        assert!(local_admin_args("remove", None, None, None, None, false).is_err());
        assert!(local_admin_args("remove", Some("a\nb"), None, None, None, false).is_err());
        assert!(local_admin_args("add", None, Some("N"), None, None, false).is_err());
        assert!(local_admin_args("passkey-remove", Some("abc DEF"), None, None, None, false).is_err());
        assert_eq!(
            local_admin_args("passkey-remove", Some("AbC-12_x"), None, None, None, false).unwrap(),
            vec!["passkey-remove", "--id=AbC-12_x"]
        );
    }

    #[test]
    fn api_requests_are_limited_to_the_admin_routes() {
        assert!(api_request("GET", "/admin/recipients", None).is_ok());
        assert!(api_request("POST", "/admin/recipients/3/scope", Some(serde_json::json!({"scope":"own"}))).is_ok());
        assert!(api_request("GET", "/admin/sources/x/files?cursor=a%2Fb", None).is_ok());
        assert!(api_request("DELETE", "/admin/recipients", None).is_err());
        assert!(api_request("GET", "/etc/passwd", None).is_err());
        assert!(api_request("GET", "/admin/../x", None).is_err());
        assert!(api_request("POST", "/admin/login", None).is_err());
        assert!(api_request("POST", "/admin/logout?x=1", None).is_err());
    }

    #[test]
    fn one_child_answers_many_requests_line_by_line() {
        use std::os::unix::fs::PermissionsExt;
        let dir = std::env::temp_dir().join(format!("api-session-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let script = dir.join("fake");
        std::fs::write(
            &script,
            "#!/bin/sh\n[ \"$1 $2\" = \"local api\" ] || exit 3\necho '{\"ready\": true}'\nn=0\nwhile read line; do n=$((n+1)); echo \"{\\\"status\\\": 200, \\\"data\\\": {\\\"n\\\": $n}}\"; done\n",
        )
        .unwrap();
        std::fs::set_permissions(&script, std::fs::Permissions::from_mode(0o755)).unwrap();
        let mut s = ApiSession::start(&script, &dir).unwrap();
        let a = s.ask(&api_request("GET", "/admin/status", None).unwrap()).unwrap();
        let b = s.ask(&api_request("GET", "/admin/status", None).unwrap()).unwrap();
        assert_eq!(a["data"]["n"], 1);
        assert_eq!(b["data"]["n"], 2);
        drop(s);
        let _ = std::fs::remove_dir_all(&dir);
    }
}
