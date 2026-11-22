"""Configuration of the admin process. It needs the database and a passphrase hash, nothing else:
no mail credentials and no OAuth data, so a problem here cannot reach the mail service."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional, Tuple

from ..auth.passwords import is_valid_hash
from ..errors import ConfigError
from ..paths import default_file

DEFAULT_HOSTS = ("localhost", "127.0.0.1", "[::1]")


@dataclass(frozen=True)
class AdminConfig:
    db_path: Path
    passphrase_hash: str
    allowed_hosts: Tuple[str, ...] = DEFAULT_HOSTS
    allowed_origins: Tuple[str, ...] = ()
    idle_seconds: int = 20 * 60
    max_session_seconds: int = 4 * 60 * 60
    max_failed_logins: int = 5
    lockout_seconds: int = 15 * 60
    max_body_bytes: int = 16 * 1024
    max_apply_bytes: int = 16 * 1024 * 1024  # the upload route only (files of one pass)
    attachment_root: Optional[Path] = None
    confirmation_seconds: int = 120

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "AdminConfig":
        e = os.environ if env is None else env
        db = e.get("OUTBOX_DB_PATH", "").strip()
        if not db:
            if not (e.get("OUTBOX_DATA_DIR") or "").strip():
                raise ConfigError("Missing required setting OUTBOX_DB_PATH (or OUTBOX_DATA_DIR).")
            db = str(default_file(e, "outbox.db"))  # the same file the mail server uses
        stored = e.get("OUTBOX_ADMIN_PASSPHRASE_HASH", "").strip()
        if not is_valid_hash(stored):
            raise ConfigError(
                "OUTBOX_ADMIN_PASSPHRASE_HASH is missing or not a valid hash "
                "(create one with: python -m outbox_mcp.admin.passphrase)."
            )

        def items(name: str, default: Tuple[str, ...]) -> Tuple[str, ...]:
            raw = e.get(name, "")
            found = tuple(part.strip() for part in raw.split(",") if part.strip())
            return found or default

        origins = items("OUTBOX_ADMIN_ALLOWED_ORIGINS", ())
        for origin in origins:
            if "*" in origin or origin.endswith("/"):
                raise ConfigError("OUTBOX_ADMIN_ALLOWED_ORIGINS needs exact origins (no wildcard, no trailing slash).")
        root = e.get("OUTBOX_ADMIN_ATTACHMENT_ROOT", "").strip()
        if root and not Path(root).is_absolute():
            raise ConfigError("OUTBOX_ADMIN_ATTACHMENT_ROOT must be an absolute path.")
        return cls(
            attachment_root=Path(root) if root else None,
            db_path=Path(db),
            passphrase_hash=stored,
            allowed_hosts=tuple(h.lower() for h in items("OUTBOX_ADMIN_ALLOWED_HOSTS", DEFAULT_HOSTS)),
            allowed_origins=origins,
        )
