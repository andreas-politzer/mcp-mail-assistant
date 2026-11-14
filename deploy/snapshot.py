#!/usr/bin/env python3
"""Write consistent snapshots of the SQLite databases to a staging directory.

Copying a live SQLite file (especially one in WAL mode) can produce a broken copy.
SQLite's backup API takes a consistent snapshot while the service keeps running.
restic then backs up the staging directory. Adjust SRC and DST for your layout.
"""
import os
import sqlite3
import sys
from pathlib import Path

SRC = Path.home() / "outbox-data"
DST = Path.home() / "outbox-backup" / "staging"


def snapshot(src: Path) -> None:
    tmp = DST / (src.name + ".tmp")
    dst = DST / src.name
    tmp.unlink(missing_ok=True)
    source = sqlite3.connect(src, timeout=30)
    target = sqlite3.connect(tmp)
    try:
        source.backup(target)
        target.execute("pragma journal_mode=delete")
        result = target.execute("pragma integrity_check").fetchone()[0]
    finally:
        target.close()
        source.close()
    if result != "ok":
        tmp.unlink(missing_ok=True)
        sys.exit(f"{src.name}: integrity_check failed: {result}")
    os.chmod(tmp, 0o600)
    tmp.replace(dst)
    print(f"{src.name}: ok, {dst.stat().st_size} bytes")


def main() -> None:
    DST.mkdir(mode=0o700, exist_ok=True)
    dbs = sorted(SRC.glob("*.db"))
    if not dbs:
        sys.exit("no databases found")
    for db in dbs:
        snapshot(db)


main()
