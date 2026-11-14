"""The recipient registry: suggestions wait, only the admin side can activate."""

import sqlite3

import pytest

from outbox_mcp.errors import ConfigError, RateLimitExceeded, RecipientNotAllowed, ValidationError
from outbox_mcp.security.recipients import Recipient, RecipientList
from outbox_mcp.storage.database import Database
from outbox_mcp.storage.recipient_store import DbRecipientList, RecipientStore

DAY = 86400.0


@pytest.fixture
def store(db):
    return RecipientStore(db)


def suggest(store, name="Ferdinand", address="ferdinand@example.org", note="", now=1000.0, **kw):
    return store.suggest("o", name, address, note, now, **kw)


def approve(store, ref="ferdinand@example.org", now=2000.0, **kw):
    return store.approve(ref, now, "admin:test", **kw)


# -- suggesting ---------------------------------------------------------------------

def test_a_suggestion_is_not_a_recipient(store):
    r = suggest(store, note="Neffe")
    assert r.status == "suggested" and r.address == "ferdinand@example.org"
    assert store.active() == ()
    assert [p.address for p in store.pending()] == ["ferdinand@example.org"]


def test_suggestion_is_validated(store):
    for name, address in [("", "a@example.com"), ("A", "not-an-address"), ("A", "A <a@example.com>"),
                          ("A\nB", "a@example.com"), ("A", "a@example.com, b@example.com")]:
        with pytest.raises(ValidationError):
            suggest(store, name=name, address=address)
    with pytest.raises(ValidationError):
        suggest(store, note="x" * 301)
    with pytest.raises(ValidationError):
        suggest(store, note="line\nbreak")
    assert store.pending() == []


def test_same_suggestion_twice_returns_the_open_one(store):
    first = suggest(store)
    again = suggest(store, address="FERDINAND@example.org")
    assert again.status == "already_suggested" and again.id == first.id
    assert len(store.pending()) == 1


def test_suggesting_an_active_address_changes_nothing(store):
    suggest(store)
    approve(store)
    assert suggest(store).status == "already_allowed"
    assert len(store.active()) == 1 and store.pending() == []


def test_blocked_addresses_cannot_be_suggested(store):
    suggest(store)
    store.block("ferdinand@example.org", 1500.0, "admin:test")
    with pytest.raises(RecipientNotAllowed):
        suggest(store)
    assert store.pending() == []


@pytest.mark.parametrize("final", ["reject", "remove_after_approve", "expire"])
def test_rejected_removed_or_expired_can_be_suggested_again(store, final):
    suggest(store)
    if final == "reject":
        store.reject("ferdinand@example.org", 1500.0, "admin:test")
    elif final == "remove_after_approve":
        approve(store)
        store.remove("ferdinand@example.org", 2500.0, "admin:test")
    else:
        store.expire_stale(1000.0 + 31 * DAY, ttl_days=30)
    assert suggest(store, now=1000.0 + 40 * DAY).status == "suggested"
    assert store.active() == ()


def test_limits_open_and_per_day(store):
    for i in range(5):
        suggest(store, name=f"P{i}", address=f"p{i}@example.org", max_open=5, max_per_day=10)
    with pytest.raises(RateLimitExceeded):
        suggest(store, name="P5", address="p5@example.org", max_open=5, max_per_day=10)
    for i in range(5):
        store.reject(f"p{i}@example.org", 1100.0, "admin:test")
    for i in range(5, 10):
        suggest(store, name=f"P{i}", address=f"p{i}@example.org", now=1200.0, max_open=5, max_per_day=10)
    for i in range(5, 10):
        store.reject(f"p{i}@example.org", 1300.0, "admin:test")
    with pytest.raises(RateLimitExceeded):
        suggest(store, name="Q", address="q@example.org", now=1400.0, max_open=5, max_per_day=10)
    assert suggest(store, name="Q", address="q@example.org", now=1000.0 + DAY + 1,
                   max_open=5, max_per_day=10).status == "suggested"


def test_owners_only_see_their_own_suggestions(store):
    store.suggest("alice", "A", "a@example.org", "", 1000.0)
    store.suggest("bob", "B", "b@example.org", "", 1000.0)
    assert [s.address for s in store.suggestions_of("alice")] == ["a@example.org"]
    assert store.suggestions_of("carol") == []


def test_expiry_after_ttl_only_for_open_suggestions(store):
    suggest(store, now=1000.0)
    suggest(store, name="Anna", address="anna@example.org", now=1000.0 + 20 * DAY)
    assert store.expire_stale(1000.0 + 31 * DAY, ttl_days=30) == 1
    assert [p.address for p in store.pending()] == ["anna@example.org"]
    approve(store, "anna@example.org")
    assert store.expire_stale(1000.0 + 400 * DAY, ttl_days=30) == 0
    assert len(store.active()) == 1


# -- the admin side -----------------------------------------------------------------

def test_approve_makes_it_active_with_aliases(store):
    suggest(store, name="Ferdinand")
    rec = approve(store, aliases=["mein Neffe", "Ferdi"])
    assert rec.status == "active"
    [r] = store.active()
    assert (r.name, r.address, r.aliases) == ("Ferdinand", "ferdinand@example.org", ("mein Neffe", "Ferdi"))


def test_approve_can_rename_and_refer_by_id(store):
    s = suggest(store)
    approve(store, s.id, name="Ferdinand P.")
    assert store.active()[0].name == "Ferdinand P."


def test_approve_requires_an_open_suggestion(store):
    with pytest.raises(ValidationError):
        approve(store, "nobody@example.org")
    suggest(store)
    store.reject("ferdinand@example.org", 1500.0, "admin:test")
    with pytest.raises(ValidationError):
        approve(store)


def test_names_and_aliases_must_stay_unique_among_active(store):
    store.add("Me", "me@example.com", ["ich"], 1000.0, "admin:test")
    suggest(store, name="Ich")
    with pytest.raises(ValidationError):
        approve(store)  # "Ich" collides with the alias "ich"
    approve(store, name="Ferdinand", aliases=["Ferdi"])
    suggest(store, name="Fred", address="fred@example.org")
    with pytest.raises(ValidationError):
        approve(store, "fred@example.org", aliases=["ferdi"])
    assert [r.name for r in store.active()] == ["Me", "Ferdinand"]


def test_add_activates_directly(store):
    r = store.add("Anna", "anna@example.org", ["Annchen"], 1000.0, "admin:test")
    assert r.status == "active" and store.active()[0].aliases == ("Annchen",)


def test_add_refuses_blocked_and_duplicates(store):
    store.add("Anna", "anna@example.org", [], 1000.0, "admin:test")
    with pytest.raises(ValidationError):
        store.add("Anna 2", "ANNA@example.org", [], 1100.0, "admin:test")
    store.block("anna@example.org", 1200.0, "admin:test")
    with pytest.raises(RecipientNotAllowed):
        store.add("Anna", "anna@example.org", [], 1300.0, "admin:test")
    store.unblock("anna@example.org", 1400.0, "admin:test")
    assert store.add("Anna", "anna@example.org", [], 1500.0, "admin:test").status == "active"


def test_block_stops_an_active_recipient_at_once(store):
    store.add("Anna", "anna@example.org", [], 1000.0, "admin:test")
    store.block("anna@example.org", 1100.0, "admin:test")
    assert store.active() == ()


def test_remove_only_for_active(store):
    suggest(store)
    with pytest.raises(ValidationError):
        store.remove("ferdinand@example.org", 1500.0, "admin:test")


def test_records_listing_shows_every_status(store):
    suggest(store)
    store.add("Anna", "anna@example.org", [], 1000.0, "admin:test")
    assert {r.status for r in store.all_records()} == {"suggested", "active"}


# -- audit --------------------------------------------------------------------------

def test_every_change_is_audited_with_its_source(store, db):
    suggest(store, note="Neffe")
    approve(store, aliases=["Ferdi"])
    store.block("ferdinand@example.org", 3000.0, "admin:test")
    events = [(e["event"], e["recipient_address"], e["owner"]) for e in db.audit_events()]
    assert ("recipient_suggested", "ferdinand@example.org", "o") in events
    assert ("recipient_approved", "ferdinand@example.org", "admin:test") in events
    assert ("recipient_blocked", "ferdinand@example.org", "admin:test") in events


def test_audit_log_stays_append_only_for_recipient_events(store, db):
    suggest(store)
    with sqlite3.connect(db.path) as conn:
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            conn.execute("DELETE FROM audit_log WHERE event='recipient_suggested'")


# -- the list the mail service uses -------------------------------------------------

def test_db_list_behaves_like_the_file_list(store):
    store.add("Me", "me@example.com", ["myself"], 1000.0, "admin:test")
    live = DbRecipientList(store)
    assert live.resolve("MYSELF").address == "me@example.com"
    assert live.is_allowed_address("ME@example.com")
    with pytest.raises(RecipientNotAllowed):
        live.resolve("stranger@example.com")
    assert [r.name for r in live.all()] == ["Me"]


def test_db_list_sees_changes_without_restart(store):
    live = DbRecipientList(store)
    assert live.all() == ()
    suggest(store)
    assert not live.is_allowed_address("ferdinand@example.org")
    approve(store)
    assert live.is_allowed_address("ferdinand@example.org")
    store.block("ferdinand@example.org", 3000.0, "admin:test")
    assert not live.is_allowed_address("ferdinand@example.org")


# -- one-time import of the old recipients file -------------------------------------

def test_file_is_imported_once(store):
    old = RecipientList([Recipient("Me", "me@example.com", ("ich",)), Recipient("Anna", "anna@example.org", ())])
    assert store.import_list_once(old, 1000.0) == 2
    assert [r.name for r in store.active()] == ["Me", "Anna"]
    assert store.import_list_once(old, 1100.0) == 0


def test_removed_recipients_do_not_come_back_from_the_file(store):
    old = RecipientList([Recipient("Me", "me@example.com", ())])
    store.import_list_once(old, 1000.0)
    store.remove("me@example.com", 1100.0, "admin:test")
    assert store.import_list_once(old, 1200.0) == 0
    assert store.active() == ()


def test_empty_file_imports_nothing_and_does_not_block_later_adds(store):
    assert store.import_list_once(RecipientList([]), 1000.0) == 0
    store.add("Anna", "anna@example.org", [], 1100.0, "admin:test")
    assert len(store.active()) == 1


def test_import_is_audited(store, db):
    store.import_list_once(RecipientList([Recipient("Me", "me@example.com", ())]), 1000.0)
    assert [e["event"] for e in db.audit_events()] == ["recipient_imported"]


# -- schema -------------------------------------------------------------------------

OLD_V1 = """
CREATE TABLE drafts (
    id TEXT PRIMARY KEY, owner TEXT NOT NULL, created_at REAL NOT NULL, expires_at REAL NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending','sending','sent','failed','unknown','expired','cancelled')),
    recipient_name TEXT NOT NULL, recipient_address TEXT NOT NULL, subject TEXT NOT NULL, body TEXT NOT NULL,
    body_sha256 TEXT NOT NULL, sending_at REAL, finished_at REAL, detail TEXT);
CREATE TABLE audit_log (
    seq INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, event TEXT NOT NULL, draft_id TEXT, owner TEXT,
    recipient_address TEXT, subject TEXT, body_sha256 TEXT, detail TEXT);
CREATE TABLE draft_attachments (
    draft_id TEXT NOT NULL, position INTEGER NOT NULL, name TEXT NOT NULL, data BLOB NOT NULL,
    size_bytes INTEGER NOT NULL, sha256 TEXT NOT NULL, PRIMARY KEY (draft_id, position));
INSERT INTO drafts VALUES ('drf_old','o',1.0,9999999999.0,'pending','Ferdinand','ferdinand@example.org',
    'Alt','Alter Text','abc',NULL,NULL,NULL);
INSERT INTO audit_log(ts,event,draft_id,owner) VALUES (1.0,'prepared','drf_old','o');
PRAGMA user_version = 1;
"""


def test_version_1_database_gets_the_recipient_tables(tmp_path):
    path = tmp_path / "v1.db"
    with sqlite3.connect(path) as conn:
        conn.executescript(OLD_V1)
    db = Database(path)
    assert db.get_draft("drf_old", "o").body == "Alter Text"
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == Database.SCHEMA_VERSION == 2
    store = RecipientStore(db)
    assert store.add("Anna", "anna@example.org", [], 1000.0, "admin:test").status == "active"
    Database(path)  # second open is harmless
    assert len(RecipientStore(Database(path)).active()) == 1


def test_database_from_the_future_is_still_refused(tmp_path):
    path = tmp_path / "future.db"
    Database(path)
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA user_version = 3")
    with pytest.raises(ConfigError, match="newer"):
        Database(path)
