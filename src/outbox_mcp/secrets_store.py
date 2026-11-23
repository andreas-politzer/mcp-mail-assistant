"""The SMTP password in the system keychain (macOS Keychain, Windows Credential Manager,
Secret Service on Linux).

Windows and Linux go through the `keyring` library. On macOS the system program
`/usr/bin/security` is used instead: macOS asks for the device password whenever a *different*
program reads an item, and every rebuilt app or Python file counts as a different program.
`/usr/bin/security` never changes, so it creates and reads its own items without any dialog.

Trade-off, on purpose: on macOS any process of the same user can read the password through
`/usr/bin/security` without a dialog (the per-program prompt is gone). The passkey approval still
guards sending through this server, but not a process that talks to the mail server itself.

Nothing here writes the password to a file. The environment variable OUTBOX_SMTP_PASSWORD
still works for servers without a keychain; the keychain is the way for a desktop account.
Set OUTBOX_KEYCHAIN_BACKEND=keyring to use `keyring` on macOS as well.
"""

from __future__ import annotations

import contextlib
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from typing import Iterator, Optional

from .errors import ConfigError


class KeychainError(ConfigError):
    """Any failure of the keychain step. The wizard shows these in German; the text itself stays short English."""


class KeychainLocked(KeychainError):
    """The keychain is locked: the person has to sign in to the Mac again."""

SERVICE = "mail-assistant"
SECURITY = "/usr/bin/security"
_PREFIX = "ma1:"  # stored value = prefix + hex of the UTF-8 password, so it is always plain ASCII
_LOCK = threading.Lock()  # one keychain access at a time inside a process
MAX_PASSWORD_BYTES = 1024
LOCK_WAIT_SECONDS = 30.0
READ_TIMEOUT = 30  # Claude Desktop starts the server and waits; do not hang longer than it does
WRITE_TIMEOUT = 60
_LOCKED_MESSAGE = "The keychain is locked. Unlock it (sign in to the Mac) and try again."


def account_name(host: str, user: str) -> str:
    return f"smtp:{user}@{host}"


def _keyring():
    try:
        import keyring
        import keyring.errors
    except ImportError:  # pragma: no cover - keyring is a declared dependency
        raise KeychainError("The keychain library (keyring) is not installed.") from None
    return keyring


def _use_security() -> bool:
    return (
        sys.platform == "darwin"
        and os.environ.get("OUTBOX_KEYCHAIN_BACKEND", "").lower() != "keyring"
        and os.path.exists(SECURITY)
    )


def _lock_file() -> str:
    folder = os.environ.get("OUTBOX_DATA_DIR", "").strip()
    if folder and os.path.isdir(folder):
        return os.path.join(folder, ".keychain.lock")
    return os.path.join(tempfile.gettempdir(), f"mail-assistant-keychain-{os.getuid()}.lock")


@contextlib.contextmanager
def _exclusive() -> Iterator[None]:
    """One keychain change at a time: across threads and across the processes of this app (wizard,
    server started by Claude Desktop, command line). A second process waits briefly instead of racing."""
    import fcntl

    with _LOCK:
        fd = os.open(_lock_file(), os.O_CREAT | os.O_RDWR, 0o600)
        try:
            deadline = time.monotonic() + LOCK_WAIT_SECONDS
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise KeychainError("Another part of the app is using the keychain. Try again in a moment.") from None
                    time.sleep(0.05)
            try:
                yield
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _run(args: list[str], stdin: Optional[str] = None, timeout: int = WRITE_TIMEOUT) -> "subprocess.CompletedProcess[str]":
    try:
        return subprocess.run(
            [SECURITY, *args], input=stdin, capture_output=True, text=True, timeout=timeout, check=False
        )
    except subprocess.TimeoutExpired:
        raise KeychainError(
            "The keychain did not answer in time. If a device-password dialog is open, confirm it and try again."
        ) from None
    except OSError:
        raise KeychainError("The keychain program could not be started.") from None


def _not_found(done: "subprocess.CompletedProcess[str]") -> bool:
    return done.returncode == 44 or "could not be found" in done.stderr


def _is_locked(done: "subprocess.CompletedProcess[str]") -> bool:
    return done.returncode == 36 or "interaction is not allowed" in done.stderr.lower()


def _quote(value: str) -> str:
    if re.search(r'["\\\n\r\x00]', value) or any(ord(c) < 32 for c in value):
        raise KeychainError("The mailbox address or server contains characters the keychain cannot take.")
    return f'"{value}"'


def _decode_legacy(raw: str) -> str:
    """Items written by `keyring` come back raw, or as hex when they hold non-ASCII text."""
    if raw and len(raw) % 2 == 0 and re.fullmatch(r"[0-9a-fA-F]+", raw):
        try:
            text = bytes.fromhex(raw).decode("utf-8")
            if text.isprintable() and not text.isascii():
                return text
        except ValueError:
            pass
    return raw


def _decode(raw: str) -> tuple[str, bool]:
    """The password in a stored value, and whether it was an old `keyring` value (no prefix)."""
    if raw.startswith(_PREFIX):
        try:
            return bytes.fromhex(raw[len(_PREFIX):]).decode("utf-8"), False
        except ValueError:
            raise KeychainError("The stored password is damaged. Enter it again.") from None
    return _decode_legacy(raw), True


def _sec_read(account: str) -> tuple[Optional[str], bool]:
    """(password or None, was it an old value). Never changes anything."""
    done = _run(["find-generic-password", "-s", SERVICE, "-a", account, "-w"], timeout=READ_TIMEOUT)
    if done.returncode != 0:
        if _not_found(done):
            return None, False
        if _is_locked(done):
            raise KeychainLocked(_LOCKED_MESSAGE)
        raise KeychainError("The keychain could not be read. Unlock it and try again.")
    return _decode(done.stdout.rstrip("\n"))


def _sec_get(account: str) -> Optional[str]:
    password, legacy = _sec_read(account)
    if password is None:
        # Another process may be writing right now (delete, then add): look once more.
        time.sleep(0.2)
        password, legacy = _sec_read(account)
    if password is not None and legacy:
        # An item made by another program (older `keyring` entry) asks for the device password on every
        # read by a new program. Re-save it once as our own item, then it never asks again.
        try:
            _sec_set(account, password, legacy=True)
        except ConfigError:
            pass
    return password


def _sec_delete(account: str) -> bool:
    """Removes every entry of this account (a delete takes one match per call). True if any was removed."""
    removed = False
    for _ in range(10):
        done = _run(["delete-generic-password", "-s", SERVICE, "-a", account])
        if done.returncode == 0:
            removed = True
            continue
        if _not_found(done):
            return removed
        if _is_locked(done):
            raise KeychainLocked(_LOCKED_MESSAGE)
        raise KeychainError("The keychain refused the change. Unlock it and try again.")
    raise KeychainError("The keychain still holds entries after ten deletions. Remove them in Keychain Access.")


def _sec_add(account: str, password: str, update: bool) -> bool:
    value = (_PREFIX + password.encode("utf-8").hex()).encode("ascii").hex()
    flag = "-U " if update else ""
    line = f"add-generic-password {flag}-s {_quote(SERVICE)} -a {_quote(account)} -X {value}\n"
    _run(["-i"], stdin=line)
    stored, legacy = _sec_read(account)  # `security -i` hides failures in its exit code
    return stored == password and not legacy


def _sec_set(account: str, password: str, legacy: bool = False) -> None:
    # An item that security made itself can be updated in place: nothing is deleted, so a failure cannot
    # lose the old password. An item made by another program (older `keyring` entry) cannot be updated;
    # it is replaced, and the new one is written up to three times before giving up.
    if not legacy:
        if _sec_add(account, password, update=True):
            return
        current, old_value = _sec_read(account)
        if current is not None and not old_value:
            raise KeychainError("The keychain could not update the password. The old one is kept.")
    _sec_delete(account)
    for _ in range(3):
        if _sec_add(account, password, update=False):
            return
    raise KeychainError("The keychain refused the password.")


def backend_name() -> str:
    if _use_security():
        return "macos.security"
    kr = _keyring()
    backend = kr.get_keyring()
    return f"{type(backend).__module__}.{type(backend).__name__}"


def _unavailable(exc: Exception) -> KeychainError:
    return KeychainError(
        "No system keychain is available here "
        f"({exc.__class__.__name__}). Set OUTBOX_SMTP_PASSWORD in the environment instead."
    )


def get_smtp_password(host: str, user: str) -> Optional[str]:
    """The stored password, or None when none is stored or no keychain exists."""
    if _use_security():
        with _exclusive():
            return _sec_get(account_name(host, user))
    kr = _keyring()
    try:
        value = kr.get_password(SERVICE, account_name(host, user))
    except kr.errors.NoKeyringError:
        return None
    except kr.errors.KeyringError as exc:
        raise KeychainError(f"The keychain could not be read ({exc.__class__.__name__}). Unlock it and try again.") from None
    if value is not None and value.startswith(_PREFIX):
        # Written on macOS through /usr/bin/security and now read through keyring (switched back).
        return _decode(value)[0]
    return value


def set_smtp_password(host: str, user: str, password: str) -> None:
    if not password:
        raise KeychainError("The password is empty.")
    if len(password.encode("utf-8")) > MAX_PASSWORD_BYTES:
        raise KeychainError(f"The password is longer than {MAX_PASSWORD_BYTES} bytes, which the keychain step does not take.")
    if _use_security():
        with _exclusive():
            _sec_set(account_name(host, user), password)
        return
    kr = _keyring()
    try:
        kr.set_password(SERVICE, account_name(host, user), password)
    except kr.errors.NoKeyringError as exc:
        raise _unavailable(exc) from None
    except kr.errors.KeyringError as exc:
        raise KeychainError(f"The keychain refused the password ({exc.__class__.__name__}).") from None


def delete_smtp_password(host: str, user: str) -> bool:
    """True when an entry was removed, False when there was none."""
    if _use_security():
        with _exclusive():
            return _sec_delete(account_name(host, user))
    kr = _keyring()
    try:
        kr.delete_password(SERVICE, account_name(host, user))
        return True
    except kr.errors.PasswordDeleteError:
        return False
    except kr.errors.NoKeyringError as exc:
        raise _unavailable(exc) from None
    except kr.errors.KeyringError as exc:
        raise KeychainError(f"The keychain refused the change ({exc.__class__.__name__}).") from None
