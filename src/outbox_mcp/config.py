"""Configuration, read from environment variables (see .env.example)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional

from .errors import ConfigError, ValidationError
from .security.rate_limiter import RateLimits
from .security.validation import validate_address, validate_display_name

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
_SECURITY_MODES = ("starttls", "ssl", "none")
_DEFAULT_PORTS = {"starttls": 587, "ssl": 465}


@dataclass(frozen=True)
class Config:
    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_password: str = field(repr=False)
    smtp_security: str
    from_address: str
    recipients_file: Path
    db_path: Path
    from_name: str = ""
    smtp_helo: Optional[str] = None
    smtp_timeout_seconds: float = 30.0
    draft_ttl_seconds: int = 600
    draft_retention_days: int = 7
    max_prepares_per_hour: int = 20
    max_sends_per_hour: int = 3
    max_sends_per_day: int = 10
    max_subject_chars: int = 150
    max_body_chars: int = 20000

    @property
    def limits(self) -> RateLimits:
        return RateLimits(
            max_prepares_per_hour=self.max_prepares_per_hour,
            max_sends_per_hour=self.max_sends_per_hour,
            max_sends_per_day=self.max_sends_per_day,
        )

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "Config":
        e = os.environ if env is None else env

        def required(name: str) -> str:
            value = e.get(name, "").strip()
            if not value:
                raise ConfigError(f"Missing required setting {name}.")
            return value

        def number(name: str, default, kind=int):
            raw = e.get(name, "").strip()
            if not raw:
                return default
            try:
                value = kind(raw)
            except ValueError:
                raise ConfigError(f"Setting {name} must be a number.") from None
            if value <= 0:
                raise ConfigError(f"Setting {name} must be greater than zero.")
            return value

        security = e.get("OUTBOX_SMTP_SECURITY", "starttls").strip().lower()
        if security not in _SECURITY_MODES:
            raise ConfigError("OUTBOX_SMTP_SECURITY must be one of: starttls, ssl, none.")

        host = required("OUTBOX_SMTP_HOST")
        if security == "none" and host.lower() not in _LOCAL_HOSTS:
            raise ConfigError(
                "OUTBOX_SMTP_SECURITY=none is only allowed for localhost (for tests)."
            )

        if security == "none":
            default_port = None
        else:
            default_port = _DEFAULT_PORTS[security]
        port = number("OUTBOX_SMTP_PORT", default_port)
        if port is None:
            raise ConfigError("Missing required setting OUTBOX_SMTP_PORT.")

        if security == "none":
            user = e.get("OUTBOX_SMTP_USER", "").strip()
            password = e.get("OUTBOX_SMTP_PASSWORD", "")
        else:
            user = required("OUTBOX_SMTP_USER")
            password = required("OUTBOX_SMTP_PASSWORD")

        try:
            from_address = validate_address(required("OUTBOX_FROM_ADDRESS"))
            from_name = validate_display_name(e.get("OUTBOX_FROM_NAME", ""))
        except ValidationError as exc:
            raise ConfigError(f"Invalid sender setting: {exc}") from None

        config = cls(
            smtp_host=host,
            smtp_port=port,
            smtp_user=user,
            smtp_password=password,
            smtp_security=security,
            from_address=from_address,
            from_name=from_name,
            smtp_helo=(e.get("OUTBOX_SMTP_HELO", "").strip() or None),
            recipients_file=Path(e.get("OUTBOX_RECIPIENTS_FILE", "recipients.json")),
            db_path=Path(e.get("OUTBOX_DB_PATH", "outbox.db")),
            smtp_timeout_seconds=number("OUTBOX_SMTP_TIMEOUT_SECONDS", 30.0, float),
            draft_ttl_seconds=number("OUTBOX_DRAFT_TTL_SECONDS", 600),
            draft_retention_days=number("OUTBOX_DRAFT_RETENTION_DAYS", 7),
            max_prepares_per_hour=number("OUTBOX_MAX_PREPARES_PER_HOUR", 20),
            max_sends_per_hour=number("OUTBOX_MAX_SENDS_PER_HOUR", 3),
            max_sends_per_day=number("OUTBOX_MAX_SENDS_PER_DAY", 10),
            max_subject_chars=number("OUTBOX_MAX_SUBJECT_CHARS", 150),
            max_body_chars=number("OUTBOX_MAX_BODY_CHARS", 20000),
        )
        return config
