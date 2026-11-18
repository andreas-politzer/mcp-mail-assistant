"""OAuth settings, read from environment variables (see .env.example)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional
from urllib.parse import urlsplit

from ..errors import ConfigError
from ..paths import default_file
from .passwords import is_valid_hash
from .redirects import CLAUDE_AI_CALLBACK, parse_allowlist

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


@dataclass(frozen=True)
class AuthConfig:
    base_url: str
    password_hash: str = field(repr=False)
    db_path: Path
    redirect_uris: tuple = (CLAUDE_AI_CALLBACK,)
    scopes: tuple = ("mail:send",)
    mcp_path: str = "/mcp"
    access_token_ttl: int = 3600
    refresh_token_ttl: int = 90 * 86400
    auth_code_ttl: int = 120
    pending_ttl: int = 600
    max_attempts_per_request: int = 3
    max_login_failures: int = 5
    login_lockout_seconds: int = 900
    max_clients: int = 20
    client_prune_seconds: int = 3600
    max_pending_requests: int = 20
    max_parallel_password_checks: int = 2
    max_login_body: int = 8192

    def __post_init__(self):
        object.__setattr__(self, "base_url", _clean_base_url(self.base_url))
        object.__setattr__(self, "db_path", Path(self.db_path))

    @property
    def issuer_url(self) -> str:
        return self.base_url

    @property
    def resource_url(self) -> str:
        return self.base_url + self.mcp_path

    @property
    def login_url(self) -> str:
        return self.base_url + "/login"

    @property
    def host(self) -> str:
        return urlsplit(self.base_url).hostname or ""

    @property
    def secure_cookies(self) -> bool:
        return self.base_url.startswith("https://")

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "AuthConfig":
        e = os.environ if env is None else env

        def required(name: str) -> str:
            value = e.get(name, "").strip()
            if not value:
                raise ConfigError(f"Missing required setting {name}.")
            return value

        def positive(name: str, default: int) -> int:
            raw = e.get(name, "").strip()
            if not raw:
                return default
            try:
                value = int(raw)
            except ValueError:
                raise ConfigError(f"{name} must be a whole number.") from None
            if value <= 0:
                raise ConfigError(f"{name} must be greater than zero.")
            return value

        password_hash = required("OUTBOX_OAUTH_PASSWORD_HASH")
        if not is_valid_hash(password_hash):
            raise ConfigError(
                "OUTBOX_OAUTH_PASSWORD_HASH is not a valid hash. "
                "Create one with: python -m outbox_mcp.auth.passwords"
            )
        redirects = e.get("OUTBOX_OAUTH_REDIRECT_URIS", "").strip()
        return cls(
            base_url=required("OUTBOX_PUBLIC_BASE_URL"),
            password_hash=password_hash,
            db_path=Path(e.get("OUTBOX_OAUTH_DB_PATH", "").strip() or default_file(e, "auth.db")),
            redirect_uris=parse_allowlist(redirects) if redirects else (CLAUDE_AI_CALLBACK,),
            access_token_ttl=positive("OUTBOX_OAUTH_ACCESS_TTL_SECONDS", 3600),
            refresh_token_ttl=positive("OUTBOX_OAUTH_REFRESH_TTL_DAYS", 90) * 86400,
        )


def _clean_base_url(value: str) -> str:
    parts = urlsplit(value)
    host = parts.hostname or ""
    local = host in _LOCAL_HOSTS
    if parts.scheme not in ("https", "http") or not host:
        raise ConfigError("OUTBOX_PUBLIC_BASE_URL must start with https://")
    if parts.scheme == "http" and not local:
        raise ConfigError("OUTBOX_PUBLIC_BASE_URL must use https (http is only allowed for localhost).")
    if parts.query or parts.fragment or parts.path not in ("", "/") or "@" in parts.netloc:
        raise ConfigError("OUTBOX_PUBLIC_BASE_URL must be a bare address like https://mail.example.org")
    return f"{parts.scheme}://{parts.netloc}"
