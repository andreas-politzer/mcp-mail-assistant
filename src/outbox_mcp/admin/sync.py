"""Synchronisation of an upload source: the plan the Mac app asks for before it sends any file.

The plan is computed on the server from the manifest (path, sha256, size per local file) against what is
stored, the rules and the quotas. Nothing is stored by planning. The plan itself stays on the server,
bound to the confirmation, so the apply step cannot widen it: only files named in the plan, with the
planned hash, can be stored, and removals are exactly the ones planned.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

from .. import sources as src_rules
from ..errors import SourceRejected
from ..storage.database import Database

MAX_MANIFEST = 2000
PASS_FILES = 40
PASS_BYTES = 8 * 1024 * 1024
MAX_ITEMS = 200
_SHA = re.compile(r"^[0-9a-f]{64}$")


def _entries(manifest: Any) -> List[dict]:
    if not isinstance(manifest, list):
        raise SourceRejected("input", "The manifest must be a list.")
    if len(manifest) > MAX_MANIFEST:
        raise SourceRejected("input", f"The manifest may list at most {MAX_MANIFEST} files.")
    out = []
    for entry in manifest:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            raise SourceRejected("input", "Every manifest entry needs a path.")
        sha, size = entry.get("sha256"), entry.get("size")
        if not isinstance(sha, str) or not _SHA.match(sha.lower()):
            raise SourceRejected("input", "Every manifest entry needs a sha256 (64 hex characters).")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise SourceRejected("input", "Every manifest entry needs a size in bytes.")
        out.append({"path": entry["path"], "sha256": sha.lower(), "size": size})
    return out


def make_plan(db: Database, source, manifest: Any) -> Dict[str, Any]:
    """The plan: what would be added, replaced and removed, what is rejected, and the passes."""
    if source["kind"] != "upload":
        raise SourceRejected("state", "Only an upload source is synchronised.")
    entries = _entries(manifest)
    allowed, denied = db.source_rule_lists(source["id"])
    current = {r["path_key"]: r for r in db.list_stored_files(source["id"], "current")}
    accept: Dict[str, dict] = {}
    rejected: List[dict] = []
    for e in entries:
        try:
            display, key = src_rules.clean_relative_path(e["path"])
            src_rules.check_stored_name("upload", display)
        except SourceRejected as exc:
            rejected.append({"path": e["path"][:200], "reason": str(exc)})
            continue
        if key in accept:
            rejected.append({"path": display, "reason": "Listed twice."})
            continue
        state = src_rules.evaluate(source["scope"], source["file_key"], allowed, denied, key)
        if state == "denied":
            rejected.append({"path": display, "reason": "Blocked."})
        elif state != "allowed":
            rejected.append({"path": display, "reason": "Not in an allowed folder of this source."})
        elif e["size"] > src_rules.MAX_FILE_BYTES:
            rejected.append({"path": display, "reason": f"Too large (limit {src_rules.MAX_FILE_BYTES} bytes)."})
        else:
            accept[key] = {"path": display, "sha256": e["sha256"], "size": e["size"]}
    if len(accept) > src_rules.MAX_FILES_PER_SOURCE:
        raise SourceRejected("quota", f"A source can hold at most {src_rules.MAX_FILES_PER_SOURCE} files.")
    usage = db.source_usage()
    mine = usage["per_source"].get(source["id"], {}).get("bytes", 0)
    if usage["bytes"] - mine + sum(a["size"] for a in accept.values()) > src_rules.MAX_TOTAL_BYTES:
        raise SourceRejected("quota", f"The stored files may not exceed {src_rules.MAX_TOTAL_BYTES} bytes in total.")
    todo, items, unchanged = {}, [], 0
    for key, a in sorted(accept.items()):
        row = current.get(key)
        if row is None:
            todo[key] = a
            items.append({"path": a["path"], "change": "new", "size": a["size"]})
        elif row["sha256"] != a["sha256"]:
            todo[key] = a
            items.append({"path": a["path"], "change": "changed", "size": a["size"]})
        else:
            unchanged += 1
    removals = sorted(k for k in current if k not in accept)
    for k in removals:
        items.append({"path": current[k]["path_display"], "change": "removed", "size": current[k]["size_bytes"]})
    passes: List[List[str]] = []
    run, run_bytes = [], 0
    for key in sorted(todo):
        size = todo[key]["size"]
        if run and (len(run) >= PASS_FILES or run_bytes + size > PASS_BYTES):
            passes.append(run)
            run, run_bytes = [], 0
        run.append(key)
        run_bytes += size
    if run:
        passes.append(run)
    return {
        "summary": {"new": sum(1 for i in items if i["change"] == "new"),
                    "changed": sum(1 for i in items if i["change"] == "changed"),
                    "removed": len(removals), "unchanged": unchanged},
        "items": items[:MAX_ITEMS], "items_truncated": len(items) > MAX_ITEMS, "rejected": rejected[:MAX_ITEMS],
        "rejected_count": len(rejected), "todo": todo, "removals": removals, "passes": passes,
        "single_file": source["scope"] == "file",
    }
