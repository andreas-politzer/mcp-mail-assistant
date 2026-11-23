#!/usr/bin/env bash
# Prüft ein gebautes Server-Programm so, wie die App es benutzt:
#   version, setup status, local api (Start und eine echte Anfrage), stdio (Handschlag mit Claude Desktop),
#   admin und serve (alle Module müssen sich laden lassen).
# Aufruf:  bash packaging/check-macos.sh /voller/Pfad/zu/mail-assistant-server
# Läuft gegen den PyInstaller-Ordner und, genauso, gegen das Programm in der fertigen .app.
# Rührt weder den echten Schlüsselbund noch die echte Konfiguration von Claude Desktop an.
set -uo pipefail
SERVER="${1:-}"
fail(){ echo "  FEHLER: $1"; FAILED=1; }
ok(){ echo "  ok: $1"; }
FAILED=0
[ -n "$SERVER" ] || { echo "Aufruf: bash packaging/check-macos.sh /voller/Pfad/zu/mail-assistant-server"; exit 2; }
case "$SERVER" in /*) ;; *) SERVER="$PWD/$SERVER" ;; esac
echo "== Prüfe $SERVER"
[ -f "$SERVER" ] || { echo "  FEHLER: Datei fehlt"; exit 1; }
[ -x "$SERVER" ] || { echo "  FEHLER: Datei ist nicht ausführbar (Ausführungsrecht fehlt)"; exit 1; }
if [ -d "$(dirname "$SERVER")/_internal" ] || [ -d "$(dirname "$SERVER")/../Frameworks" ]; then ok "Begleitordner vorhanden"; fi
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
mkdir -p "$T/data" "$T/home"
# Alles, was nach außen greifen könnte, zeigt in den Wegwerf-Ordner.
export HOME="$T/home" OUTBOX_DATA_DIR="$T/data" CLAUDE_DESKTOP_CONFIG="$T/claude.json" OUTBOX_KEYCHAIN_BACKEND=keyring
limited(){ perl -e 'alarm shift; exec @ARGV' "$@"; }   # macOS hat kein "timeout"

echo "-- 1. version"
V="$(limited 60 "$SERVER" version 2>&1)"; RC=$?
if [ $RC -eq 0 ] && [ -n "$V" ] && [ "$V" != "unknown" ]; then ok "Version $V"; else fail "version liefert '$V' (Ausgang $RC)"; fi

echo "-- 2. setup status"
S="$(limited 60 "$SERVER" setup status 2>&1 | tail -1)"
case "$S" in *'"ok": true'*) ok "setup status antwortet";; *) fail "setup status: $S";; esac

echo "-- 3. local api (Start und eine Anfrage)"
A="$(printf '{"method":"GET","path":"/admin/recipients"}\n' | limited 60 "$SERVER" local api 2>&1)"
case "$A" in *'"ready": true'*) ok "local api meldet ready";; *) fail "local api meldet nicht ready: $(echo "$A" | head -3)";; esac
case "$A" in *'"status": 200'*) ok "local api beantwortet eine Anfrage";; *) fail "local api antwortet nicht mit 200: $(echo "$A" | tail -2)";; esac

echo "-- 4. stdio (Handschlag wie mit Claude Desktop)"
printf '{"email":"pruefung@example.org","host":"smtp.example.org","port":587,"security":"starttls"}' > "$T/data/mailbox.json"
H="$(printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"pruefung","version":"0"}}}' \
  | OUTBOX_SMTP_PASSWORD=pruefung OUTBOX_EXIT_WHEN_STDIN_CLOSES=1 limited 60 "$SERVER" stdio 2>&1)"
case "$H" in *'"protocolVersion"'*'"serverInfo"'*) ok "stdio antwortet auf initialize";; *) fail "stdio: $(echo "$H" | tail -3)";; esac

echo "-- 5. admin und serve laden alle Module"
AD="$(limited 60 "$SERVER" admin --help 2>&1)"; RC=$?
if [ $RC -eq 0 ]; then ok "admin --help"; else fail "admin --help (Ausgang $RC): $(echo "$AD" | tail -3)"; fi
SV="$(env -i PATH="$PATH" HOME="$T/home" OUTBOX_DATA_DIR="$T/data" perl -e 'alarm shift; exec @ARGV' 60 "$SERVER" serve 2>&1)"
if echo "$SV" | grep -qE "ModuleNotFoundError|ImportError|No module named"; then fail "serve: Modul fehlt: $(echo "$SV" | tail -3)";
elif echo "$SV" | grep -q "Missing required setting"; then ok "serve lädt (und verlangt erwartungsgemäß seine Einstellungen)"; else fail "serve: unerwartete Ausgabe: $(echo "$SV" | tail -3)"; fi

echo
if [ $FAILED -eq 0 ]; then echo "== ALLES IN ORDNUNG: $SERVER"; else echo "== PROBLEME GEFUNDEN: $SERVER"; fi
exit $FAILED
