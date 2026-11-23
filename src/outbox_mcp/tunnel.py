"""Phone use, step 2: the tunnel. Tailscale Funnel is the one supported way.

This module only checks Tailscale, shows the public address before anything changes, switches the Funnel on
for the server's port and looks at the result. It installs nothing and signs in to nothing. The Funnel stays
with Tailscale itself (`funnel --bg`); no second background service and no tunnel secret is kept here.

The Funnel only ever points at the public server port on this computer (ADR 0003), never at anything
administrative. A rule somebody else has set up on the same HTTPS port is never overwritten.

Messages in `reason` fields are shown to the person in the wizard (German); the output of tailscale is
only appended as a short "Details:". Everything else is English.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable, Mapping, Optional, Sequence, Tuple

from . import remote
from .setup_tasks import SetupError

# A program started from the Finder has almost no PATH, so the usual places are looked at one by one.
KNOWN_PATHS = (
    "/Applications/Tailscale.app/Contents/MacOS/Tailscale",
    "/opt/homebrew/bin/tailscale",
    "/usr/local/bin/tailscale",
)
HTTPS_PORT = 443
_ADMIN_URL = re.compile(r"https://login\.tailscale\.com/[^\s\"'<>]+")
_OURS = {f"http://127.0.0.1:{remote.PORT}", f"http://localhost:{remote.PORT}", f"http://[::1]:{remote.PORT}"}
_OURS |= {u.replace("http://", "") for u in set(_OURS)}


class NeedsAdmin(SetupError):
    """Funnel (or HTTPS) has to be switched on by the person in Tailscale's admin pages first."""

    def __init__(self, message: str, url: Optional[str] = None):
        super().__init__(message)
        self.url = url


@dataclass(frozen=True)
class Result:
    code: int
    out: str = ""
    err: str = ""
    timed_out: bool = False


Runner = Callable[[list, float], Result]
Fetcher = Callable[[str, str, float, Optional[bytes]], Tuple[int, bytes]]


def _text(value) -> str:
    if value is None:
        return ""
    return value.decode("utf-8", "replace") if isinstance(value, bytes) else str(value)


def _cli_env(base: Optional[Mapping[str, str]] = None) -> dict:
    """The environment for the tailscale program. On the Mac it is the app itself, and it only acts as a
    command line tool when TERM (or TERM_PROGRAM) is set. A program started from the Finder has neither,
    and Tailscale then tries to open its window and fails ("The Tailscale GUI failed to start")."""
    env = dict(os.environ if base is None else base)
    if not (env.get("TERM") or "").strip():
        env["TERM"] = "dumb"
    return env


def _run(args: list, timeout: float) -> Result:
    try:
        done = subprocess.run(
            args, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL, env=_cli_env()
        )
    except subprocess.TimeoutExpired as exc:
        return Result(-1, _text(exc.stdout), _text(exc.stderr), True)
    except OSError:
        return Result(127)
    return Result(done.returncode, done.stdout or "", done.stderr or "")


def _json(text: str) -> Optional[dict]:
    try:
        data = json.loads(text)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _details(*texts: str) -> str:
    line = next((" ".join(t.split()) for t in texts if t and t.strip()), "")
    return f" Details: {line[:160]}" if line else ""


def _require_mac(platform: Optional[str]) -> None:
    if (platform or sys.platform) != "darwin":
        raise SetupError("Der Handy-Betrieb ist in dieser Version nur für den Mac eingerichtet.")


def find_tailscale(
    *, which: Callable[[str], Optional[str]] = shutil.which, paths: Sequence[str] = KNOWN_PATHS
) -> Optional[str]:
    """The tailscale program: on the PATH, or in one of the places the installers use."""
    found = which("tailscale")
    for candidate in ([found] if found else []) + list(paths):
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


# ---------------------------------------------------------------- what Tailscale says

_STATE_TEXT = {
    "missing": "Tailscale ist auf diesem Mac nicht installiert. Installiere es von tailscale.com/download und melde dich dort an.",
    "stopped": "Tailscale ist ausgeschaltet. Schalte es in der Menüleiste ein.",
    "needs_login": "Du bist bei Tailscale nicht angemeldet. Öffne die Tailscale-App und melde dich an.",
    "needs_approval": "Dieser Mac muss in der Tailscale-Verwaltung noch freigegeben werden.",
    "not_running": "Tailscale läuft nicht. Starte die Tailscale-App und melde dich an.",
    "unreadable": "Tailscale konnte nicht abgefragt werden.",
    "no_magicdns": (
        "Für den Tunnel braucht dein Tailscale-Netz MagicDNS und HTTPS-Zertifikate. "
        "Beides schaltest du in der Tailscale-Verwaltung unter „DNS“ ein."
    ),
    "funnel_busy": (
        "Auf diesem Mac nutzt schon etwas anderes die HTTPS-Freigabe von Tailscale (Serve oder Funnel). "
        "Mail Assistant überschreibt das nicht. Beende die andere Freigabe oder richte den Tunnel selbst ein."
    ),
    "funnel_unknown": (
        "Der Zustand der Tailscale-Freigaben ließ sich nicht lesen. Aus Vorsicht wird nichts verändert."
    ),
}
# What Tailscale says when its background service is simply not there. Anything else that is not JSON is
# "unreadable": we do not know what is wrong, and the person is told that instead of a wrong cause.
_DOWN = ("is tailscale running", "failed to connect to local", "no such file or directory", "connection refused")
_BACKEND = {
    "Stopped": "stopped",
    "NeedsLogin": "needs_login",
    "NoState": "needs_login",
    "NeedsMachineAuth": "needs_approval",
}


def inspect(run: Runner, tailscale: str) -> dict:
    """Tailscale's own state and, when it is ready, the public address. Changes nothing."""
    done = run([tailscale, "status", "--json"], 10.0)
    data = _json(done.out)
    if data is None:
        said = f"{done.err}\n{done.out}".lower()
        if not done.timed_out and any(phrase in said for phrase in _DOWN):
            return {"state": "not_running", "address": None}
        detail = "keine Antwort innerhalb von 10 Sekunden" if done.timed_out else _details(done.err, done.out)
        return {"state": "unreadable", "address": None, "details": detail.removeprefix(" Details: ")}
    backend = data.get("BackendState")
    if backend != "Running":
        return {"state": _BACKEND.get(backend, "not_running"), "address": None}
    me = data.get("Self")
    name = str(me.get("DNSName") or "") if isinstance(me, dict) else ""
    name = name.strip().rstrip(".").lower()  # Tailscale writes the name with a dot at the end
    if not name.endswith(".ts.net"):
        return {"state": "no_magicdns", "address": None}
    try:
        return {"state": "ready", "address": remote.clean_address(f"https://{name}")}
    except SetupError:
        return {"state": "no_magicdns", "address": None}


def _normal(proxy) -> str:
    return str(proxy or "").strip().lower().rstrip("/")


def classify(config, host: str) -> str:
    """What the Funnel/Serve configuration means for us.

    off: nothing on the HTTPS port. ours: our port is public. ours_private: our port is served inside the
    tailnet only. other: somebody else's rule (never touched). unknown: not understood (never touched)."""
    if not isinstance(config, dict):
        return "unknown"
    try:
        web = config.get("Web") or {}
        tcp = config.get("TCP") or {}
        allow = config.get("AllowFunnel") or {}
        if config.get("Foreground"):
            return "other"
        key = f"{host}:{HTTPS_PORT}"
        if any(k != key and str(k).endswith(f":{HTTPS_PORT}") for k in web):
            return "other"
        if str(HTTPS_PORT) not in tcp and key not in web:
            return "off"
        if not isinstance(tcp.get(str(HTTPS_PORT)), dict) or not tcp[str(HTTPS_PORT)].get("HTTPS"):
            return "other"
        handlers = (web.get(key) or {}).get("Handlers") or {}
        if set(handlers) != {"/"} or _normal(handlers["/"].get("Proxy")) not in _OURS:
            return "other"
        return "ours" if allow.get(key) else "ours_private"
    except (AttributeError, TypeError):
        return "unknown"


def funnel_state(run: Runner, tailscale: str, host: str) -> str:
    """Reads the configuration; tries `funnel status` and, for older versions, `serve status`."""
    for verb in ("funnel", "serve"):
        done = run([tailscale, verb, "status", "--json"], 10.0)
        config = _json(done.out) if done.code == 0 else None
        if config is not None:
            return classify(config, host)
    return "unknown"


def _reason(state: str, details: str = "") -> str:
    text = _STATE_TEXT[state]
    return f"{text} Details: {details[:160]}" if details else text


def status(*, run: Optional[Runner] = None, tailscale: Optional[str] = None, platform: Optional[str] = None) -> dict:
    """Everything the tunnel card shows before the person decides. Changes nothing."""
    run = run or _run
    if (platform or sys.platform) != "darwin":
        return {"supported": False, "state": "unsupported", "address": None, "funnel": None, "reason": None}
    tailscale = tailscale or find_tailscale()
    if tailscale is None:
        return _answer("missing", None, None)
    info = inspect(run, tailscale)
    if info["state"] != "ready":
        return _answer(info["state"], None, None, info.get("details", ""))
    funnel = funnel_state(run, tailscale, info["address"].split("//", 1)[1])
    state = {"other": "funnel_busy", "unknown": "funnel_unknown"}.get(funnel, "ready")
    return _answer(state, info["address"] if state == "ready" else None, funnel)


def _answer(state: str, address: Optional[str], funnel: Optional[str], details: str = "") -> dict:
    return {
        "supported": True,
        "state": state,
        "address": address,
        "funnel": funnel,
        "reason": None if state == "ready" else _reason(state, details),
    }


# ---------------------------------------------------------------- switching the Funnel on

def start_funnel(run: Runner, tailscale: str) -> None:
    """`tailscale funnel --bg <port>`: Tailscale keeps the rule itself, also across restarts of the Mac."""
    done = run([tailscale, "funnel", "--bg", str(remote.PORT)], 25.0)
    both = f"{done.out}\n{done.err}"
    link = _ADMIN_URL.search(both)
    if link:
        raise NeedsAdmin(
            "Funnel ist in deinem Tailscale-Netz noch nicht freigeschaltet. Öffne die Seite, schalte es dort ein "
            "und klicke danach auf „Erneut prüfen“.",
            link.group(0).rstrip(".,)"),
        )
    if done.timed_out:
        raise SetupError("Tailscale hat nicht rechtzeitig geantwortet. Klicke gleich noch einmal auf „Erneut prüfen“.")
    if done.code != 0:
        raise SetupError("Tailscale konnte den Funnel nicht einschalten." + _details(done.err, done.out))


# ---------------------------------------------------------------- looking at the result

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _fetch(method: str, url: str, timeout: float, body: Optional[bytes]) -> Tuple[int, bytes]:
    request = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers={
            "User-Agent": "mail-assistant-setup",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
    )
    try:
        with urllib.request.build_opener(_NoRedirect).open(request, timeout=timeout) as reply:
            return reply.status, reply.read(65536)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(65536)


def check_address(
    address: str,
    *,
    fetch: Fetcher = _fetch,
    sleep: Callable[[float], None] = time.sleep,
    attempts: int = 8,
    delay: float = 2.5,
) -> None:
    """The server answers under the public address: POST /mcp gives 401 and the OAuth description names
    that address. The first certificate takes Tailscale 5 to 20 seconds, so a miss is tried again.

    This runs on this computer, through the name. It proves service, certificate and name; whether the
    phone gets through from outside is shown by the next step."""
    from .auth.web import METADATA_PATH  # here, not at the top: the setup commands should start fast

    last = "unreachable"
    for attempt in range(attempts):
        try:
            code, _ = fetch("POST", f"{address}/mcp", 10.0, b"{}")
            if code == 401:
                code, body = fetch("GET", f"{address}{METADATA_PATH}", 10.0, None)
                described = (_json(body.decode("utf-8", "replace")) or {}).get("issuer") if code == 200 else None
                if isinstance(described, str) and described.rstrip("/") == address.rstrip("/"):
                    return
                if code == 200:
                    last = "wrong_answer"
                    break  # an answer that will not change by waiting
                last = "gateway"
            else:
                last = "gateway"
        except OSError:
            last = "unreachable"
        if attempt + 1 < attempts:
            sleep(delay)
    if last == "wrong_answer":
        raise SetupError(
            "Die Adresse antwortet, aber nicht mit den Angaben von Mail Assistant. "
            "Läuft unter dieser Adresse noch etwas anderes?"
        )
    raise SetupError(
        "Die Adresse ist noch nicht erreichbar. Beim ersten Mal holt Tailscale ein Zertifikat, das kann ein bis zwei "
        "Minuten dauern. Klicke gleich noch einmal auf „Erneut prüfen“."
    )


# ---------------------------------------------------------------- the whole step

def enable(
    env: Mapping[str, str],
    command: str,
    address: str,
    *,
    run: Optional[Runner] = None,
    launchctl: Optional[remote.Runner] = None,
    uid: Optional[int] = None,
    tailscale: Optional[str] = None,
    fetch: Fetcher = _fetch,
    sleep: Callable[[float], None] = time.sleep,
    platform: Optional[str] = None,
) -> dict:
    """The order matters: the service must run before the Funnel is looked at, and it needs a passphrase hash
    to start. So: Tailscale is checked, the confirmed address must still be the real one, the service starts
    (with a throw-away passphrase if the person has none yet), the Funnel is switched on, the address is tried.
    Asking again is safe: what is already in place is left alone."""
    run = run or _run
    _require_mac(platform)
    tailscale = tailscale or find_tailscale()
    if tailscale is None:
        raise SetupError(_STATE_TEXT["missing"])
    info = inspect(run, tailscale)
    if info["state"] != "ready":
        raise SetupError(_reason(info["state"], info.get("details", "")))
    if remote.clean_address(address) != info["address"]:
        raise SetupError("Die Adresse hat sich geändert. Bitte die Karte neu laden und die neue Adresse bestätigen.")
    host = info["address"].split("//", 1)[1]
    funnel = funnel_state(run, tailscale, host)
    if funnel == "other":
        raise SetupError(_STATE_TEXT["funnel_busy"])
    if funnel == "unknown":
        raise SetupError(_STATE_TEXT["funnel_unknown"])

    placeholder = False
    current = remote.status(env, command, run=launchctl, uid=uid, platform="darwin")
    if not (current.get("enabled") and current.get("running") and current.get("address") == info["address"]
            and current.get("program_matches")):
        placeholder = remote.enable(env, command, info["address"], None, run=launchctl, uid=uid,
                                    platform="darwin")["passphrase_placeholder"]
    else:
        placeholder = bool(current.get("passphrase_placeholder"))

    if funnel != "ours":
        start_funnel(run, tailscale)
    if funnel_state(run, tailscale, host) != "ours":
        raise SetupError("Tailscale meldet den Funnel nicht als eingeschaltet. Klicke gleich noch einmal auf „Erneut prüfen“.")
    check_address(info["address"], fetch=fetch, sleep=sleep)
    return {"address": info["address"], "passphrase_placeholder": placeholder}
