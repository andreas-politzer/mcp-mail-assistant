"""Several processes open a brand-new database at the same moment (Claude Desktop starts the
server twice, right after each other). None of them may fail with "database is locked"."""

import multiprocessing as mp
import sys
import time
from pathlib import Path

SRC = str(Path(__file__).resolve().parents[1] / "src")


def _open(path, start_at, queue):
    sys.path.insert(0, SRC)
    from outbox_mcp.storage.database import Database

    while time.time() < start_at:
        pass
    try:
        Database(Path(path))
        queue.put("ok")
    except Exception as exc:  # noqa: BLE001 - the message is the result
        queue.put(f"{exc.__class__.__name__}: {exc}")


def test_new_database_opened_by_several_processes_at_once(tmp_path):
    ctx = mp.get_context("spawn")
    for round_number in range(6):
        path = tmp_path / f"round{round_number}" / "outbox.db"
        path.parent.mkdir()
        queue = ctx.Queue()
        start_at = time.time() + 1.5
        procs = [ctx.Process(target=_open, args=(str(path), start_at, queue)) for _ in range(6)]
        for p in procs:
            p.start()
        results = [queue.get(timeout=60) for _ in procs]
        for p in procs:
            p.join(timeout=30)
        assert results == ["ok"] * 6, results
