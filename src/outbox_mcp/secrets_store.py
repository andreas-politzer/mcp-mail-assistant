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

import os
import re
import subprocess
import sys
import threading
from typing import Optional

from .errors import ConfigError

SERVICE = "mail-assistant"
SECURITY = "/usr/bin/security"
_PREFIX = "ma1:"  # stored value = prefix + hex of the UTF-8 password, so it is always plain ASCII
_LOCK = threading.Lock()  # one keychain access at a time inside a process


def account_name(host: str, user: str) -> str:
    return f"smtp:{user}@{host}"


def _keyring():
    try:
        import keyring
        import keyring.errors
    except ImportError:  # pragma: no cover - keyring is a declared dependency
        raise ConfigError("The keychain library (keyring) is not installed.") from None
    return keyring


def _use_security() -> bool:
    return (
        sys.platform == "darwin"
        and os.environ.get("OUTBOX_KEYCHAIN_BACKEND", "").lower() != "keyring"
        and os.path.exists(SECURITY)
    )


def _run(args: list[str], stdin: Optional[str] = None) -> "subprocess.CompletedProcess[str]":
    # Generous timeout: reading an item made by another program shows a device-password dialog once.
    try:
        return subprocess.run(
            [SECURITY, *args], input=stdin, capture_output=True, text=True, timeout=120, check=False
        )
    except subprocess.TimeoutExpired:
        raise ConfigError(
            "The keychain did not answer in time. If a device-password dialog is open, confirm it and try again."
        ) from None
    except OSError:
        raise ConfigError("The keychain program could not be started.") from None


def _quote(value: str) -> str:
    if re.search(r'["\\\n\r]', value):
        raise ConfigError("The mailbox address or server contains characters the keychain cannot take.")
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


def _sec_get(account: str) -> Optional[str]:
    done = _run(["find-generic-password", "-s", SERVICE, "-a", account, "-w"])
    if done.returncode != 0:
        if done.returncode == 44 or "could not be found" in done.stderr:
            return None
        raise ConfigError("The keychain could not be read. Unlock it and try again.")
    raw = done.stdout.rstrip("\n")
    if raw.startswith(_PREFIX):
        try:
            return bytes.fromhex(raw[len(_PREFIX):]).decode("utf-8")
        except ValueError:
            raise ConfigError("The stored password is damaged. Enter it again.") from None
    password = _decode_legacy(raw)
    # An item made by another program (older `keyring` entry) asks for the device password on every
    # read by a new program. Re-save it once as our own item, then it never asks again.
    try:
        _sec_set(account, password)
    except ConfigError:
        pass
    return password


def _sec_delete(account: str) -> bool:
    done = _run(["delete-generic-password", "-s", SERVICE, "-a", account])
    if done.returncode == 0:
        return True
    if done.returncode == 44 or "could not be found" in done.stderr:
        return False
    raise ConfigError("The keychain refused the change. Unlock it and try again.")


def _sec_set(account: str, password: str) -> None:
    # Delete first: an item made by another program (older `keyring` entry) cannot be updated by
    # `security`, but it can be replaced. The password goes over stdin, never into the process list.
    _sec_delete(account)
    value = (_PREFIX + password.encode("utf-8").hex()).encode("ascii").hex()
    line = f"add-generic-password -s {_quote(SERVICE)} -a {_quote(account)} -X {value}\n"
    _run(["-i"], stdin=line)
    if _sec_get(account) != password:  # `security -i` hides failures in its exit code
        raise ConfigError("The keychain refused the password.")


def backend_name() -> str:
    if _use_security():
        return "macos.security"
    kr = _keyring()
    backend = kr.get_keyring()
    return f"{type(backend).__module__}.{type(backend).__name__}"


def _unavailable(exc: Exception) -> ConfigError:
    return ConfigError(
        "No system keychain is available here "
        f"({exc.__class__.__name__}). Set OUTBOX_SMTP_PASSWORD in the environment instead."
    )


def get_smtp_password(host: str, user: str) -> Optional[str]:
    """The stored password, or None when none is stored or no keychain exists."""
    if _use_security():
        with _LOCK:
            return _sec_get(account_name(host, user))
    kr = _keyring()
    try:
        return kr.get_password(SERVICE, account_name(host, user))
    except kr.errors.NoKeyringError:
        return None
    except kr.errors.KeyringError as exc:
        raise ConfigError(f"The keychain could not be read ({exc.__class__.__name__}). Unlock it and try again.") from None


def set_smtp_password(host: str, user: str, password: str) -> None:
    if not password:
        raise ConfigError("The password is empty.")
    if _use_security():
        with _LOCK:
            _sec_set(account_name(host, user), password)
        return
    kr = _keyring()
    try:
        kr.set_password(SERVICE, account_name(host, user), password)
    except kr.errors.NoKeyringError as exc:
        raise _unavailable(exc) from None
    except kr.errors.KeyringError as exc:
        raise ConfigError(f"The keychain refused the password ({exc.__class__.__name__}).") from None


def delete_smtp_password(host: str, user: str) -> bool:
    """True when an entry was removed, False when there was none."""
    if _use_security():
        with _LOCK:
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
        raise ConfigError(f"The keychain refused the change ({exc.__class__.__name__}).") from None
