use serde::Serialize;
use sha2::{Digest, Sha256};
use std::fs::File;
use std::io::Read;
use std::path::Path;

/// Files above this size are refused; the server accepts at most 200 KB per file anyway.
const MAX_BYTES: u64 = 50 * 1024 * 1024;

#[derive(Serialize)]
struct FileHash {
    name: String,
    size: u64,
    sha256: String,
}

/// Reads one regular file in chunks and returns its SHA-256. Nothing is uploaded or stored.
#[tauri::command]
fn hash_file(path: String) -> Result<FileHash, String> {
    let p = Path::new(&path);
    let meta = std::fs::metadata(p).map_err(|e| e.to_string())?;
    if !meta.is_file() {
        return Err("Das ist keine einzelne Datei.".into());
    }
    if meta.len() > MAX_BYTES {
        return Err("Die Datei ist zu groß.".into());
    }
    let mut file = File::open(p).map_err(|e| e.to_string())?;
    let mut hasher = Sha256::new();
    let mut buf = [0u8; 64 * 1024];
    loop {
        let n = file.read(&mut buf).map_err(|e| e.to_string())?;
        if n == 0 {
            break;
        }
        hasher.update(&buf[..n]);
    }
    let name = p
        .file_name()
        .map(|n| n.to_string_lossy().into_owned())
        .unwrap_or_default();
    Ok(FileHash {
        name,
        size: meta.len(),
        sha256: format!("{:x}", hasher.finalize()),
    })
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .invoke_handler(tauri::generate_handler![hash_file])
        .run(tauri::generate_context!())
        .expect("error while running the application");
}
