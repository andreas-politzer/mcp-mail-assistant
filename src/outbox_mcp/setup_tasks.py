"""The steps of the setup wizard, done by the server program itself.

The desktop app only starts this program and reads one line of JSON back (see setup_cli.py), so every
step can be tested here without a window. Nothing in this file writes the mail password to a file: it
goes from one standard-input line into the system keychain and nowhere else.

Messages in `reason` fields are shown to the person in the wizard (German); everything else is English.
"""

from __future__ import annotations

import json
import os
import smtplib
import socket
import ssl
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping, Optional

from . import secrets_store
from .errors import OutboxError, ValidationError
from .mail.message_builder import build_message
from .mail.smtp_client import SmtpClient, SmtpSendFailed, SmtpSendUnknown
from .paths import data_dir
from .security.validation import validate_address
from .storage.database import Database
from .storage.recipient_store import RecipientStore

MAILBOX_FILE = "mailbox.json"
PASSWORD_FLAG = "password_stored"
ENTRY_NAME = "mail-assistant"
SOURCE = "setup:wizard"
_SECURITY = ("starttls", "ssl", "none")
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
# Fixed port of the local approval page; if something else holds it, the page moves to the next free one.
APPROVAL_PORT = 47821


class SetupError(OutboxError):
    """A step failed. The text is meant for the person in the wizard."""


@dataclass(frozen=True)
class Mailbox:
    email: str
    host: str
    port: int
    security: str

    def validate(self) -> "Mailbox":
        try:
            email = validate_address(self.email)
        except ValidationError:
            raise SetupError("Das ist keine gültige E-Mail-Adresse.") from None
        host = self.host.strip()
        if not host or any(c.isspace() for c in host):
            raise SetupError("Der Server fehlt oder ist ungültig.")
        if not 0 < int(self.port) < 65536:
            raise SetupError("Der Port ist ungültig.")
        if self.security not in _SECURITY:
            raise SetupError("Die Verschlüsselung muss STARTTLS oder SSL/TLS sein.")
        if self.security == "none" and host.lower() not in _LOCAL_HOSTS:
            raise SetupError("Ohne Verschlüsselung geht es nur für den eigenen Computer (zum Testen).")
        return Mailbox(email, host, int(self.port), self.security)


def _folder(env: Mapping[str, str]) -> Path:
    folder = data_dir(env)
    if folder is None:
        raise SetupError("Der Datenordner ist nicht festgelegt (OUTBOX_DATA_DIR).")
    return folder


def load_mailbox(env: Mapping[str, str]) -> Optional[Mailbox]:
    path = _folder(env) / MAILBOX_FILE
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return Mailbox(raw["email"], raw["host"], int(raw["port"]), raw["security"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _write_private(path: Path, text: str) -> None:
    """Write through a temporary file in the same folder, so a crash never leaves half a file."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        if os.name == "posix":
            os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _explain(exc: BaseException, secret: str = "") -> str:
    """A short German reason for a failed SMTP connection. Never contains the password."""
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return "Der Anbieter hat die Anmeldung abgelehnt. Passwort oder App-Passwort stimmt nicht, oder der Zugriff für externe Programme ist nicht eingeschaltet."
    if isinstance(exc, smtplib.SMTPNotSupportedError):
        return "Der Server bietet diese Art der Anmeldung oder Verschlüsselung nicht an. Prüfe Port und Verschlüsselung."
    if isinstance(exc, (ssl.SSLError, ssl.CertificateError)):
        return "Die verschlüsselte Verbindung konnte nicht sicher aufgebaut werden. Prüfe Server, Port und Verschlüsselung."
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return "Der Server antwortet nicht rechtzeitig. Prüfe Server und Port und deine Internetverbindung."
    if isinstance(exc, (socket.gaierror, ConnectionError, OSError)):
        return "Der Server ist nicht erreichbar. Prüfe den Servernamen, den Port und deine Internetverbindung."
    text = " ".join(f"{exc}".split())
    if secret:
        text = text.replace(secret, "***")
    return f"Der Anbieter meldet einen Fehler: {text[:160]}"


def check_login(box: Mailbox, password: str, timeout: float = 20.0, context: Optional[ssl.SSLContext] = None) -> None:
    """Connect and sign in, send nothing. Raises SetupError with a reason."""
    context = context or ssl.create_default_context()
    server: Optional[smtplib.SMTP] = None
    try:
        if box.security == "ssl":
            server = smtplib.SMTP_SSL(box.host, box.port, context=context, timeout=timeout)
        else:
            server = smtplib.SMTP(box.host, box.port, timeout=timeout)
        server.ehlo()
        if box.security == "starttls":
            if not server.has_extn("starttls"):
                raise smtplib.SMTPNotSupportedError("no STARTTLS")
            server.starttls(context=context)
            server.ehlo()
        server.login(box.email, password)
    except (smtplib.SMTPException, OSError, ssl.SSLError) as exc:
        raise SetupError(_explain(exc, password)) from None
    finally:
        if server is not None:
            try:
                server.quit()
            except Exception:
                try:
                    server.close()
                except Exception:
                    pass


def keychain_reason(exc: BaseException) -> str:
    """A German reason for a failed keychain step: what to do first, the short technical cause after it."""
    if isinstance(exc, secrets_store.KeychainLocked):
        return "Der Schlüsselbund ist gesperrt. Entsperre ihn, indem du dich am Mac neu anmeldest (Bildschirm entsperren), und versuche es noch einmal."
    return (
        "Der Schlüsselbund dieses Computers hat das Passwort nicht angenommen. Entsperre den Mac, "
        f"starte die App notfalls neu und versuche es noch einmal. Details: {exc}"
    )


def save_mailbox(
    env: Mapping[str, str], box: Mailbox, password: str, context: Optional[ssl.SSLContext] = None
) -> None:
    """Check the sign-in first; only a working password reaches the keychain and the settings file."""
    box = box.validate()
    folder = _folder(env)
    if not password:
        raise SetupError("Das Passwort fehlt.")
    check_login(box, password, context=context)
    if box.security == "none":
        # Test setting for the local mail sink: no keychain, the password stays out of every file.
        raise SetupError("Ohne Verschlüsselung wird kein Passwort gespeichert.")
    try:
        secrets_store.set_smtp_password(box.host, box.email, password)
    except OutboxError as exc:
        raise SetupError(keychain_reason(exc)) from None
    previous = load_mailbox(env)
    # The flag lets the wizard show "password stored" without reading the keychain, which on a Mac costs the
    # person a password dialog every time.
    _write_private(folder / MAILBOX_FILE, json.dumps({**asdict(box), PASSWORD_FLAG: True}, indent=2) + "\n")
    if previous is not None and (previous.host, previous.email) != (box.host, box.email):
        # The provider or address changed: the old password has no use any more, so it does not stay behind.
        try:
            secrets_store.delete_smtp_password(previous.host, previous.email)
        except OutboxError:
            pass


def send_test_mail(
    env: Mapping[str, str], to: str, mark_own: bool, context: Optional[ssl.SSLContext] = None, lang: str = "de"
) -> None:
    box = load_mailbox(env)
    if box is None:
        raise SetupError("Das Postfach ist noch nicht eingerichtet.")
    try:
        to = validate_address(to)
    except ValidationError:
        raise SetupError("Das ist keine gültige Empfängeradresse.") from None
    password = (env.get("OUTBOX_SMTP_PASSWORD") or "") or (secrets_store.get_smtp_password(box.host, box.email) or "")
    if not password:
        raise SetupError("Im Schlüsselbund liegt kein Passwort für dieses Postfach. Bitte das Passwort noch einmal eingeben.")
    if lang == "en":
        subject = "Test mail from Mail Assistant"
        body = "This mail comes from your Mail Assistant. If you are reading it, sending is set up.\n"
    else:
        subject = "Testmail von Mail Assistant"
        body = "Diese Mail kommt von deinem Mail Assistant. Wenn du sie liest, ist der Versand eingerichtet.\n"
    message = build_message(box.email, "", to, "", subject, body)
    client = SmtpClient(box.host, box.port, box.email, password, box.security, 30.0, ssl_context=context)
    try:
        client.send(message, box.email, to)
    except SmtpSendFailed as exc:
        raise SetupError(f"Der Anbieter hat die Testmail nicht angenommen. {_trim(str(exc), password)}") from None
    except SmtpSendUnknown:
        raise SetupError("Die Verbindung brach ab, während die Testmail übergeben wurde. Schau in dein Postfach, bevor du es noch einmal versuchst.") from None
    if mark_own:
        _mark_own(env, to)


def _trim(text: str, secret: str) -> str:
    return text.replace(secret, "***")[:160]


def _mark_own(env: Mapping[str, str], address: str) -> None:
    from .paths import default_file

    store = RecipientStore(Database(str(default_file(env, "outbox.db"))))
    now = time.time()
    try:
        store.add("Ich", address, [], now, SOURCE, scope="own")
    except ValidationError:
        # Already on the list (active): make sure it counts as own.
        store.set_scope(address, "own", now, SOURCE)


# ---------------------------------------------------------------- Claude Desktop

def claude_config_path(env: Mapping[str, str], platform: Optional[str] = None) -> Path:
    override = (env.get("CLAUDE_DESKTOP_CONFIG") or "").strip()
    if override:
        return _follow_link(Path(override))
    platform = platform or sys.platform
    home = Path(env.get("HOME") or env.get("USERPROFILE") or Path.home())
    if platform == "darwin":
        path = home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    elif platform.startswith("win"):
        path = Path(env.get("APPDATA") or home / "AppData" / "Roaming") / "Claude" / "claude_desktop_config.json"
    else:
        path = home / ".config" / "Claude" / "claude_desktop_config.json"
    return _follow_link(path)


def _follow_link(path: Path) -> Path:
    """A settings file that is a symbolic link (some people keep it in a dotfiles folder) stays a link:
    we write to what it points at, instead of replacing the link with a plain file."""
    return Path(os.path.realpath(path)) if path.is_symlink() else path


def _require_claude_folder(path: Path) -> None:
    """Claude Desktop creates its settings folder on first start. No folder means it is not installed
    (or never started): creating the folder ourselves would make the wizard look successful for nothing."""
    if not path.parent.is_dir():
        raise SetupError(
            "Claude Desktop ist auf diesem Computer nicht installiert oder wurde noch nie gestartet. "
            "Lade es von claude.com/download, öffne es einmal und komm dann hierher zurück."
        )


def _entry(env: Mapping[str, str], command: str) -> dict:
    box = load_mailbox(env)
    if box is None:
        raise SetupError("Das Postfach ist noch nicht eingerichtet.")
    if not command or not os.path.isabs(command):
        raise SetupError("Der Pfad des Mail-Assistant-Programms fehlt.")
    problem = install_problem(command)
    if problem:
        raise SetupError(problem)
    return {
        "command": command,
        "args": ["stdio"],
        "env": {
            "OUTBOX_DATA_DIR": str(_folder(env)),
            # The mailbox itself is not here: the server reads mailbox.json from the data folder, so a
            # later change of the mailbox needs no change of this entry and no restart of Claude Desktop.
            "OUTBOX_EXIT_WHEN_STDIN_CLOSES": "1",
            # The local approval page: mail to other people is approved there with a passkey.
            "OUTBOX_APPROVAL_HOST": "localhost",
            "OUTBOX_APPROVAL_PORT": str(APPROVAL_PORT),
        },
    }


def _read_config(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
    except (OSError, ValueError):
        raise SetupError("Die Konfigurationsdatei von Claude Desktop ist nicht lesbar. Ich ändere sie nicht.") from None
    if not isinstance(data, dict) or not isinstance(data.get("mcpServers", {}), dict):
        raise SetupError("Die Konfigurationsdatei von Claude Desktop hat einen unerwarteten Aufbau. Ich ändere sie nicht.")
    return data


def preview_claude(env: Mapping[str, str], command: str) -> list:
    _entry(env, command)  # same checks as the real change
    path = claude_config_path(env)
    _require_claude_folder(path)
    data = _read_config(path)
    lines = []
    if ENTRY_NAME in data.get("mcpServers", {}):
        lines.append("Der vorhandene Eintrag „mail-assistant“ in der Konfigurationsdatei von Claude Desktop wird aktualisiert.")
    else:
        lines.append("In der Konfigurationsdatei von Claude Desktop wird ein Eintrag „mail-assistant“ ergänzt.")
    others = [k for k in data.get("mcpServers", {}) if k != ENTRY_NAME]
    lines.append(f"Alle anderen Einträge ({len(others)}) bleiben unberührt." if others else "Alle anderen Einstellungen bleiben unberührt.")
    if path.exists():
        lines.append("Vorher wird eine Kopie der Datei neben das Original gelegt.")
    return lines


def _backup_name(path: Path) -> Path:
    """A name for a copy of the settings file that is not taken yet: two changes within the same second
    (connect, then remove) must not overwrite each other's copy."""
    stem = f"{path.name}.mail-assistant-backup-{time.strftime('%Y%m%d-%H%M%S')}"
    candidate, n = path.with_name(stem), 1
    while candidate.exists():
        n += 1
        candidate = path.with_name(f"{stem}-{n}")
    return candidate


def connect_claude(env: Mapping[str, str], command: str) -> Optional[Path]:
    """Add or update only our own entry. Returns the backup file, or None when there was no file yet."""
    entry = _entry(env, command)
    path = claude_config_path(env)
    _require_claude_folder(path)
    data = _read_config(path)
    backup = None
    try:
        if path.exists():
            backup = _backup_name(path)
            _write_private(backup, path.read_text(encoding="utf-8"))
        data.setdefault("mcpServers", {})[ENTRY_NAME] = entry
        _write_private(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    except OSError:
        raise SetupError("Die Konfigurationsdatei von Claude Desktop lässt sich nicht schreiben.") from None
    return backup


def disconnect_claude(env: Mapping[str, str]) -> bool:
    """Remove only our own entry (backup first). True when an entry was removed, False when there was none:
    asking twice, or asking without Claude Desktop installed, is not an error."""
    path = claude_config_path(env)
    if not path.exists():
        return False
    data = _read_config(path)
    servers = data.get("mcpServers", {})
    if ENTRY_NAME not in servers:
        return False
    try:
        backup = _backup_name(path)
        _write_private(backup, path.read_text(encoding="utf-8"))
        del servers[ENTRY_NAME]
        _write_private(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    except OSError:
        raise SetupError("Die Konfigurationsdatei von Claude Desktop lässt sich nicht schreiben.") from None
    return True


def install_problem(command: str) -> Optional[str]:
    """A place the program must not be started from when Claude Desktop is to use it, or None.

    macOS runs a freshly downloaded app from a temporary, read-only copy ("App Translocation") until it was moved,
    and a program started from a mounted disk image is gone once the image is ejected. The path of either would be
    written into Claude Desktop's configuration and stop working."""
    path = (command or "").replace("\\", "/")
    if "/AppTranslocation/" in path:
        return (
            "Mail Assistant läuft gerade aus einem vorläufigen Ordner, den macOS beim ersten Start anlegt. "
            "Beende die App, ziehe sie in den Ordner „Programme“ und starte sie von dort neu."
        )
    if path.startswith("/Volumes/"):
        return (
            "Mail Assistant läuft gerade von einem Datenträger, zum Beispiel direkt aus der heruntergeladenen Image-Datei. "
            "Ziehe die App in den Ordner „Programme“, wirf die Image-Datei aus und starte die App von dort neu."
        )
    return None


def status(env: Mapping[str, str], command: str = "") -> dict:
    """The real state, so the wizard only asks for what is missing."""
    box = load_mailbox(env)
    stored = False
    if box is not None:
        # Never reads the keychain: a read would make macOS ask for the device password. The flag is set once the
        # password has been stored; a file from before the flag counts as "not stored" and the wizard asks again.
        try:
            raw = json.loads((_folder(env) / MAILBOX_FILE).read_text(encoding="utf-8"))
            stored = raw.get(PASSWORD_FLAG) is True
        except (OSError, ValueError, AttributeError):
            stored = False
    connected = False
    try:
        entry = _read_config(claude_config_path(env)).get("mcpServers", {}).get(ENTRY_NAME)
        connected = bool(entry) and (not command or entry.get("command") == command)
    except SetupError:
        connected = False
    return {
        "mailbox": asdict(box) if box else None,
        "password_stored": stored,
        "claude_connected": connected,
        "passkeys": _passkey_count(env),
        "passkey_list": _passkey_list(env),
        "install_problem": install_problem(command),
    }


# ---------------------------------------------------------------- local approval page (passkey)

def approval_env(env: Mapping[str, str]) -> dict:
    """The environment the approval page runs with: the local page on a fixed port."""
    merged = dict(env)
    merged["OUTBOX_APPROVAL_HOST"] = "localhost"
    merged["OUTBOX_APPROVAL_PORT"] = str(APPROVAL_PORT)
    return merged


def _approval_service(env: Mapping[str, str]):
    from .approval.service import ApprovalConfig, ApprovalService
    from .paths import default_file

    config = ApprovalConfig.from_env(approval_env(env))
    return ApprovalService(Database(str(default_file(env, "outbox.db"))), config, time.time)


def _passkey_count(env: Mapping[str, str]) -> int:
    try:
        return _approval_service(env).passkey_count()
    except Exception:
        return 0


def _passkey_list(env: Mapping[str, str]) -> list:
    """Name and creation time of each passkey, for the wizard. Never the ids or the keys."""
    try:
        return [
            {"name": k["label"] or "", "created_at": k["created_at"]}
            for k in _approval_service(env).list_passkeys()
        ]
    except Exception:
        return []


def approval_link(env: Mapping[str, str]) -> str:
    """One-time link to register a passkey on the local approval page.

    The page runs inside the server that Claude Desktop starts, so it only answers after that
    program is running. Without it there is nothing to register on, and the reason says what to do."""
    from dataclasses import replace

    from .approval.local_listener import find_running_page

    service = _approval_service(env)
    port = find_running_page(service.config)
    if port is None:
        raise SetupError(
            "Die Freigabeseite läuft noch nicht. Sie startet mit Claude Desktop: beende Claude Desktop ganz "
            "(Cmd+Q auf dem Mac, nicht nur das Fenster schließen), starte es neu, öffne einen neuen Chat und versuche es dann noch einmal."
        )
    if port != service.config.port:
        service.config = replace(service.config, port=port)
    token = service.create_enrollment()
    if token is None:
        raise SetupError("Es sind schon alle möglichen Passkeys eingerichtet.")
    return service.enrollment_url(token)
