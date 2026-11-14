"""Comparison of resource identifiers (the address of the protected MCP endpoint)."""

from __future__ import annotations

from urllib.parse import urlsplit


def normalize_resource(value: object) -> str | None:
    """Lower-case scheme and host, drop one trailing slash. None for anything unusable."""
    if not isinstance(value, str) or not value:
        return None
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.netloc or parts.query or parts.fragment:
        return None
    return f"{parts.scheme.lower()}://{parts.netloc.lower()}{parts.path.rstrip('/')}"


def same_resource(a: object, b: object) -> bool:
    na, nb = normalize_resource(a), normalize_resource(b)
    return na is not None and na == nb
