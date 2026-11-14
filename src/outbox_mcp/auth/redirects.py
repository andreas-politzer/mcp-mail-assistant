"""Redirect URIs are compared as exact strings. No wildcards, no prefixes, no patterns."""

from __future__ import annotations

from typing import Iterable
from urllib.parse import urlsplit

from ..errors import ConfigError

# The only callback that Claude's documentation names for claude.ai, Desktop, mobile and Cowork.
CLAUDE_AI_CALLBACK = "https://claude.ai/api/mcp/auth_callback"


def is_allowed_redirect_uri(uri: object, allowed: Iterable[str]) -> bool:
    return isinstance(uri, str) and uri != "" and any(uri == entry for entry in allowed)


def parse_allowlist(text: str) -> tuple[str, ...]:
    """Parse a comma-separated allowlist. Refuses anything that is not a plain https address."""
    entries: list[str] = []
    for raw in text.split(","):
        entry = raw.strip()
        if not entry:
            continue
        _check_entry(entry)
        if entry not in entries:
            entries.append(entry)
    if not entries:
        raise ConfigError("The redirect URI list is empty.")
    return tuple(entries)


def _check_entry(entry: str) -> None:
    if "*" in entry or any(ch.isspace() for ch in entry):
        raise ConfigError(f"Redirect URI {entry!r} must be exact: no wildcards or spaces.")
    parts = urlsplit(entry)
    if parts.scheme != "https" or not parts.hostname:
        raise ConfigError(f"Redirect URI {entry!r} must be an https address.")
    if "@" in parts.netloc:
        raise ConfigError(f"Redirect URI {entry!r} must not contain credentials.")
    if parts.query or parts.fragment:
        raise ConfigError(f"Redirect URI {entry!r} must not contain a query or fragment.")
    if parts.path in ("", "/"):
        raise ConfigError(f"Redirect URI {entry!r} needs a path.")
