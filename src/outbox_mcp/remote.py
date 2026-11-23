"""Phone use: the settings file of the public (HTTP) server and the background service that runs it.

remote.json lives in the data folder, next to mailbox.json. It holds the public address and the hash of
the sign-in passphrase, never the passphrase itself. The server reads it at start (`with_remote_file`), so
the background service needs nothing but the program and OUTBOX_DATA_DIR.

On macOS the service is a LaunchAgent of the user (no administrator rights). The tunnel is not part of
it: that is a separate step.

Messages in `reason` fields are shown to the person in the wizard (German); everything else is English.
"""

from __future__ import annotations

import json
import os
import plistlib
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Mapping, Optional
from urllib.parse import urlsplit

from .auth.passwords import hash_password, is_valid_hash
from .setup_tasks import SetupError, _folder, _write_private, install_problem, load_mailbox

REMOTE_FILE = "remote.json"
LABEL = "de.politzer.mailassistant.remote"
MIN_PASSPHRASE = 12
MAX_PASSPHRASE_BYTES = 1024
PORT = 8430  # the server's fixed port on this computer (http_server.py); the tunnel points here
_LAUNCHCTL = "/bin/launchctl"
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}

Runner = Callable[[list], "subprocess.CompletedProcess"]


# ---------------------------------------------------------------- the settings file, read by the server

def with_remote_file(env: Mapping[str, str]) -> Mapping[str, str]:
    """Fill in what the environment leaves empty from remote.json. A setting in the environment always wins.

    The approval host is the host name of the public address, taken from the file, so the two can never differ."""
    raw = (env.get("OUTBOX_DATA_DIR") or "").strip()
    if not raw:
        return env
    try:
        data = json.loads((Path(raw).expanduser() / REMOTE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return env
    if not isinstance(data, dict):
        return env
    merged = dict(env)
    address = data.get("public_base_url")
    if isinstance(address, str) and address.strip() and not (merged.get("OUTBOX_PUBLIC_BASE_URL") or "").strip():
        merged["OUTBOX_PUBLIC_BASE_URL"] = address.strip()
        host = urlsplit(address.strip()).hostname
        if host and not (merged.get("OUTBOX_APPROVAL_HOST") or "").strip():
            merged["OUTBOX_APPROVAL_HOST"] = host.lower()
    digest = data.get("password_hash")
    if isinstance(digest, str) and digest.strip() and not (merged.get("OUTBOX_OAUTH_PASSWORD_HASH") or "").strip():
        merged["OUTBOX_OAUTH_PASSWORD_HASH"] = digest.strip()
    return merged


def _read_remote(env: Mapping[str, str]) -> Optional[dict]:
    try:
        data = json.loads((_folder(env) / REMOTE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


# ---------------------------------------------------------------- checks

def clean_address(value: str) -> str:
    """The public address as https://host, or SetupError."""
    text = (value or "").strip()
    try:
        parts = urlsplit(text)
        host = (parts.hostname or "").lower()
        port = parts.port
    except ValueError:
        raise SetupError("Die Adresse ist ungültig. Sie sieht so aus: https://dein-mac.dein-netz.ts.net") from None
    if parts.scheme != "https" or not host:
        raise SetupError("Die Adresse muss mit https:// beginnen, zum Beispiel https://dein-mac.dein-netz.ts.net")
    if host in _LOCAL_HOSTS or host.endswith(".local"):
        raise SetupError("Das ist eine Adresse nur für diesen Computer. Das Handy braucht die öffentliche Adresse des Tunnels.")
    if parts.query or parts.fragment or parts.path not in ("", "/") or "@" in parts.netloc or port is not None:
        raise SetupError("Die Adresse darf nur der Name sein, ohne Pfad, Port oder Zusätze.")
    return f"https://{host}"


def check_passphrase(passphrase: str) -> None:
    if len(passphrase) < MIN_PASSPHRASE:
        raise SetupError(f"Die Passphrase ist zu kurz. Sie braucht mindestens {MIN_PASSPHRASE} Zeichen.")
    if len(passphrase.encode("utf-8")) > MAX_PASSPHRASE_BYTES:
        raise SetupError("Die Passphrase ist zu lang.")
    if passphrase != passphrase.strip():
        raise SetupError("Die Passphrase darf nicht mit einem Leerzeichen beginnen oder enden.")


def _home(env: Mapping[str, str]) -> Path:
    return Path(env.get("HOME") or Path.home())


def plist_path(env: Mapping[str, str]) -> Path:
    return _home(env) / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def log_folder(env: Mapping[str, str]) -> Path:
    return _home(env) / "Library" / "Logs" / "Mail Assistant"


def _require_mac(platform: Optional[str]) -> None:
    if (platform or sys.platform) != "darwin":
        raise SetupError("Der Handy-Betrieb ist in dieser Version nur für den Mac eingerichtet.")


def _check_program(command: str) -> None:
    if not command or not os.path.isabs(command):
        raise SetupError("Der Pfad des Mail-Assistant-Programms fehlt.")
    problem = install_problem(command)
    if problem:
        raise SetupError(problem)
    if not os.path.isfile(command) or not os.access(command, os.X_OK):
        raise SetupError("Das Mail-Assistant-Programm wurde unter diesem Pfad nicht gefunden.")


def build_plist(env: Mapping[str, str], command: str) -> dict:
    """Only the program and the data folder. Everything else the server reads from the data folder, so
    a later change needs no change here. No secret is in this file."""
    logs = log_folder(env)
    return {
        "Label": LABEL,
        "ProgramArguments": [command, "serve"],
        "EnvironmentVariables": {"OUTBOX_DATA_DIR": str(_folder(env))},
        "RunAtLoad": True,
        # Start again after a crash, but not after a clean stop; and never faster than every 10 seconds,
        # so a wrong setting does not make the service restart in a tight loop.
        "KeepAlive": {"SuccessfulExit": False},
        "ThrottleInterval": 10,
        "StandardOutPath": str(logs / "remote.log"),
        "StandardErrorPath": str(logs / "remote-error.log"),
    }


# ---------------------------------------------------------------- launchctl

def _run(args: list) -> "subprocess.CompletedProcess":
    return subprocess.run(args, capture_output=True, text=True, timeout=20)


def _domain(uid: int) -> str:
    return f"gui/{uid}"


def _bootout(run: Runner, uid: int) -> None:
    """Stop and unregister the service. 'Not loaded' is not an error."""
    run([_LAUNCHCTL, "bootout", f"{_domain(uid)}/{LABEL}"])


def _bootstrap(run: Runner, uid: int, plist: Path) -> None:
    done = run([_LAUNCHCTL, "bootstrap", _domain(uid), str(plist)])
    if done.returncode != 0:
        detail = " ".join((done.stderr or done.stdout or "").split())[:160]
        raise SetupError(
            "macOS konnte den Hintergrunddienst nicht starten."
            + (f" Details: {detail}" if detail else "")
        )


# ---------------------------------------------------------------- the three commands

def enable(
    env: Mapping[str, str],
    command: str,
    address: str,
    passphrase: Optional[str],
    *,
    run: Optional[Runner] = None,
    uid: Optional[int] = None,
    platform: Optional[str] = None,
    sleep: Optional[Callable[[float], None]] = None,
    port_busy: Optional[Callable[[], bool]] = None,
) -> dict:
    """Save address and passphrase hash, set up the background service and start it.

    `passphrase=None` is the tunnel step: a sign-in passphrase the person has chosen is kept as it is; with
    none yet, a random one is hashed and thrown away (marked `passphrase_placeholder`), so nobody can sign in
    until the person gets a passphrase of their own. A given passphrase always replaces the old one."""
    run = run or _run
    _require_mac(platform)
    _check_program(command)
    if load_mailbox(env) is None:
        raise SetupError("Das Postfach ist noch nicht eingerichtet.")
    base = clean_address(address)
    if passphrase is not None:
        check_passphrase(passphrase)
    uid = os.getuid() if uid is None else uid
    plist = plist_path(env)
    # A second call restarts the service with the new settings. launchd ends the old instance in the background,
    # so wait until it is really gone; starting the new one too early fails with "Input/output error".
    _bootout(run, uid)
    if not _wait_until_stopped(run, uid, sleep or time.sleep, port_busy or _port_busy):
        raise SetupError("Der Hintergrunddienst hat sich nicht rechtzeitig beendet. Versuche es in einem Moment noch einmal.")
    try:
        log_folder(env).mkdir(parents=True, exist_ok=True)
        if os.name == "posix":
            os.chmod(log_folder(env), 0o700)  # the logs hold one-time links
        plist.parent.mkdir(parents=True, exist_ok=True)
        existing = _read_remote(env) or {}
        if passphrase is not None:
            digest, placeholder = hash_password(passphrase), False
        elif is_valid_hash(existing.get("password_hash")) and not existing.get("passphrase_placeholder"):
            digest, placeholder = existing["password_hash"], False
        else:
            digest, placeholder = hash_password(secrets.token_urlsafe(32)), True
        _write_private(
            _folder(env) / REMOTE_FILE,
            json.dumps(
                {"public_base_url": base, "password_hash": digest, "passphrase_placeholder": placeholder}, indent=2
            )
            + "\n",
        )
        _write_private(plist, plistlib.dumps(build_plist(env, command)).decode("utf-8"))
    except OSError:
        raise SetupError("Die Einstellungen für den Handy-Betrieb lassen sich nicht speichern.") from None
    try:
        try:
            _bootstrap(run, uid, plist)
        except SetupError:
            (sleep or time.sleep)(1.0)  # launchd can still be busy with the old instance: one more try
            _bootstrap(run, uid, plist)
    except SetupError:
        _bootout(run, uid)
        try:
            plist.unlink()
        except OSError:
            pass
        raise
    return {"address": base, "passphrase_placeholder": placeholder, "log": str(log_folder(env) / "remote.log")}


def _port_busy(port: int = PORT) -> bool:
    """True while something on this computer still answers on the server's port."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            return True
    except OSError:
        return False


def _wait_until_stopped(
    run: Runner,
    uid: int,
    sleep: Callable[[float], None],
    port_busy: Callable[[], bool],
    steps: int = 40,
    pause: float = 0.25,
) -> bool:
    """Wait until launchd no longer lists the service, then give the port a moment to be released.

    False only when the service is still listed after the time is up (the files must then stay, so asking
    again works). A port that stays busy after the service is gone belongs to another program: not our error."""
    for _ in range(steps):
        try:
            listed = run([_LAUNCHCTL, "print", f"{_domain(uid)}/{LABEL}"]).returncode == 0
        except (OSError, subprocess.SubprocessError):
            listed = False
        if not listed:
            break
        sleep(pause)
    else:
        return False
    for _ in range(steps // 2):
        if not port_busy():
            break
        sleep(pause)
    return True


def disable(
    env: Mapping[str, str],
    *,
    run: Optional[Runner] = None,
    uid: Optional[int] = None,
    platform: Optional[str] = None,
    sleep: Callable[[float], None] = time.sleep,
    port_busy: Callable[[], bool] = _port_busy,
    before_removal: Optional[Callable[[], None]] = None,
) -> bool:
    """Stop the service and remove its files. Recipients, passkeys and the mailbox stay.
    Returns only when the service is really gone and its port is free again.
    `before_removal` runs once the service is stopped and before any file goes; if it raises, the files stay,
    so asking again finishes the job.
    True when there was something to remove; asking twice is not an error."""
    run = run or _run
    _require_mac(platform)
    uid = os.getuid() if uid is None else uid
    plist = plist_path(env)
    had = plist.exists() or (_folder(env) / REMOTE_FILE).exists()
    _bootout(run, uid)
    if not _wait_until_stopped(run, uid, sleep, port_busy):
        raise SetupError("Der Hintergrunddienst hat sich nicht rechtzeitig beendet. Versuche es in einem Moment noch einmal.")
    if before_removal is not None:
        before_removal()
    for path in (plist, _folder(env) / REMOTE_FILE):
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            raise SetupError("Eine Datei des Handy-Betriebs lässt sich nicht löschen.") from None
    return had


def status(
    env: Mapping[str, str],
    command: str = "",
    *,
    run: Optional[Runner] = None,
    uid: Optional[int] = None,
    platform: Optional[str] = None,
) -> dict:
    """The real state. Never contains the hash or the passphrase."""
    run = run or _run
    if (platform or sys.platform) != "darwin":
        return {"supported": False, "enabled": False}
    uid = os.getuid() if uid is None else uid
    plist = plist_path(env)
    data = _read_remote(env)
    address = data.get("public_base_url") if data else None
    configured = bool(data) and isinstance(address, str) and is_valid_hash(data.get("password_hash"))
    program = None
    try:
        program = plistlib.loads(plist.read_bytes())["ProgramArguments"][0]
    except (OSError, ValueError, KeyError, IndexError, TypeError, plistlib.InvalidFileException):
        pass
    loaded = running = False
    if program is not None:
        try:
            done = run([_LAUNCHCTL, "print", f"{_domain(uid)}/{LABEL}"])
        except (OSError, subprocess.SubprocessError):
            done = None
        if done is not None and done.returncode == 0:
            loaded = True
            running = any(line.strip() == "state = running" for line in (done.stdout or "").splitlines())
    return {
        "supported": True,
        # Any leftover of phone use (also half of it): the way back is offered as long as one is there.
        "traces": plist.exists() or data is not None,
        "enabled": configured and program is not None,
        "loaded": loaded,
        "running": running,
        "address": address if configured else None,
        "program_ok": program is not None and os.path.isfile(program),
        "program_matches": bool(command) and program == command,
        "passphrase_placeholder": bool(data and data.get("passphrase_placeholder")),
        "log": str(log_folder(env) / "remote.log"),
    }


# ---------------------------------------------------------------- sign-in for the phone (passphrase, passkey)

# 31 characters: no 0/O, 1/I/L. Five groups of five give about 124 bits, readable and typeable on a phone.
PASSPHRASE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def generate_passphrase() -> str:
    """A new random sign-in passphrase such as `K7M2P-X9QRT-...`. Shown once, only its hash is stored."""
    return "-".join("".join(secrets.choice(PASSPHRASE_ALPHABET) for _ in range(5)) for _ in range(5))


def _revoke_logins(env: Mapping[str, str], failure: Optional[str] = None) -> None:
    """Forget every connector sign-in and token (Claude on the phone must sign in again). Nothing to do while
    nobody has ever signed in (no database yet)."""
    from .auth.store import AuthStore
    from .paths import default_file

    path = Path((env.get("OUTBOX_OAUTH_DB_PATH") or "").strip() or default_file(env, "auth.db"))
    if not path.exists():
        return
    try:
        AuthStore(path).revoke_all()
    except Exception:
        raise SetupError(
            failure or "Die bisherigen Anmeldungen am Handy lassen sich nicht beenden. Die Passphrase wurde nicht geändert."
        ) from None


def renew_passphrase(
    env: Mapping[str, str],
    command: str,
    *,
    run: Optional[Runner] = None,
    uid: Optional[int] = None,
    platform: Optional[str] = None,
    revoke: Optional[Callable[[Mapping[str, str]], None]] = None,
) -> dict:
    """Make a new sign-in passphrase for the phone and return it once. Every earlier sign-in ends first (the
    old passphrase's tokens would otherwise live on for up to 90 days); then the hash is replaced and the
    background service restarted, so it loads the new hash."""
    state = status(env, command, run=run, uid=uid, platform=platform)
    if not state.get("supported"):
        raise SetupError("Der Handy-Betrieb ist hier noch nicht möglich.")
    if not state.get("enabled") or not state.get("address"):
        raise SetupError("Der Tunnel ist noch nicht eingerichtet. Schalte ihn zuerst ein.")
    passphrase = generate_passphrase()
    (revoke or _revoke_logins)(env)
    answer = enable(env, command, state["address"], passphrase, run=run, uid=uid, platform=platform)
    return {"passphrase": passphrase, "address": answer["address"]}


def passkey_link(
    env: Mapping[str, str],
    *,
    run: Optional[Runner] = None,
    uid: Optional[int] = None,
    platform: Optional[str] = None,
) -> str:
    """One-time link to register a passkey on the phone, on the public approval page. The passkey belongs to
    the host name of the public address, so it has to be created there, on the phone itself. The page is part of
    the background service, which therefore has to be running."""
    from .approval.service import ApprovalConfig, ApprovalService
    from .paths import default_file
    from .storage.database import Database

    state = status(env, "", run=run, uid=uid, platform=platform)
    if not state.get("supported"):
        raise SetupError("Der Handy-Betrieb ist hier noch nicht möglich.")
    if not state.get("enabled") or not state.get("address"):
        raise SetupError("Der Tunnel ist noch nicht eingerichtet. Schalte ihn zuerst ein.")
    if not state.get("running"):
        raise SetupError("Der Hintergrunddienst läuft nicht. Schalte den Tunnel noch einmal ein.")
    host = urlsplit(state["address"]).hostname or ""
    merged = dict(env)
    merged["OUTBOX_APPROVAL_HOST"] = host
    merged.pop("OUTBOX_APPROVAL_PORT", None)
    config = ApprovalConfig.from_env(merged)
    service = ApprovalService(Database(str(default_file(env, "outbox.db"))), config, time.time)
    token = service.create_enrollment()
    if token is None:
        raise SetupError(
            "Es sind schon zwei Passkeys eingerichtet, mehr geht nicht. Entferne zuerst einen alten "
            "in der Verwaltung (Kachel „Passkeys“)."
        )
    return service.enrollment_url(token)
