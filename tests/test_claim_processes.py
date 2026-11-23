"""Claiming a draft for sending, with real separate processes.

Claude Desktop can run more than one server process on the same database. The thread test in test_drafts.py
shares one interpreter; these tests start fresh Python processes that each open the database themselves and are
released at the same moment, which is what two instances of the app actually do."""

import subprocess
import sys
import time
from collections import Counter

import pytest

from outbox_mcp.security.rate_limiter import RateLimits

PROCESSES = 6

CHILD = """
import os, sys, time
from outbox_mcp.errors import DraftNotSendable, RateLimitExceeded
from outbox_mcp.security.rate_limiter import RateLimits
from outbox_mcp.storage.database import Database

path, draft, go, ready, per_hour, per_day = sys.argv[1:7]
db = Database(path)
open(ready, "w").close()
deadline = time.time() + 30
while not os.path.exists(go) and time.time() < deadline:
    time.sleep(0.002)
limits = RateLimits(max_prepares_per_hour=100, max_sends_per_hour=int(per_hour), max_sends_per_day=int(per_day))
try:
    db.claim_for_sending(draft, "o", 1001.0, limits)
    print("claimed")
except DraftNotSendable as exc:
    print("refused:" + exc.status)
except RateLimitExceeded:
    print("limited")
"""


def _run_together(db, tmp_path, drafts, per_hour, per_day):
    """Start one process per entry of `drafts`, release them all at once, return what each one printed."""
    go = tmp_path / "go"
    procs = []
    for i, draft in enumerate(drafts):
        ready = tmp_path / f"ready{i}"
        procs.append((ready, subprocess.Popen(
            [sys.executable, "-c", CHILD, db.path, draft, str(go), str(ready), str(per_hour), str(per_day)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )))
    try:
        deadline = time.time() + 30
        while not all(r.exists() for r, _ in procs):
            assert time.time() < deadline, "a process did not start in time"
            time.sleep(0.01)
        go.write_text("go")
        out = []
        for _, p in procs:
            stdout, stderr = p.communicate(timeout=60)
            assert p.returncode == 0, stderr
            out.append(stdout.strip())
        return out
    finally:
        for _, p in procs:
            if p.poll() is None:
                p.kill()


def _drafts(db, n):
    big = RateLimits(max_prepares_per_hour=100, max_sends_per_hour=100, max_sends_per_day=100)
    return [db.create_draft("o", "Me", "me@example.com", f"S{i}", "body", 1000.0, 600, big) for i in range(n)]


def _count(db, event):
    return sum(1 for r in db.audit_events() if r["event"] == event)


def test_many_processes_claiming_one_draft_exactly_one_wins(db, tmp_path):
    [d] = _drafts(db, 1)
    results = Counter(_run_together(db, tmp_path, [d.id] * PROCESSES, per_hour=100, per_day=100))
    assert results == {"claimed": 1, "refused:sending": PROCESSES - 1}
    assert db.get_draft(d.id, "o").status == "sending"
    assert _count(db, "claimed") == 1


def test_the_send_limit_holds_across_processes(db, tmp_path):
    drafts = _drafts(db, PROCESSES)
    results = Counter(_run_together(db, tmp_path, [d.id for d in drafts], per_hour=2, per_day=10))
    assert results == {"claimed": 2, "limited": PROCESSES - 2}
    assert _count(db, "claimed") == 2
    assert sum(1 for d in drafts if db.get_draft(d.id, "o").status == "sending") == 2


@pytest.mark.parametrize("round_", range(5))
def test_the_race_stays_clean_over_repeated_rounds(db, tmp_path, round_):
    folder = tmp_path / f"r{round_}"
    folder.mkdir()
    [d] = _drafts(db, 1)
    results = Counter(_run_together(db, folder, [d.id] * PROCESSES, per_hour=100, per_day=100))
    assert results["claimed"] == 1 and sum(results.values()) == PROCESSES
