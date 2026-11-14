"""Which files may be attached to a mail. Everything here fails closed.

Only Markdown notes below a configured root folder, inside explicitly allowed folders, may be
attached. The checks run before anything is read, and one rejected file rejects the whole mail.
Error messages name the file the AI asked for, never its content.
"""

from __future__ import annotations

import hashlib
import os
import re
import stat
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import List, Sequence, Tuple

from ..errors import AttachmentRejected

_SENSITIVE_NAME_PARTS = (
    "passw", "kennwort", "secret", "geheim", "token", "credential", "api_key", "api-key", "apikey",
    "private key", ".env", ".pem",
)

_SECRET_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}"),
    re.compile(
        r"\b(?:password|passwort|passwd|pwd|kennwort|secret|api[_-]?key|token)\b\s*[:=]\s*[\"']?[^\s\"']{8,}",
        re.IGNORECASE,
    ),
)

# A label like "Passwort:" followed by exactly one word until the end of the line is refused
# whatever its length. Prose after the label ("Passwort: steht im Manager") is not.
_LABELLED_VALUE = re.compile(
    r"\b(?:password|passwort|passwd|pwd|kennwort|secret|api[_-]?key|api-schl\w*|token)\b"
    r"\*{0,2}[ \t]*[:=]\*{0,2}[ \t]*[\"'`]?[^\s\"'`(]+[\"'`]?[ \t]*$",
    re.IGNORECASE | re.MULTILINE,
)

_MAX_NAME_CHARS = 120


@dataclass(frozen=True)
class Attachment:
    name: str
    data: bytes
    sha256: str

    @property
    def size(self) -> int:
        return len(self.data)


def safe_filename(name: str) -> str:
    """A file name that cannot break out of a mail header or confuse a mail program."""
    cleaned = "".join("_" if (ord(c) < 32 or ord(c) == 127 or c in '\\/:"<>|?*') else c for c in name).strip()
    if len(cleaned) > _MAX_NAME_CHARS:
        stem, dot, ext = cleaned.rpartition(".")
        ext = dot + ext if dot and len(ext) < 10 else ""
        cleaned = cleaned[: _MAX_NAME_CHARS - len(ext)] + ext
    return cleaned or "attachment.md"


def _norm(text: str) -> str:
    return unicodedata.normalize("NFC", unicodedata.normalize("NFC", text).casefold())


def _parts(path: str) -> Tuple[str, ...]:
    return tuple(_norm(p) for p in path.split("/") if p)


def _starts_with(parts: Tuple[str, ...], prefix: Tuple[str, ...]) -> bool:
    return bool(prefix) and parts[: len(prefix)] == prefix


class AttachmentPolicy:
    def __init__(
        self,
        root: Path,
        allowed_folders: Sequence[str],
        denied_folders: Sequence[str] = (),
        max_files: int = 3,
        max_file_bytes: int = 200_000,
        max_total_bytes: int = 500_000,
    ) -> None:
        self.root = Path(root)
        self.allowed = [_parts(f) for f in allowed_folders if _parts(f)]
        self.denied = [_parts(f) for f in denied_folders if _parts(f)]
        self.max_files = max_files
        self.max_file_bytes = max_file_bytes
        self.max_total_bytes = max_total_bytes

    # -- public -----------------------------------------------------------------

    def load(self, paths: Sequence[str]) -> List[Attachment]:
        if isinstance(paths, (str, bytes)) or not isinstance(paths, (list, tuple)):
            raise AttachmentRejected("The attachments must be a list of file paths.")
        if len(paths) > self.max_files:
            raise AttachmentRejected(f"At most {self.max_files} attachments are allowed per mail.")
        seen = set()
        result: List[Attachment] = []
        total = 0
        for path in paths:
            if not isinstance(path, str):
                raise AttachmentRejected("Every attachment must be given as a file path (text).")
            parts = self._check_path(path)
            key = "/".join(_norm(p) for p in parts)
            if key in seen:
                raise AttachmentRejected(f"Attachment '{path}' is listed twice.")
            seen.add(key)
            attachment = self._read(path, parts)
            total += attachment.size
            if total > self.max_total_bytes:
                raise AttachmentRejected(
                    f"The total size of the attachments is too large (limit {self.max_total_bytes} bytes)."
                )
            result.append(attachment)
        return result

    # -- checks on the path text (nothing touched on disk yet) --------------------

    @staticmethod
    def _refuse(path: str, reason: str) -> AttachmentRejected:
        return AttachmentRejected(f"Attachment '{_show(path)}' is not allowed: {reason}.")

    def _check_path(self, path: str) -> Tuple[str, ...]:
        refuse = lambda reason: self._refuse(path, reason)  # noqa: E731
        if not path.strip():
            raise refuse("the path is empty")
        if any(ord(c) < 32 or ord(c) == 127 for c in path) or "\\" in path:
            raise refuse("the path contains forbidden characters")
        if path.startswith(("/", "~")) or path.endswith("/"):
            raise refuse("give a path relative to the notes folder, without leading or trailing slash")
        parts = tuple(path.split("/"))
        if any(p in ("", ".", "..") for p in parts):
            raise refuse("the path must not contain empty, '.' or '..' parts")
        if any(p.startswith(".") for p in parts):
            raise refuse("hidden files and folders are excluded")
        normalised = tuple(_norm(p) for p in parts)
        if not any(_starts_with(normalised, a) for a in self.allowed):
            raise refuse("it is not in an allowed folder")
        if any(_starts_with(normalised, d) for d in self.denied):
            raise refuse("it is in a blocked folder")
        if any(bad in part for part in normalised for bad in _SENSITIVE_NAME_PARTS):
            raise refuse("the name looks sensitive")
        if not parts[-1].lower().endswith(".md"):
            raise refuse("only .md (Markdown) files can be attached")
        return parts

    # -- checks on the file itself ------------------------------------------------

    def _read(self, path: str, parts: Tuple[str, ...]) -> Attachment:
        refuse = lambda reason: self._refuse(path, reason)  # noqa: E731
        if not self.root.is_dir():
            raise AttachmentRejected("The folder for attachments is not available on the server.")
        base = self.root.resolve()
        current = base
        for part in parts:
            current = current / part
            if current.is_symlink():
                raise refuse("it is or contains a symbolic link")
            if not os.path.lexists(current):
                raise refuse("the file does not exist")
        try:
            current.resolve().relative_to(base)
        except ValueError:
            raise refuse("it lies outside the notes folder") from None
        try:
            fd = os.open(current, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        except OSError:
            raise refuse("it cannot be opened") from None
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise refuse("it is not a regular file")
            if info.st_size > self.max_file_bytes:
                raise AttachmentRejected(
                    f"Attachment '{_show(path)}' is too large (limit {self.max_file_bytes} bytes)."
                )
            with os.fdopen(fd, "rb", closefd=False) as handle:
                data = handle.read(self.max_file_bytes + 1)
        finally:
            os.close(fd)
        if len(data) > self.max_file_bytes:
            raise AttachmentRejected(
                f"Attachment '{_show(path)}' is too large (limit {self.max_file_bytes} bytes)."
            )
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            raise refuse("it is not valid UTF-8 text") from None
        if "\x00" in text:
            raise refuse("it is not a text file (contains NUL bytes)")
        if _LABELLED_VALUE.search(text) or any(pattern.search(text) for pattern in _SECRET_PATTERNS):
            raise refuse("it looks like it contains a secret (a password, key or token)")
        return Attachment(
            name=safe_filename(parts[-1]), data=data, sha256=hashlib.sha256(data).hexdigest()
        )


def _show(path: str) -> str:
    """The path as shown in an error: printable and short."""
    cleaned = "".join(c if c.isprintable() else "?" for c in path)
    return cleaned if len(cleaned) <= 200 else cleaned[:200] + "..."
