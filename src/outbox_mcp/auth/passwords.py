"""Password hashing for the single owner login (scrypt, from the standard library).

Stored format: ``scrypt:<n>:<r>:<p>:<salt>:<hash>`` with URL-safe base64 and no padding.
It contains no ``$``, spaces or quotes, so it survives ``.env`` parsing unquoted.

Create one with::

    python -m outbox_mcp.auth.passwords
"""

from __future__ import annotations

import base64
import getpass
import hashlib
import hmac
import re
import secrets
import sys

_PREFIX = "scrypt"
_MAX_N = 2**20
_MAXMEM = 256 * 1024 * 1024
_SALT_BYTES = 16
_HASH_BYTES = 32
_B64 = re.compile(r"[A-Za-z0-9_-]+")


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(text: str) -> bytes | None:
    if not _B64.fullmatch(text):
        return None
    try:
        return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except ValueError:
        return None


def _parse(stored: object):
    """Return (n, r, p, salt, digest) or None if the string is not a valid hash."""
    if not isinstance(stored, str):
        return None
    parts = stored.split(":")
    if len(parts) != 6 or parts[0] != _PREFIX:
        return None
    try:
        n, r, p = (int(x) for x in parts[1:4])
    except ValueError:
        return None
    if not (2 <= n <= _MAX_N and n & (n - 1) == 0 and 1 <= r <= 16 and 1 <= p <= 16):
        return None
    salt, digest = _unb64(parts[4]), _unb64(parts[5])
    if salt is None or digest is None:
        return None
    if len(salt) < _SALT_BYTES or len(digest) != _HASH_BYTES:
        return None
    return n, r, p, salt, digest


def _scrypt(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=_HASH_BYTES, maxmem=_MAXMEM
    )


def hash_password(password: str, *, n: int = 2**14, r: int = 8, p: int = 1) -> str:
    if not password:
        raise ValueError("The password must not be empty.")
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = _scrypt(password, salt, n, r, p)
    return f"{_PREFIX}:{n}:{r}:{p}:{_b64(salt)}:{_b64(digest)}"


def is_valid_hash(stored: object) -> bool:
    return _parse(stored) is not None


def verify_password(password: str, stored: object) -> bool:
    parsed = _parse(stored)
    if parsed is None or not password:
        return False
    n, r, p, salt, digest = parsed
    try:
        candidate = _scrypt(password, salt, n, r, p)
    except (ValueError, MemoryError):
        return False
    return hmac.compare_digest(candidate, digest)


def main() -> int:
    first = getpass.getpass("New owner password (min. 12 characters): ")
    if len(first) < 12:
        print("Too short.", file=sys.stderr)
        return 2
    if getpass.getpass("Repeat: ") != first:
        print("The two entries differ.", file=sys.stderr)
        return 2
    print("\nPut this line into your .env file:\n")
    print(f"OUTBOX_OAUTH_PASSWORD_HASH={hash_password(first)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
