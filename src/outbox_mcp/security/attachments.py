"""Which files may be attached to a mail. Everything here fails closed.

Only Markdown notes below a configured root folder, inside explicitly allowed folders, may be
attached. The checks run before anything is read, and one rejected file rejects the whole mail.
Error messages name the file the AI asked for, never its content.
"""

from __future__ import annotations

import errno
import hashlib
import os
import re
import stat
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple

from ..errors import AttachmentRejected, ConfigError

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
    source: str = ""  # the path it was read from, to check the rules again when sending

    @property
    def size(self) -> int:
        return len(self.data)


def text_problem(data: bytes) -> Optional[str]:
    """Why these bytes cannot be attached as text (not UTF-8, NUL bytes, looks like a secret),
    or None if they are fine. Used for files read from a folder and for uploaded copies alike."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return "it is not valid UTF-8 text"
    if "\x00" in text:
        return "it is not a text file (contains NUL bytes)"
    if _LABELLED_VALUE.search(text) or any(pattern.search(text) for pattern in _SECRET_PATTERNS):
        return "it looks like it contains a secret (a password, key or token)"
    return None


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


def is_sensitive_name(name: str) -> bool:
    """True if a file or folder name looks sensitive (a fixed protection that no rule can lift)."""
    normalised = _norm(name)
    return any(bad in normalised for bad in _SENSITIVE_NAME_PARTS)


def path_key(path: str) -> str:
    """The normalised form of a relative path (NFC, case-folded, parts joined with '/'), used to
    compare paths and to store rules. The comparison is component by component."""
    return "/".join(_parts(path))


def key_parts(key: str) -> Tuple[str, ...]:
    return tuple(p for p in key.split("/") if p)


class AttachmentPolicy:
    def __init__(
        self,
        root: Path,
        allowed_folders: Sequence[str],
        denied_folders: Sequence[str] = (),
        max_files: int = 3,
        max_file_bytes: int = 200_000,
        max_total_bytes: int = 500_000,
        rules: Optional[Callable[[], Tuple[Sequence[Tuple[str, ...]], Sequence[Tuple[str, ...]]]]] = None,
    ) -> None:
        if not (hasattr(os, "O_NOFOLLOW") and hasattr(os, "O_DIRECTORY") and os.open in os.supports_dir_fd):
            raise ConfigError(
                "Attachments need an operating system with O_NOFOLLOW, O_DIRECTORY and dir_fd "
                "support (Linux, macOS). They are refused here instead of running less safely."
            )
        self.root = Path(root)
        self._static_allowed = [_parts(f) for f in allowed_folders if _parts(f)]
        self._static_denied = [_parts(f) for f in denied_folders if _parts(f)]
        # With a rules source the lists are read again for every check, so a change made in the
        # database applies at once. Each entry is already normalised (see path_key).
        self._rules = rules
        self.max_files = max_files
        self.max_file_bytes = max_file_bytes
        self.max_total_bytes = max_total_bytes

    @property
    def allowed(self) -> List[Tuple[str, ...]]:
        return list(self._rules()[0]) if self._rules else self._static_allowed

    @property
    def denied(self) -> List[Tuple[str, ...]]:
        return list(self._rules()[1]) if self._rules else self._static_denied

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

    def still_allowed(self, path: str) -> None:
        """Check a path again against the rules as they are now. Looks at the path text only and
        touches nothing on disk, so it can run right before sending. Raises AttachmentRejected."""
        self._check_path(path)

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
        allowed, denied = (self._rules() if self._rules else (self._static_allowed, self._static_denied))
        if not any(_starts_with(normalised, a) for a in allowed):
            raise refuse("it is not in an allowed folder")
        if any(_starts_with(normalised, d) for d in denied):
            raise refuse("it is in a blocked folder")
        if any(is_sensitive_name(part) for part in parts):
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
        fd = _open_below(base, parts, refuse)
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
        problem = text_problem(data)
        if problem:
            raise refuse(problem)
        return Attachment(
            name=safe_filename(parts[-1]), data=data, sha256=hashlib.sha256(data).hexdigest(),
            source=path,
        )


def _open_dir(base: Path):
    return os.open(base, os.O_RDONLY | os.O_DIRECTORY)


def _open_below(base: Path, parts: Tuple[str, ...], refuse) -> int:
    """Open the file by walking down from `base` one component at a time. Every step uses
    O_NOFOLLOW relative to the directory opened before it, so a folder swapped for a symlink
    after the earlier checks is still refused (no check-then-use gap)."""
    nofollow = os.O_NOFOLLOW
    dir_fd = _open_dir(base)
    try:
        for part in parts[:-1]:
            try:
                next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | nofollow, dir_fd=dir_fd)
            except OSError as exc:
                raise _open_error(exc, refuse) from None
            os.close(dir_fd)
            dir_fd = next_fd
        try:
            # O_NONBLOCK: opening a named pipe must not wait for a writer; fstat then refuses it.
            return os.open(parts[-1], os.O_RDONLY | nofollow | os.O_NONBLOCK, dir_fd=dir_fd)
        except OSError as exc:
            raise _open_error(exc, refuse) from None
    finally:
        os.close(dir_fd)


def _open_error(exc: OSError, refuse) -> AttachmentRejected:
    if exc.errno in (errno.ELOOP, errno.ENOTDIR):
        return refuse("it is or contains a symbolic link")
    if exc.errno == errno.ENOENT:
        return refuse("the file does not exist")
    return refuse("it cannot be opened")


def _show(path: str) -> str:
    """The path as shown in an error: printable and short."""
    cleaned = "".join(c if c.isprintable() else "?" for c in path)
    return cleaned if len(cleaned) <= 200 else cleaned[:200] + "..."
