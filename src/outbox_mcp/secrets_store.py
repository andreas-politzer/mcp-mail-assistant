"""The SMTP password in the system keychain (macOS Keychain, Windows Credential Manager,
Secret Service on Linux), reached through the `keyring` library.

Nothing here writes the password to a file. The environment variable OUTBOX_SMTP_PASSWORD
still works for servers without a keychain; the keychain is the way for a desktop account.
"""

from __future__ import annotations

from typing import Optional

from .errors import ConfigError

SERVICE = "mail-assistant"


def account_name(host: str, user: str) -> str:
    return f"smtp:{user}@{host}"


def _keyring():
    try:
        import keyring
        import keyring.errors
    except ImportError:  # pragma: no cover - keyring is a declared dependency
        raise ConfigError("The keychain library (keyring) is not installed.") from None
    return keyring


def backend_name() -> str:
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
    kr = _keyring()
    try:
        kr.set_password(SERVICE, account_name(host, user), password)
    except kr.errors.NoKeyringError as exc:
        raise _unavailable(exc) from None
    except kr.errors.KeyringError as exc:
        raise ConfigError(f"The keychain refused the password ({exc.__class__.__name__}).") from None


def delete_smtp_password(host: str, user: str) -> bool:
    """True when an entry was removed, False when there was none."""
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
