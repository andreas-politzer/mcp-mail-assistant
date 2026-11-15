"""The folder tree for the admin app: what exists below the root folder, and what the rules say about it.

Everything here works on real directories below the root, never follows a symbolic link and skips
hidden entries, the same as the attachment policy does. The decision is the same as in the policy:
a path is allowed if a rule allows it or one of its folders, and no rule blocks it or one of its folders.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from ..errors import ValidationError
from ..security.attachments import is_sensitive_name, key_parts, path_key
from ..storage.database import Database

MAX_ENTRIES = 500
MAX_COUNTED_FILES = 5000


@dataclass(frozen=True)
class Rule:
    parts: Tuple[str, ...]
    effect: str  # allow | deny
    display: str


def load_rules(db: Database, root: Path) -> List[Rule]:
    return [
        Rule(key_parts(r["path_key"]), r["effect"], r["path_display"])
        for r in db.file_rules("attach", root)
        if r["effect"] in ("allow", "deny")
    ]


def fingerprint(rules: Sequence[Rule]) -> str:
    """Identifies the whole rule state; a confirmation is only valid for the state it was issued for."""
    blob = json.dumps(sorted((r.parts, r.effect) for r in rules), separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _under(parts: Tuple[str, ...], prefix: Tuple[str, ...]) -> bool:
    return bool(prefix) and parts[: len(prefix)] == prefix


def evaluate(parts: Tuple[str, ...], rules: Sequence[Rule]) -> Tuple[str, Optional[Rule]]:
    """('denied', rule) | ('allowed', rule) | ('none', None) for normalised path parts. A block anywhere
    above or at the path wins over any allow."""
    denies = [r for r in rules if r.effect == "deny" and _under(parts, r.parts)]
    if denies:
        return "denied", min(denies, key=lambda r: len(r.parts))
    allows = [r for r in rules if r.effect == "allow" and _under(parts, r.parts)]
    if allows:
        return "allowed", max(allows, key=lambda r: len(r.parts))
    return "none", None


def clean_relative(path: str, allow_empty: bool = False) -> str:
    text = path.strip() if isinstance(path, str) else ""
    if not text:
        if allow_empty:
            return ""
        raise ValidationError("A path is needed.")
    parts = text.split("/")
    if (
        text.startswith(("/", "~")) or text.endswith("/") or "\\" in text
        or any(ord(c) < 32 or ord(c) == 127 for c in text)
        or any(p in ("", ".", "..") for p in parts)
    ):
        raise ValidationError("Give a clean path relative to the root folder.")
    if any(p.startswith(".") for p in parts):
        raise ValidationError("Hidden files and folders are excluded.")
    return text


_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
MAX_DEPTH = 32
MAX_VISITED = 20000


def _open_root(root: Path) -> int:
    try:
        return os.open(root, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0))
    except OSError:
        raise ValidationError("The root folder cannot be opened.") from None


def _open_child(fd: int, name: str) -> int:
    """Open a sub-folder relative to an open folder, refusing symbolic links. Because every step
    goes through the descriptor of the folder above, nothing can be swapped in between the check
    and the use: a link that appears later is refused here, and nothing outside the root is reached."""
    try:
        return os.open(name, _DIR_FLAGS, dir_fd=fd)
    except OSError:
        raise ValidationError("No such folder or file.") from None


def _open_folder(root: Path, rel: str) -> int:
    fd = _open_root(root)
    try:
        for part in rel.split("/") if rel else []:
            nxt = _open_child(fd, part)
            os.close(fd)
            fd = nxt
        return fd
    except BaseException:
        os.close(fd)
        raise


def _children_fd(fd: int) -> List[Tuple[str, str]]:
    found: List[Tuple[str, str]] = []
    try:
        with os.scandir(fd) as it:
            for entry in it:
                if entry.name.startswith("."):
                    continue
                try:
                    if entry.is_symlink():
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        found.append((entry.name, "folder"))
                    elif entry.is_file(follow_symlinks=False) and entry.name.lower().endswith(".md"):
                        found.append((entry.name, "file"))
                except OSError:
                    continue
    except OSError:
        raise ValidationError("This folder cannot be read.") from None
    return sorted(found, key=lambda item: (item[1] != "folder", item[0].casefold(), item[0]))


def real_entry(root: Path, rel: str) -> str:
    """'folder' or 'file' for a relative path that exists below the root. Neither the entry nor a
    folder above it may be a symbolic link (checked through open descriptors, not through paths)."""
    if not rel:
        os.close(_open_root(root))
        return "folder"
    parent, _, name = rel.rpartition("/")
    fd = _open_folder(root, parent)
    try:
        try:
            info = os.stat(name, dir_fd=fd, follow_symlinks=False)
        except OSError:
            raise ValidationError("No such folder or file.") from None
    finally:
        os.close(fd)
    if stat.S_ISLNK(info.st_mode):
        raise ValidationError("Symbolic links are not part of the tree.")
    if stat.S_ISDIR(info.st_mode):
        return "folder"
    if stat.S_ISREG(info.st_mode) and name.lower().endswith(".md"):
        return "file"
    raise ValidationError("No such folder or file.")


def describe(rel: str, kind: str, rules: Sequence[Rule]) -> Dict:
    parts = key_parts(path_key(rel)) if rel else ()
    state, rule = evaluate(parts, rules)
    own = next((r.effect for r in rules if r.parts == parts), None)
    return {
        "path": rel,
        "name": rel.rsplit("/", 1)[-1] if rel else "",
        "kind": kind,
        "state": state,
        "source": rule.display if rule else None,
        "inherited": bool(rule and rule.parts != parts),
        "own_rule": own,
        "sensitive": any(is_sensitive_name(p) for p in rel.split("/")) if rel else False,
    }


def list_folder(root: Path, rel: str, rules: Sequence[Rule]) -> Dict:
    rel = clean_relative(rel, allow_empty=True)
    folder, kind = real_entry(root, rel)
    if kind != "folder":
        raise ValidationError("This is a file, not a folder.")
    children = _children(folder)
    names: Dict[str, int] = {}
    for name, _ in children:
        names[path_key(name)] = names.get(path_key(name), 0) + 1
    entries = []
    for name, child_kind in children[:MAX_ENTRIES]:
        child_rel = f"{rel}/{name}" if rel else name
        item = describe(child_rel, child_kind, rules)
        item["ambiguous"] = names[path_key(name)] > 1
        entries.append(item)
    return {
        "path": rel,
        "entries": entries,
        "truncated": len(children) > MAX_ENTRIES,
        "folder": describe(rel, "folder", rules) if rel else None,
    }


def list_folder(root: Path, rel: str, rules: Sequence[Rule]) -> Dict:
    rel = clean_relative(rel, allow_empty=True)
    fd = _open_folder(root, rel)
    try:
        children = _children_fd(fd)
    finally:
        os.close(fd)
    counts: Dict[str, int] = {}
    for name, _ in children:
        counts[path_key(name)] = counts.get(path_key(name), 0) + 1
    entries = []
    for name, child_kind in children[:MAX_ENTRIES]:
        child_rel = f"{rel}/{name}" if rel else name
        item = describe(child_rel, child_kind, rules)
        item["ambiguous"] = counts[path_key(name)] > 1
        entries.append(item)
    return {
        "path": rel,
        "entries": entries,
        "truncated": len(children) > MAX_ENTRIES,
        "folder": describe(rel, "folder", rules) if rel else None,
    }


def attachable_files(root: Path, rel: str, limit: int = MAX_COUNTED_FILES) -> Tuple[List[str], bool]:
    """Markdown files below a path that the fixed protections would not refuse by name. Returns
    (paths, truncated). Walks through open descriptors, so symbolic links are never followed, not
    even one that appears while the walk is running."""
    if real_entry(root, rel) == "file":
        return ([rel] if not any(is_sensitive_name(p) for p in rel.split("/")) else []), False
    out: List[str] = []
    visited = 0
    truncated = False

    def walk(fd: int, here: str, depth: int) -> None:
        nonlocal visited, truncated
        for name, child_kind in _children_fd(fd):
            if truncated:
                return
            visited += 1
            if visited > MAX_VISITED:
                truncated = True
                return
            if is_sensitive_name(name):
                continue
            child = f"{here}/{name}" if here else name
            if child_kind == "file":
                out.append(child)
                if len(out) >= limit:
                    truncated = True
                    return
            elif depth < MAX_DEPTH:
                try:
                    sub = _open_child(fd, name)
                except ValidationError:
                    continue  # replaced by a link or removed meanwhile: not counted
                try:
                    walk(sub, child, depth + 1)
                finally:
                    os.close(sub)
            else:
                truncated = True

    fd = _open_folder(root, rel)
    try:
        walk(fd, rel, 0)
    finally:
        os.close(fd)
    return out, truncated


def apply_change(rules: Sequence[Rule], rel: str, effect: str) -> List[Rule]:
    """The rules as they would be after one change (effect: allow, deny or clear)."""
    parts = key_parts(path_key(rel))
    kept = [r for r in rules if r.parts != parts]
    if effect in ("allow", "deny"):
        kept.append(Rule(parts, effect, rel))
    return kept


def count_attachable(files: Sequence[str], rules: Sequence[Rule]) -> int:
    return sum(1 for f in files if evaluate(key_parts(path_key(f)), rules)[0] == "allowed")


def is_ambiguous(root: Path, rel: str) -> bool:
    """True if a sibling differs from this entry only in case or Unicode form."""
    if not rel:
        return False
    parent, _, name = rel.rpartition("/")
    fd = _open_folder(root, parent)
    try:
        return sum(1 for n, _ in _children_fd(fd) if path_key(n) == path_key(name)) > 1
    finally:
        os.close(fd)
