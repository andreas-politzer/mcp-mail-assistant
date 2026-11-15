"""Pure rules for file sources (no database, no disk access).

A source is a place Claude may attach files from. Two kinds exist:

* ``server_folder``: a folder on the machine the mail service runs on (today: the notes folder).
* ``upload``: copies of files that the admin app uploaded; the server never reads a path of the
  uploading machine, it only stores relative paths and contents.

Every source has its own rules. Rules of one source never apply to another source: an upload
source has no absolute path, so two sources cannot overlap on disk. A block (deny) always wins
over an allow inside its source.
"""

from __future__ import annotations

import hashlib
import unicodedata
from typing import Optional, Sequence, Tuple

from .errors import SourceRejected
from .security.attachments import is_sensitive_name, key_parts, path_key

SOURCE_KINDS = ("server_folder", "upload")
SOURCE_SCOPES = ("folder", "file")

# Limits for version 1, enforced inside the database transaction that stores a file.
MAX_ACTIVE_SOURCES = 20
MAX_FILES_PER_SOURCE = 500
MAX_FILE_BYTES = 200_000
MAX_TOTAL_BYTES = 50_000_000

MAX_LABEL_CHARS = 60
MAX_DISPLAY_CHARS = 200

# The notes folder stays Markdown only; uploaded sources may also hold plain text.
ALLOWED_SUFFIXES = {"server_folder": (".md",), "upload": (".md", ".txt")}
_ALL_SUFFIXES = (".md", ".txt")


def _has_control(text: str) -> bool:
    return any(ord(c) < 32 or ord(c) == 127 for c in text)


def clean_label(label: object) -> Tuple[str, str]:
    """(display, key) of a source label. Claude names a file as `label:relative/path`, so the
    label contains neither ':' nor '/'. The key makes the label unique regardless of case."""
    text = label.strip() if isinstance(label, str) else ""
    if not text or len(text) > MAX_LABEL_CHARS:
        raise SourceRejected("input", f"A source needs a name of 1 to {MAX_LABEL_CHARS} characters.")
    if _has_control(text) or any(c in text for c in ":/\\"):
        raise SourceRejected("input", "A source name must not contain control characters, ':', '/' or '\\'.")
    return text, unicodedata.normalize("NFC", unicodedata.normalize("NFC", text).casefold())


def clean_display(text: object) -> str:
    """Free text shown next to a source (where it came from). Never used to access anything."""
    if text is None:
        return ""
    if not isinstance(text, str):
        raise SourceRejected("input", "The origin text must be text.")
    cleaned = "".join(c for c in text if not (ord(c) < 32 or ord(c) == 127)).strip()
    return cleaned[:MAX_DISPLAY_CHARS]


def clean_relative_path(path: object) -> Tuple[str, str]:
    """(display, key) of a clean relative path inside a source: no absolute or ~ path, no empty,
    '.' or '..' part, no backslash, no hidden part."""
    text = path.strip() if isinstance(path, str) else ""
    parts = text.split("/") if text else []
    if (
        not text or text.startswith(("/", "~")) or text.endswith("/") or "\\" in text or _has_control(text)
        or any(p in ("", ".", "..") for p in parts)
    ):
        raise SourceRejected("permission", "Give a clean path relative to the source.")
    if any(p.startswith(".") for p in parts):
        raise SourceRejected("permission", "Hidden files and folders are excluded.")
    return text, path_key(text)


def check_stored_name(kind: str, display_path: str) -> None:
    """Name and type checks for a file inside a source of this kind (path already clean)."""
    parts = display_path.split("/")
    if any(is_sensitive_name(p) for p in parts):
        raise SourceRejected("permission", f"'{display_path}' is not allowed: the name looks sensitive.")
    suffixes = ALLOWED_SUFFIXES[kind]
    if not parts[-1].lower().endswith(suffixes):
        raise SourceRejected(
            "permission", f"'{display_path}' is not allowed: only {', '.join(suffixes)} files can be stored."
        )


def looks_like_file(key: str) -> bool:
    """True if a rule path names a file (a file can only be blocked, never allowed)."""
    return key.endswith(_ALL_SUFFIXES)


def evaluate(
    scope: str,
    file_key: Optional[str],
    allowed: Sequence[Tuple[str, ...]],
    denied: Sequence[Tuple[str, ...]],
    key: str,
) -> str:
    """'denied', 'allowed' or 'none' for a normalised path key inside one source.

    A block at or above the path wins over everything. An empty rule path means the whole source
    (only an upload folder source can hold one). In a folder source a path is allowed if
    an allowed folder lies above it. In a single-file source only the registered file is allowed
    (its registration is the allowance); everything else is 'none'."""
    parts = key_parts(key)
    if any(parts[: len(d)] == d for d in denied):
        return "denied"
    if scope == "file":
        return "allowed" if file_key is not None and key == file_key else "none"
    if any(parts[: len(a)] == a for a in allowed):
        return "allowed"
    return "none"


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def short_version(sha256: str) -> str:
    """The short version id shown to people (first 12 hex characters of the SHA-256)."""
    return sha256[:12]


def new_source_id(token: str) -> str:
    return f"src_{token}"
