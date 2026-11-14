"""Strict checks for everything that ends up in a mail header or body."""

from __future__ import annotations

import re

from ..errors import ValidationError

# Control characters and Unicode line separators are never allowed in single-line fields.
_SINGLE_LINE_FORBIDDEN = re.compile(r"[\x00-\x1f\x7f\x85  ]")
# In the body only tab and newlines are allowed.
_BODY_FORBIDDEN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

_DOMAIN_LABEL = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
_ADDRESS_RE = re.compile(
    r"^[A-Za-z0-9!#$%&'*+/=?^_`{|}~.-]+@" + _DOMAIN_LABEL + r"(?:\." + _DOMAIN_LABEL + r")+$"
)


def validate_subject(value: object, max_chars: int) -> str:
    if not isinstance(value, str):
        raise ValidationError("The subject must be text.")
    subject = value.strip()
    if not subject:
        raise ValidationError("The subject must not be empty.")
    if _SINGLE_LINE_FORBIDDEN.search(subject):
        raise ValidationError("The subject must be a single line without control characters.")
    if len(subject) > max_chars:
        raise ValidationError(f"The subject is too long (maximum {max_chars} characters).")
    return subject


def validate_body(value: object, max_chars: int) -> str:
    if not isinstance(value, str):
        raise ValidationError("The text must be text.")
    body = value.replace("\r\n", "\n").replace("\r", "\n")
    if not body.strip():
        raise ValidationError("The text must not be empty.")
    if _BODY_FORBIDDEN.search(body):
        raise ValidationError("The text contains control characters that are not allowed.")
    if len(body) > max_chars:
        raise ValidationError(f"The text is too long (maximum {max_chars} characters).")
    return body


def validate_display_name(value: object, max_chars: int = 100) -> str:
    if not isinstance(value, str):
        raise ValidationError("The name must be text.")
    name = value.strip()
    if _SINGLE_LINE_FORBIDDEN.search(name):
        raise ValidationError("The name must be a single line without control characters.")
    if len(name) > max_chars:
        raise ValidationError(f"The name is too long (maximum {max_chars} characters).")
    return name


def validate_address(value: object) -> str:
    """Accept only a plain address like name@example.com (no names, no lists, no comments)."""
    if not isinstance(value, str):
        raise ValidationError("The address must be text.")
    address = value.strip()
    if len(address) > 254 or not _ADDRESS_RE.match(address):
        raise ValidationError("The address is not a valid plain e-mail address.")
    local, _, _domain = address.partition("@")
    if len(local) > 64 or local.startswith(".") or local.endswith(".") or ".." in local:
        raise ValidationError("The address is not a valid plain e-mail address.")
    return address
