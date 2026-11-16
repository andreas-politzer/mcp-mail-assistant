"""File access rules in the database: evaluation, history, one-time import, and the check that runs
inside the claim transaction. The path rules themselves are tested in test_attachment_policy.py."""

import dataclasses
import sqlite3
import threading
import unicodedata

import pytest

from outbox_mcp.errors import AttachmentRejected, DraftNotSendable, RecipientNotAllowed
from outbox_mcp.security.attachments import AttachmentPolicy, path_key
from outbox_mcp.security.rate_limiter import RateLimits
from outbox_mcp.service import OutboxService
from outbox_mcp.storage.database import Database

NOW = 1_000_000.0
LIMITS = RateLimits(max_prepares_per_hour=50, max_sends_per_hour=50, max_sends_per_day=50)


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "vault"
    for folder in ("Projects", "Projects/Privat", "Projects/Privatkopie", "Banking", "Banking-Alt"):
        (r / folder).mkdir(parents=True)
    for name in ("Projects/a.md", "Projects/Privat/b.md", "Projects/Privatkopie/c.md", "Banking/x.md", "Banking-Alt/y.md"):
        (r / name).write_text("Text " + name, encoding="utf-8")
    return r


def policy_for(db, root):
    return AttachmentPolicy(root, [], [], rules=lambda: db.rule_lists("attach", root))


def allowed(db, root, path):
    try:
        policy_for(db, root).still_allowed(path)
        return True
    except AttachmentRejected:
        return False


def rule(db, root, path, effect, scope="attach"):
    db.set_file_rule(scope, root, path, effect, NOW, "admin:test")


# -- schema ----------------------------------------------------------------------------

def test_version_3_database_is_upgraded_and_keeps_its_rows(tmp_path):
    path = tmp_path / "v3.db"
    db = Database(path)
    db.set_file_rule("attach", "/r", "Projects", "allow", NOW, "admin:test")
    with sqlite3.connect(path) as conn:
        conn.executescript("DROP TABLE file_access_rules; DROP TABLE settings; PRAGMA user_version = 3;")
    again = Database(path)
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 6
        assert conn.execute("SELECT COUNT(*) FROM file_access_rules").fetchone()[0] == 0
    assert again.rule_lists("attach", "/r") == ([], [])
    Database(path)  # opening once more is harmless


def test_rule_rows_are_never_deleted(db):
    rule(db, "/r", "Projects", "allow")
    with sqlite3.connect(db.path) as conn:
        with pytest.raises(sqlite3.DatabaseError, match="rule history is kept"):
            conn.execute("DELETE FROM file_access_rules")


# -- evaluation ------------------------------------------------------------------------

def test_nothing_is_allowed_without_rules(db, root):
    assert not allowed(db, root, "Projects/a.md")


def test_allowed_folder_allows_files_below_it(db, root):
    rule(db, root, "Projects", "allow")
    assert allowed(db, root, "Projects/a.md")
    assert allowed(db, root, "Projects/Privat/b.md")
    assert not allowed(db, root, "Banking/x.md")


def test_deny_wins_over_an_allowed_subfolder_and_a_parent_allow(db, root):
    rule(db, root, "Projects", "allow")
    rule(db, root, "Projects/Privat", "deny")
    assert allowed(db, root, "Projects/a.md")
    assert not allowed(db, root, "Projects/Privat/b.md")
    # a deny higher up cannot be undone by allowing something below it
    rule(db, root, "Banking", "deny")
    rule(db, root, "Banking/Freigabe", "allow")
    assert not allowed(db, root, "Banking/Freigabe/z.md")


def test_a_rule_matches_whole_path_components_only(db, root):
    rule(db, root, "Projects", "allow")
    rule(db, root, "Projects/Privat", "deny")
    assert allowed(db, root, "Projects/Privatkopie/c.md")  # Privat does not hit Privatkopie
    rule(db, root, "Banking", "allow")
    assert not allowed(db, root, "Banking-Alt/y.md")  # Banking does not allow Banking-Alt


def test_a_single_file_can_be_blocked_but_not_allowed(db, root):
    rule(db, root, "Projects", "allow")
    rule(db, root, "Projects/a.md", "deny")
    assert not allowed(db, root, "Projects/a.md")
    assert allowed(db, root, "Projects/Privat/b.md")
    with pytest.raises(ValueError, match="single file"):
        rule(db, root, "Banking/x.md", "allow")
    assert not allowed(db, root, "Banking/x.md")


def test_case_and_unicode_variants_hit_the_same_rule(db, root):
    nfc = unicodedata.normalize("NFC", "Übersicht")
    nfd = unicodedata.normalize("NFD", "Übersicht")
    rule(db, root, "Projects", "allow")
    rule(db, root, f"Projects/{nfd}", "deny")
    assert not allowed(db, root, f"Projects/{nfc.upper()}/n.md")
    assert not allowed(db, root, f"Projects/{nfc}/n.md")
    assert allowed(db, root, "Projects/Uebersicht/n.md")


def test_cleared_rules_do_not_count(db, root):
    rule(db, root, "Projects", "allow")
    rule(db, root, "Projects", "cleared")
    assert not allowed(db, root, "Projects/a.md")
    rule(db, root, "Projects", "allow")  # the same row can be set again
    assert allowed(db, root, "Projects/a.md")
    assert len(db.file_rules("attach", root)) == 1


def test_rules_of_another_root_folder_are_not_used(db, root, tmp_path):
    rule(db, root, "Projects", "allow")
    other = tmp_path / "elsewhere"
    other.mkdir()
    assert db.rule_lists("attach", other) == ([], [])
    assert db.rule_lists("attach", root)[0] == [("projects",)]


def test_read_rules_do_not_affect_attaching(db, root):
    rule(db, root, "Projects", "allow", scope="read")
    assert not allowed(db, root, "Projects/a.md")
    assert db.rule_lists("read", root)[0] == [("projects",)]


@pytest.mark.parametrize("bad", ["", "/abs", "~/x", "a/../b", "a//b", "a/", "./a", "a\\b", "a\x00b"])
def test_bad_rule_paths_are_refused(db, bad):
    with pytest.raises(ValueError):
        rule(db, "/r", bad, "deny")


def test_symlink_out_of_the_root_is_still_refused_with_database_rules(db, root, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "o.md").write_text("draussen", encoding="utf-8")
    (root / "Projects" / "link").symlink_to(outside)
    rule(db, root, "Projects", "allow")
    with pytest.raises(AttachmentRejected, match="symbolic link"):
        policy_for(db, root).load(["Projects/link/o.md"])


# -- history and audit -----------------------------------------------------------------

def test_a_rule_change_is_audited_with_before_and_after(db):
    rule(db, "/r", "Projects", "allow")
    rule(db, "/r", "Projects", "deny")
    events = [e for e in db.audit_events() if e["event"] == "file_rule_set"]
    assert [e["detail"] for e in events] == [
        "scope=attach path=Projects none -> allow",
        "scope=attach path=Projects allow -> deny",
    ]
    assert all(e["owner"] == "admin:test" for e in events)


def test_the_rule_and_its_audit_entry_are_saved_together(db, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("audit failed")

    monkeypatch.setattr(Database, "_audit", staticmethod(boom))
    with pytest.raises(RuntimeError):
        rule(db, "/r", "Projects", "allow")
    assert db.file_rules("attach", "/r") == []


# -- one-time import from the configuration ---------------------------------------------

def test_configuration_lists_are_taken_over_once(db):
    assert db.import_env_rules_once("/r", ["Projects"], ["Projects/Privat"], NOW) == 2
    assert db.rule_lists("attach", "/r") == ([("projects",)], [("projects", "privat")])
    assert db.import_env_rules_once("/r", ["Projects"], ["Projects/Privat"], NOW) == 0
    imported = [e for e in db.audit_events() if "from configuration" in (e["detail"] or "")]
    assert len(imported) == 2


def test_a_cleared_rule_does_not_come_back_from_the_configuration(db):
    db.import_env_rules_once("/r", ["Projects"], [], NOW)
    rule(db, "/r", "Projects", "cleared")
    db.import_env_rules_once("/r", ["Projects"], [], NOW + 1)
    assert db.rule_lists("attach", "/r") == ([], [])


def test_a_new_root_folder_gets_its_own_import(db):
    db.import_env_rules_once("/a", ["Projects"], [], NOW)
    assert db.rule_lists("attach", "/b") == ([], [])
    assert db.import_env_rules_once("/b", ["Notes"], [], NOW) == 1
    assert db.rule_lists("attach", "/a")[0] == [("projects",)]


# -- the check runs inside the claim transaction -----------------------------------------

@pytest.fixture
def att_config(config, root):
    return dataclasses.replace(
        config, attachment_root=root, attachment_allowed_folders=("Projects",),
        attachment_denied_folders=("Projects/Privat",),
    )


@pytest.fixture
def svc(att_config, recipients, db, sender, clock):
    return OutboxService(att_config, recipients, db, sender, clock)


def prepare(svc, attachments=("Projects/a.md",)):
    return svc.prepare_email("o", "Ferdinand", "Betreff", "Hallo", list(attachments))["draft_id"]


def _raw_rule(db, root, path, effect):
    import os
    with sqlite3.connect(db.path) as conn:
        conn.execute(
            "INSERT INTO file_access_rules(scope,root_path,path_key,path_display,effect,changed_at,changed_by)"
            " VALUES ('attach',?,?,?,?,0,'x') ON CONFLICT(scope,root_path,path_key) DO UPDATE SET effect=excluded.effect",
            (os.path.normpath(str(root)), path_key(path), path, effect),
        )


def test_a_rule_change_by_another_connection_before_sending_stops_the_mail(svc, att_config, db, sender, clock):
    """The send-time check on its own: a rule written without any consequence (no draft cancelled)."""
    draft_id = prepare(svc)
    _raw_rule(Database(db.path), att_config.attachment_root, "Projects", "deny")
    with pytest.raises(AttachmentRejected, match="no longer allowed"):
        svc.send_email("o", draft_id)
    assert sender.sent == []
    assert db.get_draft(draft_id, "o").status == "pending"
    assert db.count_events("claimed") == 0


def test_a_rule_change_through_the_database_cancels_the_draft(svc, att_config, db, sender, clock):
    from outbox_mcp.errors import DraftNotSendable
    draft_id = prepare(svc)
    Database(db.path).set_file_rule("attach", att_config.attachment_root, "Projects", "deny", clock(), "admin:test")
    with pytest.raises(DraftNotSendable):
        svc.send_email("o", draft_id)
    assert sender.sent == [] and db.get_draft(draft_id, "o").status == "cancelled"


def test_a_single_file_block_stops_the_mail(svc, att_config, db, sender, clock):
    draft_id = prepare(svc)
    _raw_rule(db, att_config.attachment_root, "Projects/a.md", "deny")
    with pytest.raises(AttachmentRejected):
        svc.send_email("o", draft_id)
    assert sender.sent == []


def test_a_recipient_blocked_before_sending_stops_the_mail(config, db, sender, clock):
    from outbox_mcp.storage.recipient_store import DbRecipientList, RecipientStore
    store = RecipientStore(db)
    store.add("Ferdinand", "ferdinand@example.org", [], NOW, "admin:test")
    live = OutboxService(config, DbRecipientList(store), db, sender, clock, recipient_store=store)
    draft_id = live.prepare_email("o", "Ferdinand", "S", "T")["draft_id"]
    RecipientStore(Database(db.path)).block("ferdinand@example.org", NOW, "admin:test")
    with pytest.raises(RecipientNotAllowed, match="no longer on the allowed list"):
        live.send_email("o", draft_id)
    assert sender.sent == []
    assert db.get_draft(draft_id, "o").status == "pending"


def test_the_authorization_runs_while_writers_are_locked_out(db, clock):
    draft = db.create_draft("o", "Me", "me@example.com", "S", "T", NOW, 600, LIMITS)
    seen = {}

    def authorize(current):
        other = sqlite3.connect(db.path, timeout=0.2, isolation_level=None)
        try:
            other.execute("BEGIN IMMEDIATE")
            seen["blocked"] = False
        except sqlite3.OperationalError:
            seen["blocked"] = True
        finally:
            other.close()

    db.claim_for_sending(draft.id, "o", NOW, LIMITS, authorize)
    assert seen == {"blocked": True}
    assert db.get_draft(draft.id, "o").status == "sending"


def test_nothing_is_claimed_when_the_authorization_fails(db):
    draft = db.create_draft("o", "Me", "me@example.com", "S", "T", NOW, 600, LIMITS)

    def authorize(current):
        raise AttachmentRejected("no")

    with pytest.raises(AttachmentRejected):
        db.claim_for_sending(draft.id, "o", NOW, LIMITS, authorize)
    assert db.get_draft(draft.id, "o").status == "pending"
    assert db.count_events("claimed") == 0
    db.claim_for_sending(draft.id, "o", NOW, LIMITS)  # a later claim still works


def test_a_draft_is_claimed_by_exactly_one_of_many_concurrent_senders(db):
    draft = db.create_draft("o", "Me", "me@example.com", "S", "T", NOW, 600, LIMITS)
    results = []

    def attempt():
        own = Database(db.path)
        try:
            own.claim_for_sending(draft.id, "o", NOW, LIMITS)
            results.append("claimed")
        except DraftNotSendable:
            results.append("refused")

    threads = [threading.Thread(target=attempt) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count("claimed") == 1 and results.count("refused") == 7
    assert db.count_events("claimed") == 1


def test_sending_the_same_draft_twice_sends_one_mail(svc, sender):
    draft_id = prepare(svc, attachments=())
    assert svc.send_email("o", draft_id)["status"] == "sent"
    with pytest.raises(DraftNotSendable):
        svc.send_email("o", draft_id)
    assert len(sender.sent) == 1


def test_a_failed_smtp_send_after_the_claim_can_not_be_retried(svc, sender):
    from outbox_mcp.mail.smtp_client import SmtpSendFailed
    draft_id = prepare(svc, attachments=())
    sender.error = SmtpSendFailed("refused")
    assert svc.send_email("o", draft_id)["status"] == "failed"
    sender.error = None
    with pytest.raises(DraftNotSendable):
        svc.send_email("o", draft_id)
    assert sender.sent == []
