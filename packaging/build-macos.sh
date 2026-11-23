#!/usr/bin/env bash
# Baut die macOS-App "Mail Assistant" für den Chip DIESES Macs (Apple Silicon oder Intel, kein Universal-Paket):
#   1. Server-Programm mit PyInstaller bauen (Ordner packaging/dist/mail-assistant-server)
#   2. Es prüfen (packaging/check-macos.sh)
#   3. In die App-Ressourcen legen und mit "tauri build" die .app und die .dmg bauen
#   4. Das Programm IN der fertigen .app noch einmal prüfen
# Aufruf aus dem Repo-Ordner:  bash packaging/build-macos.sh
# Läuft nur auf einem Mac. Ändert nichts außerhalb des Repos. Committet nichts.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
[ "$(uname -s)" = "Darwin" ] || { echo "Dieses Skript baut die macOS-App und läuft nur auf einem Mac."; exit 1; }
echo "== Chip dieses Macs: $(uname -m)"

echo "== 1. Server-Programm mit PyInstaller bauen"
rm -rf packaging/build packaging/dist
uv run --with pyinstaller pyinstaller --noconfirm --clean --onedir --name mail-assistant-server \
  --distpath packaging/dist --workpath packaging/build --specpath packaging/build \
  --paths "$ROOT/src" \
  --collect-submodules outbox_mcp --collect-submodules mcp --collect-submodules uvicorn \
  --collect-submodules webauthn --collect-submodules keyring.backends \
  --copy-metadata mcp-mail-assistant --copy-metadata mcp --copy-metadata keyring \
  "$ROOT/packaging/entry.py"
SERVER="$ROOT/packaging/dist/mail-assistant-server/mail-assistant-server"

echo "== 2. Das gebaute Server-Programm prüfen"
bash packaging/check-macos.sh "$SERVER"

echo "== 3. In die App legen und bauen (das dauert beim ersten Mal einige Minuten)"
RES="$ROOT/frontend/src-tauri/resources/server"
rm -rf "$RES"; mkdir -p "$RES"
cp -R packaging/dist/mail-assistant-server/. "$RES/"
( cd frontend && npx tauri build --config src-tauri/tauri.bundle.conf.json )

echo "== 4. Das Programm in der fertigen App prüfen"
APP="$(find frontend/src-tauri/target/release/bundle/macos -maxdepth 1 -name '*.app' | head -1)"
[ -n "$APP" ] || { echo "FEHLER: keine .app gefunden"; exit 1; }
bash packaging/check-macos.sh "$ROOT/$APP/Contents/Resources/server/mail-assistant-server"

echo
echo "== FERTIG"
echo "App: $ROOT/$APP"
ls -1 "$ROOT"/frontend/src-tauri/target/release/bundle/dmg/*.dmg 2>/dev/null | sed 's/^/DMG: /' || true
