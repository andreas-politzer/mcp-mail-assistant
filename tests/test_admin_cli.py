"""The admin command line: the only way (for now) to approve, block or add recipients."""

import io
import sqlite3

import pytest

from outbox_mcp.admin_cli import main
from outbox_mcp.storage.database import Database
from outbox_mcp.storage.recipient_store import RecipientStore


@pytest.fixture
def env(tmp_path):
    return {"OUTBOX_DB_PATH": str(tmp_path / "mail.db")}


@pytest.fixture
def store(env):
    return RecipientStore(Database(env["OUTBOX_DB_PATH"]))


def run(args, env, answers=()):
    out = io.StringIO()
    feed = iter(answers)
    code = main(args, env=env, stdout=out, input_func=lambda prompt="": next(feed))
    return code, out.getvalue()


def suggest(store, note="Neffe"):
    store.suggest("o", "Ferdinand", "ferdinand@example.org", note, 1000.0)


def test_pending_is_empty_at_first(env, store):
    code, out = run(["pending"], env)
    assert code == 0 and "No open suggestions" in out


def test_pending_shows_name_and_address_in_plain_text_and_marks_claudes_note(env, store):
    suggest(store, note="Neffe, Adresse aus dem Gespräch")
    code, out = run(["pending"], env)
    assert code == 0
    assert "Ferdinand" in out and "ferdinand@example.org" in out
    assert "Note from Claude (not verified)" in out and "Neffe, Adresse aus dem Gespräch" in out


def test_approve_asks_first_and_shows_name_and_address(env, store):
    suggest(store)
    code, out = run(["approve", "ferdinand@example.org", "--alias", "Ferdi"], env, answers=["j"])
    assert code == 0 and "ferdinand@example.org" in out and "Approved" in out
    assert [r.aliases for r in store.active()] == [("Ferdi",)]


def test_approve_declined_changes_nothing(env, store):
    suggest(store)
    code, out = run(["approve", "ferdinand@example.org"], env, answers=["n"])
    assert code == 0 and "Not approved" in out
    assert store.active() == () and len(store.pending()) == 1


def test_approve_with_yes_does_not_ask(env, store):
    suggest(store)
    code, _ = run(["approve", "ferdinand@example.org", "--yes"], env)
    assert code == 0 and len(store.active()) == 1


def test_approve_by_id_and_rename(env, store):
    suggest(store)
    rid = store.pending()[0].id
    code, _ = run(["approve", str(rid), "--name", "Ferdinand P.", "--yes"], env)
    assert code == 0 and store.active()[0].name == "Ferdinand P."


def test_reject_block_unblock_remove(env, store):
    suggest(store)
    assert run(["reject", "ferdinand@example.org"], env)[0] == 0
    assert store.pending() == []
    store.add("Anna", "anna@example.org", [], 1000.0, "admin:test")
    assert run(["block", "anna@example.org"], env)[0] == 0 and store.active() == ()
    assert run(["unblock", "anna@example.org"], env)[0] == 0
    assert run(["add", "Anna", "anna@example.org", "--alias", "Annchen"], env)[0] == 0
    assert run(["remove", "anna@example.org"], env)[0] == 0 and store.active() == ()


def test_add_creates_an_active_recipient(env, store):
    code, out = run(["add", "Anna", "anna@example.org", "--alias", "Annchen", "--alias", "A"], env)
    assert code == 0 and "anna@example.org" in out
    assert store.active()[0].aliases == ("Annchen", "A")


def test_list_shows_every_status(env, store):
    suggest(store)
    store.add("Anna", "anna@example.org", [], 1000.0, "admin:test")
    code, out = run(["list"], env)
    assert code == 0 and "suggested" in out and "active" in out
    assert "ferdinand@example.org" in out and "anna@example.org" in out


def test_errors_are_short_messages_with_a_nonzero_code(env, store):
    code, out = run(["approve", "nobody@example.org", "--yes"], env)
    assert code == 1 and "Traceback" not in out and "nobody@example.org" in out
    code, out = run(["add", "X", "not-an-address"], env)
    assert code == 1 and store.all_records() == []


def test_admin_events_are_audited_as_cli(env, store):
    suggest(store)
    run(["approve", "ferdinand@example.org", "--yes"], env)
    with sqlite3.connect(env["OUTBOX_DB_PATH"]) as conn:
        owners = [r[0] for r in conn.execute("SELECT owner FROM audit_log WHERE event='recipient_approved'")]
    assert owners == ["admin:cli"]


def test_needs_the_database_location_and_no_smtp_settings(tmp_path):
    code, out = run(["pending"], {})
    assert code == 2 and "OUTBOX_DB_PATH" in out and "Traceback" not in out
    code, _ = run(["pending"], {"OUTBOX_DB_PATH": str(tmp_path / "m.db")})
    assert code == 0  # no SMTP password needed for administration
