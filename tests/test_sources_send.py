"""Work package 6b: attachments from file sources, checked again when sending."""

import sqlite3
import threading

import pytest

from outbox_mcp import sources as src_rules
from outbox_mcp.errors import AttachmentRejected, DraftNotSendable
from outbox_mcp.mail.smtp_client import SmtpSendFailed
from outbox_mcp.security.attachments import Attachment
from outbox_mcp.service import OutboxService
from outbox_mcp.storage.database import Database

ADMIN = "admin:test"


@pytest.fixture
def svc(config, recipients, db, sender, clock):
    return OutboxService(config, recipients, db, sender, clock)


@pytest.fixture
def src(db, clock):
    sid = db.create_source("upload", "folder", "Mac", clock(), ADMIN, origin_display="Documents")
    db.set_source_rule(sid, "Projekte", "allow", clock(), ADMIN)
    return sid


def upload(db, sid, path, text, now=1_000_000.0):
    data = text.encode()
    return db.put_stored_file(sid, path, data, src_rules.sha256_hex(data), now, ADMIN)


def prep(svc, refs, to="Ferdinand"):
    return svc.prepare_email("o", to, "Betreff", "Hallo", refs)


def raw(db, sql, args=()):
    with sqlite3.connect(db.path) as conn:
        conn.execute(sql, args)


def attachments_of_message(sender):
    message = sender.sent[0][0]
    return [(a.get_filename(), a.get_content()) for a in message.iter_attachments()]


# -- prepare -------------------------------------------------------------------------------


def test_an_uploaded_file_is_attached_and_the_answer_names_source_version_and_date(svc, db, src, sender):
    upload(db, src, "Projekte/Plan.md", "# Plan\n")
    d = prep(svc, ["Mac:Projekte/Plan.md"])
    (info,) = d["attachments"]
    assert info["name"] == "Plan.md" and info["source"] == "Mac" and info["path"] == "Projekte/Plan.md"
    assert info["version"] == src_rules.short_version(src_rules.sha256_hex(b"# Plan\n"))
    assert info["as_of"] == "1970-01-12"  # the clock of the tests is 1,000,000 s after the epoch
    assert "version" in d["instructions"] and "uploaded" in d["instructions"]
    (stored,) = db.attachments_of(d["draft_id"])
    assert (stored.source_id, stored.origin_key, stored.source) == (src, "projekte/plan.md", "Projekte/Plan.md")
    assert svc.send_email("o", d["draft_id"])["status"] == "sent"
    assert attachments_of_message(sender) == [("Plan.md", "# Plan\n")]


def test_the_label_is_found_in_any_case(svc, db, src):
    upload(db, src, "Projekte/Plan.md", "x")
    assert prep(svc, ["mac:Projekte/Plan.md"])["attachments"][0]["source"] == "Mac"


def test_without_a_known_source_name_the_path_cannot_be_resolved(svc, db, src):
    upload(db, src, "Projekte/Plan.md", "x")
    with pytest.raises(AttachmentRejected, match="Name the source"):
        prep(svc, ["Projekte/Plan.md"])  # no notes folder is configured here
    with pytest.raises(AttachmentRejected, match="Name the source"):
        prep(svc, ["Unbekannt:Projekte/Plan.md"])


@pytest.mark.parametrize("path", ["Projekte/missing.md", "Andere/Plan.md", "Projekte/Plan.pdf", "Projekte/../Plan.md"])
def test_missing_or_not_allowed_files_are_refused(svc, db, src, path):
    upload(db, src, "Projekte/Plan.md", "x")
    with pytest.raises(AttachmentRejected):
        prep(svc, [f"Mac:{path}"])


def test_a_blocked_or_purged_copy_cannot_be_attached(svc, db, src):
    upload(db, src, "Projekte/Plan.md", "x")
    db.set_source_rule(src, "Projekte/Plan.md", "deny", 1_000_000.0, ADMIN)
    with pytest.raises(AttachmentRejected, match="not available"):
        prep(svc, ["Mac:Projekte/Plan.md"])


def test_limits_duplicates_and_total_size_apply_across_sources(svc, db, src, config):
    for i in range(4):
        upload(db, src, f"Projekte/{i}.md", "x")
    with pytest.raises(AttachmentRejected, match="At most 3"):
        prep(svc, [f"Mac:Projekte/{i}.md" for i in range(4)])
    with pytest.raises(AttachmentRejected, match="listed twice"):
        prep(svc, ["Mac:Projekte/0.md", "mac:projekte/0.MD"])
    other = db.create_source("upload", "folder", "Zwei", 1_000_000.0, ADMIN)
    db.set_source_rule(other, "Projekte", "allow", 1_000_000.0, ADMIN)
    upload(db, other, "Projekte/0.md", "y")
    assert len(prep(svc, ["Mac:Projekte/0.md", "Zwei:Projekte/0.md"])["attachments"]) == 2  # same path, two sources
    for name in ("a", "b", "c"):
        upload(db, src, f"Projekte/{name}big.md", "x" * 190_000)
    with pytest.raises(AttachmentRejected, match="total size"):  # 3 x 190 KB is over the 500 KB total
        prep(svc, [f"Mac:Projekte/{name}big.md" for name in ("a", "b", "c")])


def test_a_single_file_source_is_named_by_label_and_file_name(svc, db, clock):
    sid = db.create_source("upload", "file", "Plan", clock(), ADMIN, file_name="Plan.md")
    upload(db, sid, "Plan.md", "einzeln")
    assert prep(svc, ["Plan:Plan.md"])["attachments"][0]["path"] == "Plan.md"
    with pytest.raises(AttachmentRejected):
        prep(svc, ["Plan:Other.md"])


# -- the check when sending ------------------------------------------------------------------


def test_a_replaced_file_makes_an_older_draft_unsendable_until_it_is_prepared_again(svc, db, src, sender):
    upload(db, src, "Projekte/Plan.md", "alt")
    d = prep(svc, ["Mac:Projekte/Plan.md"])
    upload(db, src, "Projekte/Plan.md", "neu")
    with pytest.raises(AttachmentRejected, match="changed"):
        svc.send_email("o", d["draft_id"])
    assert sender.sent == [] and db.get_draft(d["draft_id"], "o").status == "pending"
    assert db.count_events("claimed") == 0
    again = prep(svc, ["Mac:Projekte/Plan.md"])
    assert again["attachments"][0]["version"] != d["attachments"][0]["version"]
    assert svc.send_email("o", again["draft_id"])["status"] == "sent"
    assert attachments_of_message(sender) == [("Plan.md", "neu")]


def test_uploading_the_same_content_again_does_not_invalidate_a_draft(svc, db, src, sender):
    upload(db, src, "Projekte/Plan.md", "gleich")
    d = prep(svc, ["Mac:Projekte/Plan.md"])
    upload(db, src, "Projekte/Plan.md", "gleich")
    assert svc.send_email("o", d["draft_id"])["status"] == "sent"


def test_blocking_cancels_the_open_draft_and_deletes_its_copy_and_the_upload(svc, db, src, sender, clock):
    upload(db, src, "Projekte/Plan.md", "geheim-ish")
    keep = upload(db, src, "Projekte/Andere.md", "bleibt")
    d = prep(svc, ["Mac:Projekte/Plan.md", "Mac:Projekte/Andere.md"])
    effects = db.set_source_rule(src, "Projekte/Plan.md", "deny", clock(), ADMIN)
    assert effects == {"purged_files": 1, "cancelled_drafts": 1, "deleted_copies": 2}
    assert db.get_draft(d["draft_id"], "o").status == "cancelled"
    assert db.attachments_of(d["draft_id"]) == []  # both copies are gone, also the harmless one
    assert db.stored_content(src, "Projekte/Plan.md") is None
    assert db.stored_content(src, "Projekte/Andere.md")["version"] == keep["version"]
    with pytest.raises(DraftNotSendable):
        svc.send_email("o", d["draft_id"])
    assert sender.sent == []
    (event,) = [e for e in db.audit_events() if e["event"] == "draft_cancelled_by_block"]
    assert event["draft_id"] == d["draft_id"]


def test_a_draft_without_a_blocked_file_is_left_alone(svc, db, src, sender, clock):
    upload(db, src, "Projekte/Plan.md", "x")
    upload(db, src, "Projekte/Privat/Intern.md", "y")
    d = prep(svc, ["Mac:Projekte/Plan.md"])
    effects = db.set_source_rule(src, "Projekte/Privat", "deny", clock(), ADMIN)
    assert effects["cancelled_drafts"] == 0 and effects["purged_files"] == 1
    assert svc.send_email("o", d["draft_id"])["status"] == "sent"


def test_loosening_a_rule_cancels_nothing(svc, db, src, sender, clock):
    upload(db, src, "Projekte/Plan.md", "x")
    d = prep(svc, ["Mac:Projekte/Plan.md"])
    db.set_source_rule(src, "Weiteres", "allow", clock(), ADMIN)
    assert svc.send_email("o", d["draft_id"])["status"] == "sent"


def test_removing_a_source_cancels_its_drafts(svc, db, src, sender, clock):
    upload(db, src, "Projekte/Plan.md", "x")
    d = prep(svc, ["Mac:Projekte/Plan.md"])
    effects = db.remove_source(src, clock(), ADMIN)
    assert effects["cancelled_drafts"] == 1 and effects["purged_files"] == 1
    assert db.get_draft(d["draft_id"], "o").status == "cancelled" and db.attachments_of(d["draft_id"]) == []
    with pytest.raises(DraftNotSendable):
        svc.send_email("o", d["draft_id"])


def test_send_time_check_refuses_when_nothing_cancelled_the_draft(svc, db, sender, clock):
    """The check at send time on its own: every change below is written without its consequences."""
    changes = {
        "rule": lambda sid: raw(
            db, "INSERT INTO file_access_rules(scope,root_path,path_key,path_display,effect,changed_at,changed_by,"
            "source_id) VALUES ('attach',?,?,?,'deny',0,'x',?)", (f"source:{sid}", "projekte/plan.md", "Projekte/Plan.md", sid)),
        "source removed": lambda sid: raw(db, "UPDATE file_sources SET status='removed' WHERE id=?", (sid,)),
        "copy purged": lambda sid: raw(
            db, "UPDATE stored_files SET state='purged', purged_reason='blocked', content=NULL WHERE source_id=?", (sid,)),
        "copy replaced": lambda sid: raw(db, "UPDATE stored_files SET sha256='ab' WHERE source_id=?", (sid,)),
    }
    for name, change in changes.items():
        sid = db.create_source("upload", "folder", f"S {name}", clock(), ADMIN)
        db.set_source_rule(sid, "Projekte", "allow", clock(), ADMIN)
        upload(db, sid, "Projekte/Plan.md", "x")
        d = prep(svc, [f"S {name}:Projekte/Plan.md"])
        change(sid)
        with pytest.raises(AttachmentRejected, match="no longer allowed"):
            svc.send_email("o", d["draft_id"])
        assert db.get_draft(d["draft_id"], "o").status == "pending", name
    assert sender.sent == [] and db.count_events("claimed") == 0


def test_a_draft_that_does_not_record_its_source_is_refused(svc, db, sender, clock):
    d = db.create_draft(
        "o", "Ferdinand", "ferdinand@example.org", "S", "T", clock(), 600, svc.config.limits,
        [Attachment(name="x.md", data=b"x", sha256=src_rules.sha256_hex(b"x"), source="Projekte/x.md")],
    )
    with pytest.raises(AttachmentRejected, match="does not record"):
        svc.send_email("o", d.id)
    assert sender.sent == []


def test_copies_in_never_sent_drafts_are_deleted_but_sent_ones_are_kept(svc, db, src, sender, clock):
    upload(db, src, "Projekte/Plan.md", "x")
    failed = prep(svc, ["Mac:Projekte/Plan.md"])
    sender.error = SmtpSendFailed("refused")
    assert svc.send_email("o", failed["draft_id"])["status"] == "failed"
    sender.error = None
    cancelled = prep(svc, ["Mac:Projekte/Plan.md"])
    svc.cancel_draft("o", cancelled["draft_id"])
    sent = prep(svc, ["Mac:Projekte/Plan.md"])
    assert svc.send_email("o", sent["draft_id"])["status"] == "sent"
    unsent_pending = prep(svc, ["Mac:Projekte/Plan.md"])
    effects = db.set_source_rule(src, "Projekte", "deny", clock(), ADMIN)
    assert effects["cancelled_drafts"] == 1 and effects["deleted_copies"] == 3  # pending, failed, cancelled
    assert db.attachments_of(failed["draft_id"]) == [] and db.attachments_of(cancelled["draft_id"]) == []
    assert db.attachments_of(unsent_pending["draft_id"]) == []
    assert len(db.attachments_of(sent["draft_id"])) == 1  # the record of what went out stays


def test_a_notes_folder_file_and_an_upload_can_travel_together(config, recipients, db, sender, clock, tmp_path):
    import dataclasses
    vault = tmp_path / "vault"
    (vault / "Projects").mkdir(parents=True)
    (vault / "Projects/note.md").write_text("Notiz", encoding="utf-8")
    cfg = dataclasses.replace(config, attachment_root=vault, attachment_allowed_folders=("Projects",))
    service = OutboxService(cfg, recipients, db, sender, clock)
    sid = db.create_source("upload", "folder", "Mac", clock(), ADMIN)
    db.set_source_rule(sid, "Projekte", "allow", clock(), ADMIN)
    upload(db, sid, "Projekte/Plan.md", "Plan")
    d = prep(service, ["Projects/note.md", "Mac:Projekte/Plan.md"])
    assert [a["source"] for a in d["attachments"]] == ["notes", "Mac"]
    assert service.send_email("o", d["draft_id"])["status"] == "sent"
    assert sorted(attachments_of_message(sender)) == [("Plan.md", "Plan"), ("note.md", "Notiz")]
    # blocking the notes folder cancels a draft that holds its file, whatever else it holds
    d2 = prep(service, ["Projects/note.md", "Mac:Projekte/Plan.md"])
    db.set_file_rule("attach", vault, "Projects", "deny", clock(), ADMIN)
    assert db.get_draft(d2["draft_id"], "o").status == "cancelled"


def test_removing_the_notes_source_stops_notes_attachments(config, recipients, db, sender, clock, tmp_path):
    import dataclasses
    vault = tmp_path / "vault"
    (vault / "Projects").mkdir(parents=True)
    (vault / "Projects/note.md").write_text("Notiz", encoding="utf-8")
    cfg = dataclasses.replace(config, attachment_root=vault, attachment_allowed_folders=("Projects",))
    service = OutboxService(cfg, recipients, db, sender, clock)
    d = prep(service, ["Projects/note.md"])
    db.remove_source(service.notes_source_id, clock(), ADMIN)
    assert db.get_draft(d["draft_id"], "o").status == "cancelled"
    with pytest.raises(AttachmentRejected):
        prep(service, ["Projects/note.md"])
    # a restart registers the folder again as a NEW source that allows nothing
    again = OutboxService(cfg, recipients, db, sender, clock)
    assert again.notes_source_id != service.notes_source_id
    with pytest.raises(AttachmentRejected):
        prep(again, ["Projects/note.md"])


# -- block and send racing each other --------------------------------------------------------


def test_a_block_and_a_send_at_the_same_moment_never_send_after_the_block(svc, db, src, sender, clock):
    for i in range(12):
        upload(db, src, f"Projekte/F{i}/Plan.md", "x")
        d = prep(svc, [f"Mac:Projekte/F{i}/Plan.md"])
        barrier = threading.Barrier(2)
        outcome = {}

        def do_send():
            barrier.wait()
            try:
                outcome["send"] = svc.send_email("o", d["draft_id"])["status"]
            except Exception as exc:  # refused or no longer pending
                outcome["send"] = type(exc).__name__

        def do_block():
            barrier.wait()
            Database(db.path).set_source_rule(src, f"Projekte/F{i}", "deny", clock(), ADMIN)

        threads = [threading.Thread(target=do_send), threading.Thread(target=do_block)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        events = db.audit_events()
        claimed = [e["seq"] for e in events if e["event"] == "claimed" and e["draft_id"] == d["draft_id"]]
        blocked = [e["seq"] for e in events if e["event"] == "source_rule_set" and f"F{i}" in e["detail"]]
        assert len(blocked) == 1
        if outcome["send"] == "sent":
            assert claimed and claimed[0] < blocked[0], "a mail left after the block was in force"
        else:
            assert not claimed
            assert db.get_draft(d["draft_id"], "o").status in ("cancelled", "pending")
