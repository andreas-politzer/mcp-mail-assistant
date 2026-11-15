//! Everything the app does with files on this Mac.
//!
//! The rules of this module:
//! - Local paths live only here and in one small file in the app's data folder. They are never
//!   sent to the server, never written to the audit log and never handed to the user interface:
//!   the interface gets a display text and opaque ids.
//! - A folder is read completely or not at all. Any error while reading aborts the scan, so the
//!   server never receives a manifest with holes (the server removes copies that are missing from
//!   the manifest).
//! - Symbolic links are never followed. Only `.md` and `.txt` files are looked at, hidden files and
//!   folders are skipped.
//! - File contents are read again right before sending and must still match the scanned hash.

use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::HashMap;
use std::fs;
use std::io::{Read, Write};
use std::path::{Component, Path, PathBuf};

/// The server accepts at most this many files in one manifest.
pub const MAX_MANIFEST: usize = 2000;
/// The server accepts at most 200 KB per file; reading stops well above that.
const MAX_READ_BYTES: u64 = 1024 * 1024;
const MAX_DEPTH: usize = 16;
const STORE_FILE: &str = "sources.json";

// ---------------------------------------------------------------------------------------------
// Stored links between server sources and local places
// ---------------------------------------------------------------------------------------------

#[derive(Serialize, Deserialize, Clone, Debug, PartialEq)]
pub struct Link {
    pub server_id: String,
    /// "folder" or "file"
    pub kind: String,
    pub path: String,
}

#[derive(Serialize, Deserialize, Default, Debug)]
struct StoreFile {
    links: Vec<Link>,
}

fn store_path(dir: &Path) -> PathBuf {
    dir.join(STORE_FILE)
}

#[cfg(unix)]
fn make_private_dir(dir: &Path) -> Result<(), String> {
    use std::os::unix::fs::DirBuilderExt;
    fs::DirBuilder::new()
        .recursive(true)
        .mode(0o700)
        .create(dir)
        .map_err(|e| format!("Der App-Ordner konnte nicht angelegt werden: {e}"))
}

#[cfg(not(unix))]
fn make_private_dir(dir: &Path) -> Result<(), String> {
    fs::create_dir_all(dir).map_err(|e| format!("Der App-Ordner konnte nicht angelegt werden: {e}"))
}

#[cfg(unix)]
fn open_private(path: &Path) -> std::io::Result<fs::File> {
    use std::os::unix::fs::OpenOptionsExt;
    fs::OpenOptions::new().write(true).create(true).truncate(true).mode(0o600).open(path)
}

#[cfg(not(unix))]
fn open_private(path: &Path) -> std::io::Result<fs::File> {
    fs::OpenOptions::new().write(true).create(true).truncate(true).open(path)
}

pub fn load_links(dir: &Path) -> Result<Vec<Link>, String> {
    let p = store_path(dir);
    if !p.exists() {
        return Ok(Vec::new());
    }
    let text = fs::read_to_string(&p).map_err(|e| format!("Die lokale Quellenliste ist nicht lesbar: {e}"))?;
    let parsed: StoreFile =
        serde_json::from_str(&text).map_err(|_| "Die lokale Quellenliste ist beschädigt.".to_string())?;
    Ok(parsed.links)
}

pub fn save_links(dir: &Path, links: &[Link]) -> Result<(), String> {
    make_private_dir(dir)?;
    let text = serde_json::to_string_pretty(&StoreFile { links: links.to_vec() }).map_err(|e| e.to_string())?;
    let tmp = dir.join(format!("{STORE_FILE}.tmp"));
    {
        let mut f = open_private(&tmp).map_err(|e| format!("Speichern nicht möglich: {e}"))?;
        f.write_all(text.as_bytes()).map_err(|e| format!("Speichern nicht möglich: {e}"))?;
        f.sync_all().map_err(|e| format!("Speichern nicht möglich: {e}"))?;
    }
    fs::rename(&tmp, store_path(dir)).map_err(|e| format!("Speichern nicht möglich: {e}"))
}

// ---------------------------------------------------------------------------------------------
// Paths
// ---------------------------------------------------------------------------------------------

/// A path inside a source as the interface and the server name it: forward slashes, no `..`.
pub fn safe_rel(rel: &str) -> Result<PathBuf, String> {
    if rel.is_empty() {
        return Ok(PathBuf::new());
    }
    if rel.contains('\0') || rel.contains('\\') || rel.starts_with('/') {
        return Err("Ungültiger Pfad.".into());
    }
    let mut out = PathBuf::new();
    for part in rel.split('/') {
        if part.is_empty() || part == "." || part == ".." {
            return Err("Ungültiger Pfad.".into());
        }
        out.push(part);
    }
    for c in out.components() {
        if !matches!(c, Component::Normal(_)) {
            return Err("Ungültiger Pfad.".into());
        }
    }
    Ok(out)
}

fn is_text_name(name: &str) -> bool {
    let lower = name.to_lowercase();
    lower.ends_with(".md") || lower.ends_with(".txt")
}

fn home_short(path: &Path) -> String {
    if let Ok(home) = std::env::var("HOME") {
        if let Ok(rest) = path.strip_prefix(&home) {
            return format!("~/{}", rest.display());
        }
    }
    path.display().to_string()
}

/// What the interface may show about a link. It is shown on this Mac only.
pub fn display_of(link: &Link) -> String {
    home_short(Path::new(&link.path))
}

pub fn reachable(link: &Link) -> bool {
    match fs::symlink_metadata(&link.path) {
        Ok(m) if m.file_type().is_symlink() => false,
        Ok(m) => (link.kind == "folder" && m.is_dir()) || (link.kind == "file" && m.is_file()),
        Err(_) => false,
    }
}

// ---------------------------------------------------------------------------------------------
// Hashing and reading
// ---------------------------------------------------------------------------------------------

pub fn hash_bytes(data: &[u8]) -> String {
    format!("{:x}", Sha256::digest(data))
}

/// Reads a regular file (never a link) completely. Fails on any problem.
fn read_regular(path: &Path) -> Result<Vec<u8>, String> {
    let meta = fs::symlink_metadata(path).map_err(|e| format!("Nicht lesbar: {e}"))?;
    if meta.file_type().is_symlink() || !meta.is_file() {
        return Err("Keine gewöhnliche Datei.".into());
    }
    if meta.len() > MAX_READ_BYTES {
        return Err("Die Datei ist zu groß.".into());
    }
    let mut data = Vec::with_capacity(meta.len() as usize);
    fs::File::open(path)
        .and_then(|f| f.take(MAX_READ_BYTES + 1).read_to_end(&mut data))
        .map_err(|e| format!("Nicht lesbar: {e}"))?;
    if data.len() as u64 > MAX_READ_BYTES {
        return Err("Die Datei ist zu groß.".into());
    }
    Ok(data)
}

// ---------------------------------------------------------------------------------------------
// Scanning a source
// ---------------------------------------------------------------------------------------------

#[derive(Serialize, Clone, Debug)]
pub struct Entry {
    pub path: String,
    pub size: u64,
    pub sha256: String,
}

#[derive(Serialize, Clone, Debug)]
pub struct Skipped {
    pub path: String,
    pub reason: String,
}

pub struct Scan {
    pub base: PathBuf,
    pub single: bool,
    pub files: HashMap<String, String>,
}

pub struct ScanResult {
    pub entries: Vec<Entry>,
    pub skipped: Vec<Skipped>,
    pub scan: Scan,
}

fn walk(
    dir: &Path,
    rel: &str,
    depth: usize,
    out: &mut Vec<Entry>,
    skipped: &mut Vec<Skipped>,
) -> Result<(), String> {
    if depth > MAX_DEPTH {
        return Err("Die Ordner sind zu tief verschachtelt. Der Abgleich wurde abgebrochen.".into());
    }
    let reader = fs::read_dir(dir).map_err(|e| format!("Ein Ordner ist nicht lesbar ({e}). Der Abgleich wurde abgebrochen."))?;
    let mut items = Vec::new();
    for item in reader {
        items.push(item.map_err(|e| format!("Ein Ordner ist nicht vollständig lesbar ({e}). Der Abgleich wurde abgebrochen."))?);
    }
    items.sort_by_key(|i| i.file_name());
    for item in items {
        let name = match item.file_name().into_string() {
            Ok(n) => n,
            Err(_) => {
                skipped.push(Skipped { path: format!("{rel}{}", item.file_name().to_string_lossy()), reason: "Name nicht lesbar".into() });
                continue;
            }
        };
        if name.starts_with('.') {
            continue;
        }
        let path = item.path();
        let meta = fs::symlink_metadata(&path).map_err(|e| format!("'{name}' ist nicht lesbar ({e}). Der Abgleich wurde abgebrochen."))?;
        let shown = format!("{rel}{name}");
        if meta.file_type().is_symlink() {
            skipped.push(Skipped { path: shown, reason: "Verknüpfung, wird nicht gefolgt".into() });
        } else if meta.is_dir() {
            walk(&path, &format!("{shown}/"), depth + 1, out, skipped)?;
        } else if meta.is_file() && is_text_name(&name) {
            let data = read_regular(&path).map_err(|e| format!("'{shown}': {e} Der Abgleich wurde abgebrochen."))?;
            out.push(Entry { path: shown, size: data.len() as u64, sha256: hash_bytes(&data) });
            if out.len() > MAX_MANIFEST {
                return Err(format!("Mehr als {MAX_MANIFEST} Dateien. Der Abgleich wurde abgebrochen."));
            }
        }
    }
    Ok(())
}

/// Reads the whole source. Any error returns Err and no partial result.
pub fn scan_link(link: &Link) -> Result<ScanResult, String> {
    if !reachable(link) {
        return Err("Die Quelle ist auf diesem Mac nicht erreichbar. Es wurde nichts verändert.".into());
    }
    let base = PathBuf::from(&link.path);
    let mut entries = Vec::new();
    let mut skipped = Vec::new();
    let single = link.kind == "file";
    if single {
        let name = base.file_name().and_then(|n| n.to_str()).ok_or("Der Dateiname ist nicht lesbar.")?.to_string();
        let data = read_regular(&base).map_err(|e| format!("'{name}': {e} Der Abgleich wurde abgebrochen."))?;
        entries.push(Entry { path: name, size: data.len() as u64, sha256: hash_bytes(&data) });
    } else {
        walk(&base, "", 0, &mut entries, &mut skipped)?;
    }
    // The folder must still be what it was when the scan began.
    if !reachable(link) {
        return Err("Die Quelle ist während des Lesens verschwunden. Es wurde nichts verändert.".into());
    }
    entries.sort_by(|a, b| a.path.cmp(&b.path));
    let files = entries.iter().map(|e| (e.path.clone(), e.sha256.clone())).collect();
    Ok(ScanResult { entries, skipped, scan: Scan { base, single, files } })
}

#[derive(Serialize, Debug)]
pub struct SentFile {
    pub path: String,
    pub sha256: String,
    pub content: String,
}

/// Reads files of an earlier scan again. Every file must still match its scanned hash and be text.
pub fn read_scanned(scan: &Scan, paths: &[String]) -> Result<Vec<SentFile>, String> {
    let mut out = Vec::new();
    for p in paths {
        let want = scan.files.get(p).ok_or("Diese Datei gehörte nicht zum Lesen vorhin.")?;
        let full = if scan.single { scan.base.clone() } else { scan.base.join(safe_rel(p)?) };
        let data = read_regular(&full).map_err(|e| format!("'{p}': {e}"))?;
        if &hash_bytes(&data) != want {
            return Err(format!("'{p}' hat sich seit dem Lesen geändert. Bitte neu abgleichen."));
        }
        let content = String::from_utf8(data).map_err(|_| format!("'{p}' ist kein gültiger Text."))?;
        out.push(SentFile { path: p.clone(), sha256: want.clone(), content });
    }
    Ok(out)
}

// ---------------------------------------------------------------------------------------------
// One level of a folder, for the tree
// ---------------------------------------------------------------------------------------------

#[derive(Serialize, Debug)]
pub struct TreeItem {
    pub name: String,
    pub kind: String,
    pub size: Option<u64>,
}

pub fn list_level(link: &Link, rel: &str) -> Result<Vec<TreeItem>, String> {
    if link.kind != "folder" || !reachable(link) {
        return Err("Die Quelle ist auf diesem Mac nicht erreichbar.".into());
    }
    let dir = Path::new(&link.path).join(safe_rel(rel)?);
    let canon_base = fs::canonicalize(&link.path).map_err(|e| e.to_string())?;
    let canon_dir = fs::canonicalize(&dir).map_err(|_| "Der Ordner ist nicht lesbar.".to_string())?;
    if !canon_dir.starts_with(&canon_base) {
        return Err("Ungültiger Pfad.".into());
    }
    let mut out = Vec::new();
    let reader = fs::read_dir(&canon_dir).map_err(|e| format!("Der Ordner ist nicht lesbar ({e})."))?;
    for item in reader {
        let item = item.map_err(|e| format!("Der Ordner ist nicht lesbar ({e})."))?;
        let Ok(name) = item.file_name().into_string() else { continue };
        if name.starts_with('.') {
            continue;
        }
        let Ok(meta) = fs::symlink_metadata(item.path()) else { continue };
        if meta.file_type().is_symlink() {
            continue;
        }
        if meta.is_dir() {
            out.push(TreeItem { name, kind: "dir".into(), size: None });
        } else if meta.is_file() && is_text_name(&name) {
            out.push(TreeItem { name, kind: "file".into(), size: Some(meta.len()) });
        }
    }
    out.sort_by(|a, b| (a.kind != "dir", a.name.to_lowercase()).cmp(&(b.kind != "dir", b.name.to_lowercase())));
    Ok(out)
}

/// Short name of a picked place, shown while the person names the source.
pub fn base_name(path: &Path) -> String {
    path.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_else(|| "Ordner".into())
}

// ---------------------------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    fn temp(name: &str) -> PathBuf {
        let p = std::env::temp_dir().join(format!("mail-assistant-test-{}-{name}", std::process::id()));
        let _ = fs::remove_dir_all(&p);
        fs::create_dir_all(&p).unwrap();
        p
    }

    fn folder_link(p: &Path) -> Link {
        Link { server_id: "s1".into(), kind: "folder".into(), path: p.display().to_string() }
    }

    #[test]
    fn relative_paths_cannot_escape() {
        assert!(safe_rel("a/b.md").is_ok());
        assert!(safe_rel("").is_ok());
        for bad in ["../x", "a/../b", "/abs", "a//b", "a\\b", "./a", "a/\0"] {
            assert!(safe_rel(bad).is_err(), "{bad}");
        }
    }

    #[test]
    fn hash_is_sha256_hex() {
        assert_eq!(hash_bytes(b"abc"), "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
    }

    #[test]
    fn scan_takes_text_files_only_and_skips_links_and_hidden() {
        let root = temp("scan");
        fs::create_dir_all(root.join("sub")).unwrap();
        fs::create_dir_all(root.join(".git")).unwrap();
        fs::write(root.join("a.md"), "hello").unwrap();
        fs::write(root.join("sub/b.TXT"), "world").unwrap();
        fs::write(root.join("c.pdf"), "x").unwrap();
        fs::write(root.join(".secret.md"), "x").unwrap();
        fs::write(root.join(".git/d.md"), "x").unwrap();
        #[cfg(unix)]
        std::os::unix::fs::symlink(root.join("a.md"), root.join("link.md")).unwrap();
        let r = scan_link(&folder_link(&root)).unwrap();
        let paths: Vec<_> = r.entries.iter().map(|e| e.path.as_str()).collect();
        assert_eq!(paths, vec!["a.md", "sub/b.TXT"]);
        #[cfg(unix)]
        assert_eq!(r.skipped.len(), 1);
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn a_missing_folder_aborts_instead_of_returning_an_empty_list() {
        let root = temp("gone");
        let link = folder_link(&root);
        fs::remove_dir_all(&root).unwrap();
        assert!(scan_link(&link).is_err());
    }

    #[cfg(unix)]
    #[test]
    fn an_unreadable_subfolder_aborts_the_whole_scan() {
        use std::os::unix::fs::PermissionsExt;
        let root = temp("locked");
        fs::create_dir_all(root.join("locked")).unwrap();
        fs::write(root.join("locked/x.md"), "x").unwrap();
        fs::write(root.join("a.md"), "x").unwrap();
        fs::set_permissions(root.join("locked"), fs::Permissions::from_mode(0o000)).unwrap();
        // Root can read everything; then there is nothing to test.
        let locked_for_us = fs::read_dir(root.join("locked")).is_err();
        let result = scan_link(&folder_link(&root));
        fs::set_permissions(root.join("locked"), fs::Permissions::from_mode(0o755)).unwrap();
        if locked_for_us {
            assert!(result.is_err());
        }
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn a_changed_file_is_refused_when_it_is_read_again() {
        let root = temp("changed");
        fs::write(root.join("a.md"), "one").unwrap();
        let r = scan_link(&folder_link(&root)).unwrap();
        assert_eq!(read_scanned(&r.scan, &["a.md".into()]).unwrap()[0].content, "one");
        fs::write(root.join("a.md"), "two").unwrap();
        assert!(read_scanned(&r.scan, &["a.md".into()]).is_err());
        assert!(read_scanned(&r.scan, &["other.md".into()]).is_err());
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn a_single_file_source_lists_its_one_file() {
        let root = temp("single");
        fs::write(root.join("Plan.md"), "x").unwrap();
        let link = Link { server_id: "s".into(), kind: "file".into(), path: root.join("Plan.md").display().to_string() };
        let r = scan_link(&link).unwrap();
        assert_eq!(r.entries.len(), 1);
        assert_eq!(r.entries[0].path, "Plan.md");
        let _ = fs::remove_dir_all(&root);
    }

    #[cfg(unix)]
    #[test]
    fn the_link_file_is_private() {
        use std::os::unix::fs::PermissionsExt;
        let dir = temp("store").join("app");
        let links = vec![Link { server_id: "s".into(), kind: "folder".into(), path: "/x".into() }];
        save_links(&dir, &links).unwrap();
        assert_eq!(load_links(&dir).unwrap(), links);
        let file_mode = fs::metadata(dir.join(STORE_FILE)).unwrap().permissions().mode() & 0o777;
        let dir_mode = fs::metadata(&dir).unwrap().permissions().mode() & 0o777;
        assert_eq!(file_mode, 0o600);
        assert_eq!(dir_mode, 0o700);
        let _ = fs::remove_dir_all(dir.parent().unwrap());
    }
}
