"""Schema 6: every recipient is 'own' or 'external'. Only the admin side can change it."""

import sqlite3
import threading

import pytest

from outbox_mcp.errors import ValidationError
from outbox_mcp.security.recipients import Recipient, RecipientList
from outbox_mcp.storage.database import Database
from outbox_mcp.storage.recipient_store import RecipientStore

SRC = "admin:test"


@pytest.fixture
def store(db):
    return RecipientStore(db)


def make_v5_database(path):
    """A database as version 5 left it: the recipients table has no scope column."""
    db = Database(path)
    store = RecipientStore(db)
    store.add("Anna", "anna@example.org", [], 1000.0, SRC)
    store.add("Berta", "berta@example.org", [], 1000.0, SRC)
    store.add("Carla", "carla@example.org", [], 1000.0, SRC)
    store.remove("carla@example.org", 1100.0, SRC)
    store.suggest("o", "Dora", "dora@example.org", "", 1200.0)
    store.block("evil@example.org", 1300.0, SRC)
    with sqlite3.connect(path) as conn:
        conn.execute("ALTER TABLE recipients DROP COLUMN scope")
        conn.execute("PRAGMA user_version = 5")
    return path


def scopes(path):
    with sqlite3.connect(path) as conn:
        return dict(conn.execute("SELECT address, scope FROM recipients ORDER BY id").fetchall())


# -- migration ----------------------------------------------------------------------

def test_upgrade_from_5_makes_active_recipients_own_and_all_others_external(tmp_path):
    path = make_v5_database(tmp_path / "v5.db")
    with sqlite3.connect(path) as conn:
        assert "scope" not in [r[1] for r in conn.execute("PRAGMA table_info(recipients)")]
    Database(path)
    assert scopes(path) == {
        "anna@example.org": "own",
        "berta@example.org": "own",
        "carla@example.org": "external",  # removed: not active, so not own
        "dora@example.org": "external",   # only suggested
        "evil@example.org": "external",   # blocked
    }
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == Database.SCHEMA_VERSION == 6


def test_the_migration_runs_once_and_does_not_undo_a_later_change(tmp_path):
    path = make_v5_database(tmp_path / "v5.db")
    store = RecipientStore(Database(path))
    store.set_scope("anna@example.org", "external", 2000.0, SRC)
    store.add("Eva", "eva@example.org", [], 2100.0, SRC)  # external by default
    Database(path)  # opening again must not touch anything
    assert scopes(path)["anna@example.org"] == "external"
    assert scopes(path)["eva@example.org"] == "external"
    assert scopes(path)["berta@example.org"] == "own"


def test_two_processes_opening_a_version_5_database_at_once_do_not_clash(tmp_path):
    path = make_v5_database(tmp_path / "v5.db")
    errors = []
    barrier = threading.Barrier(6)

    def opener():
        barrier.wait()
        try:
            Database(path)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=opener) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert scopes(path)["anna@example.org"] == "own"
    assert scopes(path)["dora@example.org"] == "external"


def test_a_new_database_has_the_column_and_nothing_is_own_by_itself(tmp_path):
    path = tmp_path / "new.db"
    store = RecipientStore(Database(path))
    store.add("Anna", "anna@example.org", [], 1000.0, SRC)
    assert scopes(path) == {"anna@example.org": "external"}


def test_the_database_itself_refuses_any_other_scope_value(tmp_path):
    path = tmp_path / "new.db"
    store = RecipientStore(Database(path))
    store.add("Anna", "anna@example.org", [], 1000.0, SRC)
    with sqlite3.connect(path) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE recipients SET scope='trusted'")


# -- the rules of the store -----------------------------------------------------------

def test_everything_is_external_unless_the_admin_side_says_own(store):
    assert store.add("Anna", "anna@example.org", [], 1000.0, SRC).scope == "external"
    assert store.add("Berta", "berta@example.org", [], 1000.0, SRC, scope="own").scope == "own"
    store.suggest("o", "Carla", "carla@example.org", "", 1000.0)
    assert store.approve("carla@example.org", 1100.0, SRC).scope == "external"
    store.suggest("o", "Dora", "dora@example.org", "", 1000.0)
    assert store.approve("dora@example.org", 1100.0, SRC, scope="own").scope == "own"


def test_an_unknown_scope_is_refused(store):
    with pytest.raises(ValidationError):
        store.add("Anna", "anna@example.org", [], 1000.0, SRC, scope="trusted")
    store.suggest("o", "Carla", "carla@example.org", "", 1000.0)
    with pytest.raises(ValidationError):
        store.approve("carla@example.org", 1100.0, SRC, scope="")
    store.add("Anna", "anna@example.org", [], 1000.0, SRC)
    with pytest.raises(ValidationError):
        store.set_scope("anna@example.org", "Own", 1100.0, SRC)


def test_scope_of_only_answers_for_active_recipients(store):
    store.add("Anna", "Anna@Example.org", [], 1000.0, SRC, scope="own")
    store.add("Berta", "berta@example.org", [], 1000.0, SRC)
    store.suggest("o", "Carla", "carla@example.org", "", 1000.0)
    store.add("Dora", "dora@example.org", [], 1000.0, SRC, scope="own")
    store.remove("dora@example.org", 1100.0, SRC)
    assert store.scope_of("anna@example.org") == "own"
    assert store.scope_of("ANNA@EXAMPLE.ORG") == "own"
    assert store.scope_of("berta@example.org") == "external"
    assert store.scope_of("carla@example.org") is None    # only suggested
    assert store.scope_of("dora@example.org") is None     # removed
    assert store.scope_of("nobody@example.org") is None


def test_anything_that_is_not_exactly_own_counts_as_external(tmp_path):
    path = tmp_path / "x.db"
    db = Database(path)
    store = RecipientStore(db)
    store.add("Anna", "anna@example.org", [], 1000.0, SRC, scope="own")
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA ignore_check_constraints = ON")
        conn.execute("UPDATE recipients SET scope='OWN '")
    assert store.scope_of("anna@example.org") == "external"
    assert store.find("anna@example.org").scope == "external"


def test_set_scope_is_audited_and_only_for_active_recipients(store, db):
    store.add("Anna", "anna@example.org", [], 1000.0, SRC)
    assert store.set_scope("anna@example.org", "own", 1100.0, SRC).scope == "own"
    events = [(e["event"], e["detail"]) for e in db.audit_events() if e["event"] == "recipient_scope_changed"]
    assert events == [("recipient_scope_changed", "scope: external -> own")]
    store.set_scope("anna@example.org", "own", 1200.0, SRC)  # no change: nothing new in the log
    assert len([e for e in db.audit_events() if e["event"] == "recipient_scope_changed"]) == 1
    store.suggest("o", "Carla", "carla@example.org", "", 1000.0)
    with pytest.raises(ValidationError):
        store.set_scope("carla@example.org", "own", 1300.0, SRC)
    with pytest.raises(ValidationError):
        store.set_scope("nobody@example.org", "own", 1300.0, SRC)


def test_a_record_never_keeps_its_old_scope_when_it_comes_back(store):
    store.add("Anna", "anna@example.org", [], 1000.0, SRC, scope="own")
    store.remove("anna@example.org", 1100.0, SRC)
    assert store.add("Anna", "anna@example.org", [], 1200.0, SRC).scope == "external"
    store.add("Berta", "berta@example.org", [], 1000.0, SRC, scope="own")
    store.remove("berta@example.org", 1100.0, SRC)
    store.suggest("o", "Berta", "berta@example.org", "", 1200.0)
    assert store.find("berta@example.org").scope == "external"
    assert store.approve("berta@example.org", 1300.0, SRC).scope == "external"


def test_the_old_recipients_file_is_taken_over_as_external(store):
    store.import_list_once(RecipientList([Recipient("Me", "me@example.com", ())]), 1000.0)
    assert store.scope_of("me@example.com") == "external"


def test_the_ai_side_cannot_change_a_scope(store):
    """Suggesting never makes anything own, and a suggestion for an own address stays external."""
    store.add("Anna", "anna@example.org", [], 1000.0, SRC, scope="own")
    assert store.suggest("o", "Anna", "anna@example.org", "", 1100.0).status == "already_allowed"
    assert store.scope_of("anna@example.org") == "own"  # unchanged, not downgraded, not touched
