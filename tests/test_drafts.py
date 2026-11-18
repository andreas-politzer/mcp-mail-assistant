import os
import stat
import sqlite3
import threading

import pytest

from outbox_mcp.errors import DraftNotFound, DraftNotSendable, RateLimitExceeded
from outbox_mcp.security.rate_limiter import RateLimits

LIM = RateLimits(max_prepares_per_hour=3, max_sends_per_hour=2, max_sends_per_day=3)


def make(db, now=1000.0, owner="o", n=1):
    return db.create_draft(owner, "Me", "me@example.com", f"S{n}", "body", now, 600, LIM)


def test_create_and_get(db):
    d = make(db)
    assert d.id.startswith("drf_") and d.status == "pending"
    assert db.get_draft(d.id, "o").body == "body"
    assert make(db, n=2).id != d.id


def test_owner_isolation(db):
    d = make(db)
    with pytest.raises(DraftNotFound):
        db.get_draft(d.id, "other")
    with pytest.raises(DraftNotFound):
        db.get_draft("drf_nope", "o")


def test_content_immutable(db):
    d = make(db)
    conn = sqlite3.connect(db.path)
    for col, val in [("body", "x"), ("subject", "x"), ("recipient_address", "x@example.net"), ("expires_at", 1)]:
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute(f"UPDATE drafts SET {col}=? WHERE id=?", (val, d.id))
    conn.close()


def test_invalid_transitions(db):
    d = make(db)
    conn = sqlite3.connect(db.path)
    with pytest.raises(sqlite3.DatabaseError):
        conn.execute("UPDATE drafts SET status='sent' WHERE id=?", (d.id,))
    conn.close()
    db.claim_for_sending(d.id, "o", 1001.0, LIM)
    db.finish(d.id, "sent", 1002.0)
    conn = sqlite3.connect(db.path)
    with pytest.raises(sqlite3.DatabaseError):
        conn.execute("UPDATE drafts SET status='pending' WHERE id=?", (d.id,))
    conn.close()


def test_audit_append_only(db):
    make(db)
    conn = sqlite3.connect(db.path)
    with pytest.raises(sqlite3.DatabaseError):
        conn.execute("UPDATE audit_log SET event='x'")
    with pytest.raises(sqlite3.DatabaseError):
        conn.execute("DELETE FROM audit_log")
    conn.close()


def test_audit_has_no_body(db):
    make(db)
    row = db.audit_events()[0]
    assert "body" not in row.keys() and row["event"] == "prepared"


def test_claim_once(db):
    d = make(db)
    db.claim_for_sending(d.id, "o", 1001.0, LIM)
    with pytest.raises(DraftNotSendable) as e:
        db.claim_for_sending(d.id, "o", 1002.0, LIM)
    assert e.value.status == "sending"


def test_concurrent_claim_only_one_wins(db):
    d = make(db)
    results = []

    def go():
        try:
            db.claim_for_sending(d.id, "o", 1001.0, LIM)
            results.append("ok")
        except DraftNotSendable:
            results.append("refused")

    threads = [threading.Thread(target=go) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert results.count("ok") == 1 and results.count("refused") == 7


def test_expiry(db):
    d = make(db)
    with pytest.raises(DraftNotSendable) as e:
        db.claim_for_sending(d.id, "o", 1000.0 + 601, LIM)
    assert e.value.status == "expired"
    assert db.get_draft(d.id, "o").status == "expired"


def test_expire_stale(db):
    d = make(db)
    assert db.expire_stale(1000.0 + 601) == 1
    assert db.get_draft(d.id, "o").status == "expired"


def test_cancel(db):
    d = make(db)
    assert db.cancel(d.id, "o", 1001.0).status == "cancelled"
    with pytest.raises(DraftNotSendable):
        db.claim_for_sending(d.id, "o", 1002.0, LIM)
    with pytest.raises(DraftNotSendable):
        db.cancel(d.id, "o", 1003.0)


def test_prepare_rate_limit(db):
    for i in range(3):
        make(db, n=i)
    with pytest.raises(RateLimitExceeded):
        make(db, n=9)
    make(db, now=1000.0 + 3601, n=10)


def test_send_rate_limit_keeps_draft(db):
    ds = [make(db, n=i) for i in range(3)]
    db.claim_for_sending(ds[0].id, "o", 1001.0, LIM)
    db.claim_for_sending(ds[1].id, "o", 1002.0, LIM)
    with pytest.raises(RateLimitExceeded):
        db.claim_for_sending(ds[2].id, "o", 1003.0, LIM)
    assert db.get_draft(ds[2].id, "o").status == "pending"
    assert db.count_events("rate_limited") == 1
    later = make(db, now=1003.0 + 3601, n=7)
    db.claim_for_sending(later.id, "o", 1003.0 + 3602, LIM)


def test_daily_limit(db):
    ds = [make(db, now=1000.0 + i * 4000, n=i) for i in range(4)]
    for i in range(3):
        db.claim_for_sending(ds[i].id, "o", 1001.0 + i * 4000, LIM)
    with pytest.raises(RateLimitExceeded):
        db.claim_for_sending(ds[3].id, "o", 1001.0 + 3 * 4000, LIM)


def test_recover_interrupted(db):
    d = make(db)
    db.claim_for_sending(d.id, "o", 1001.0, LIM)
    assert db.recover_interrupted(1005.0) == 1
    assert db.get_draft(d.id, "o").status == "unknown"


def test_recover_leaves_a_send_that_just_began(db):
    """Another server process on the same database may be in the middle of this send."""
    fresh, stale = make(db, n=1), make(db, n=2)
    db.claim_for_sending(stale.id, "o", 1001.0, LIM)
    db.claim_for_sending(fresh.id, "o", 1100.0, LIM)
    assert db.recover_interrupted(1110.0, older_than=60.0) == 1
    assert db.get_draft(stale.id, "o").status == "unknown"
    assert db.get_draft(fresh.id, "o").status == "sending"
    assert db.recover_interrupted(1200.0, older_than=60.0) == 1
    assert db.get_draft(fresh.id, "o").status == "unknown"


def test_purge_only_old_terminal(db):
    old, pending = make(db, n=1), make(db, now=1000.0, n=2)
    db.cancel(old.id, "o", 1001.0)
    assert db.purge(1001.0 + 8 * 86400, 7) == 1
    with pytest.raises(DraftNotFound):
        db.get_draft(old.id, "o")
    assert db.get_draft(pending.id, "o").status == "pending"
    assert len(db.audit_events()) >= 3


@pytest.mark.skipif(os.name == "nt", reason="POSIX file permissions")
def test_database_files_are_private(tmp_path):
    from outbox_mcp.storage.database import Database
    old = os.umask(0o022)  # a typical umask would give 0644
    try:
        db = Database(tmp_path / "new.db")
        make(db)  # writes, so the -wal / -shm files exist
        files = [p for p in tmp_path.iterdir() if p.name.startswith("new.db")]
        assert (tmp_path / "new.db") in files
        for f in files:
            assert stat.S_IMODE(f.stat().st_mode) & 0o077 == 0, f.name
        assert stat.S_IMODE((tmp_path / "new.db").stat().st_mode) == 0o600
    finally:
        os.umask(old)


@pytest.mark.skipif(os.name == "nt", reason="POSIX file permissions")
def test_existing_database_gets_restricted(tmp_path):
    from outbox_mcp.storage.database import Database
    path = tmp_path / "old.db"
    path.write_bytes(b"")
    path.chmod(0o644)
    Database(path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
