#!/usr/bin/env bash
# Installiert die frisch gebaute App "Mail Assistant" in den Programme-Ordner und startet sie:
#   1. Läuft die App noch, wird sie beendet
#   2. Die gebaute .app ersetzt die alte in /Applications
#   3. Der Handy-Zugang (falls eingerichtet) wird neu gestartet, damit er die neue Version nutzt
#   4. Die App wird geöffnet
# Aufruf aus dem Repo-Ordner:  bash packaging/install-macos.sh
# Läuft nur auf einem Mac. Ändert nichts im Repo. Committet nichts.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
[ "$(uname -s)" = "Darwin" ] || { echo "Dieses Skript läuft nur auf einem Mac."; exit 1; }
APP="$(find frontend/src-tauri/target/release/bundle/macos -maxdepth 1 -name '*.app' 2>/dev/null | head -1)"
[ -n "$APP" ] || { echo "FEHLER: keine gebaute App gefunden. Zuerst: bash packaging/build-macos.sh"; exit 1; }
NAME="$(basename "$APP")"
DEST="/Applications/$NAME"

echo "== 1. App beenden, falls sie läuft"
osascript -e "tell application \"${NAME%.app}\" to quit" >/dev/null 2>&1 || true
sleep 1

echo "== 2. In den Programme-Ordner kopieren"
rm -rf "$DEST"
ditto "$ROOT/$APP" "$DEST"

echo "== 3. Handy-Zugang neu starten (falls eingerichtet)"
if launchctl print "gui/$(id -u)/de.politzer.mailassistant.remote" >/dev/null 2>&1; then
  launchctl kickstart -k "gui/$(id -u)/de.politzer.mailassistant.remote" && echo "neu gestartet"
else
  echo "nicht eingerichtet, nichts zu tun"
fi

echo "== 4. App öffnen"
open "$DEST"
echo "== FERTIG: $DEST"
