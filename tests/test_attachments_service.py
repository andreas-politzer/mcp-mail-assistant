import dataclasses
import sqlite3
from email import policy as email_policy

import pytest

from outbox_mcp.config import Config
from outbox_mcp.errors import AttachmentRejected, ConfigError
from outbox_mcp.service import OutboxService
from outbox_mcp.storage.database import Database

BASE = {
    "OUTBOX_SMTP_HOST": "smtp.example.com", "OUTBOX_SMTP_USER": "me@example.com",
    "OUTBOX_SMTP_PASSWORD": "pw", "OUTBOX_FROM_ADDRESS": "me@example.com",
}


@pytest.fixture
def vault(tmp_path):
    v = tmp_path / "vault"
    (v / "Projects").mkdir(parents=True)
    (v / "Banking").mkdir()
    (v / "Projects/note.md").write_text("# Projektnotiz\n\nSchöne Grüße!\n", encoding="utf-8")
    (v / "Projects/other.md").write_text("zweite Notiz", encoding="utf-8")
    (v / "Banking/x.md").write_text("Konto", encoding="utf-8")
    return v


@pytest.fixture
def att_config(config, vault):
    return dataclasses.replace(
        config, attachment_root=vault, attachment_allowed_folders=("Projects",),
        attachment_denied_folders=("Projects/Private",),
    )


@pytest.fixture
def svc(att_config, recipients, db, sender, clock):
    return OutboxService(att_config, recipients, db, sender, clock)


def prep(svc, attachments=None, **kw):
    return svc.prepare_email("o", kw.get("to", "Ferdinand"), "Betreff", "Hallo", attachments)


# -- prepare ------------------------------------------------------------------------

def test_prepare_lists_name_and_size_but_no_hash(svc, vault):
    d = prep(svc, ["Projects/note.md"])
    size = (vault / "Projects/note.md").stat().st_size
    assert d["attachments"] == [{"name": "note.md", "size_bytes": size}]
    assert "sha256" not in str(d["attachments"])
    assert "attachment" in d["instructions"].lower()


def test_prepare_without_attachments_has_empty_list(svc):
    assert prep(svc)["attachments"] == []


def test_attachments_not_enabled(config, recipients, db, sender, clock):
    plain = OutboxService(config, recipients, db, sender, clock)
    with pytest.raises(AttachmentRejected, match="not enabled"):
        plain.prepare_email("o", "Ferdinand", "S", "T", ["Projects/note.md"])
    assert db.count_events("prepared") == 0
    assert plain.prepare_email("o", "Ferdinand", "S", "T", [])["attachments"] == []


def test_rejected_attachment_creates_no_draft(svc, db):
    with pytest.raises(AttachmentRejected):
        prep(svc, ["Banking/x.md"])
    assert db.count_events("prepared") == 0


def test_attachments_must_be_a_list(svc):
    with pytest.raises(AttachmentRejected, match="list"):
        prep(svc, "Projects/note.md")


def test_attachment_changes_the_fingerprint(svc):
    assert prep(svc)["fingerprint"] != prep(svc, ["Projects/note.md"])["fingerprint"]
    assert prep(svc, ["Projects/note.md"])["fingerprint"] != prep(svc, ["Projects/other.md"])["fingerprint"]


def test_audit_records_name_size_and_hash_but_not_content(svc, db):
    prep(svc, ["Projects/note.md"])
    event = [e for e in db.audit_events() if e["event"] == "prepared"][-1]
    assert "note.md" in event["detail"] and "sha256" in event["detail"]
    assert "Schöne" not in event["detail"]


# -- send ---------------------------------------------------------------------------

def test_send_attaches_the_markdown_file(svc, sender, vault):
    d = prep(svc, ["Projects/note.md", "Projects/other.md"])
    assert svc.send_email("o", d["draft_id"])["status"] == "sent"
    msg = sender.sent[0][0]
    parts = list(msg.iter_attachments())
    assert [p.get_filename() for p in parts] == ["note.md", "other.md"]
    assert parts[0].get_content_type() == "text/markdown"
    assert parts[0].get_content_disposition() == "attachment"
    assert parts[0].get_content() == "# Projektnotiz\n\nSchöne Grüße!\n"
    assert msg.get_body(("plain",)).get_content().strip() == "Hallo"
    assert msg["Bcc"] is None and msg["Cc"] is None


def test_mail_without_attachments_is_still_plain(svc, sender):
    svc.send_email("o", prep(svc)["draft_id"])
    msg = sender.sent[0][0]
    assert not msg.is_multipart() and msg.get_content_type() == "text/plain"


def test_sent_file_is_the_prepared_copy(svc, sender, vault):
    d = prep(svc, ["Projects/note.md"])
    (vault / "Projects/note.md").write_text("GEÄNDERT nach der Bestätigung", encoding="utf-8")
    svc.send_email("o", d["draft_id"])
    assert "Schöne Grüße" in list(sender.sent[0][0].iter_attachments())[0].get_content()


def test_sent_even_if_file_is_gone_after_prepare(svc, sender, vault):
    d = prep(svc, ["Projects/note.md"])
    (vault / "Projects/note.md").unlink()
    assert svc.send_email("o", d["draft_id"])["status"] == "sent"
    assert len(list(sender.sent[0][0].iter_attachments())) == 1


def test_message_survives_serialisation(svc, sender):
    svc.send_email("o", prep(svc, ["Projects/note.md"])["draft_id"])
    raw = sender.sent[0][0].as_bytes(policy=email_policy.SMTP)
    assert b"filename=note.md" in raw or b'filename="note.md"' in raw


# -- database -----------------------------------------------------------------------

def test_attachment_rows_are_immutable(svc, db):
    d = prep(svc, ["Projects/note.md"])
    with sqlite3.connect(db.path) as conn:
        with pytest.raises(sqlite3.DatabaseError, match="immutable"):
            conn.execute("UPDATE draft_attachments SET name='evil.md'")
        with pytest.raises(sqlite3.DatabaseError, match="active"):
            conn.execute("DELETE FROM draft_attachments")
        conn.rollback()
    assert db.attachments_of(d["draft_id"])[0].name == "note.md"


def test_no_attachments_can_be_added_to_a_sent_draft(svc, db):
    d = prep(svc)
    svc.send_email("o", d["draft_id"])
    with sqlite3.connect(db.path) as conn:
        with pytest.raises(sqlite3.DatabaseError, match="pending"):
            conn.execute(
                "INSERT INTO draft_attachments(draft_id,position,name,data,size_bytes,sha256)"
                " VALUES (?,?,?,?,?,?)", (d["draft_id"], 0, "x.md", b"x", 1, "0" * 64))


def test_purge_removes_attachments_with_their_draft(svc, db, clock):
    d = prep(svc, ["Projects/note.md"])
    svc.send_email("o", d["draft_id"])
    clock.t += 8 * 86400
    svc.list_recipients()
    prep(svc)  # triggers housekeeping
    with sqlite3.connect(db.path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM draft_attachments WHERE draft_id=?", (d["draft_id"],)).fetchone()[0] == 0


def test_schema_version_is_set(db):
    with sqlite3.connect(db.path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == Database.SCHEMA_VERSION


OLD_SCHEMA = """
CREATE TABLE drafts (
    id TEXT PRIMARY KEY, owner TEXT NOT NULL, created_at REAL NOT NULL, expires_at REAL NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending','sending','sent','failed','unknown','expired','cancelled')),
    recipient_name TEXT NOT NULL, recipient_address TEXT NOT NULL, subject TEXT NOT NULL, body TEXT NOT NULL,
    body_sha256 TEXT NOT NULL, sending_at REAL, finished_at REAL, detail TEXT);
CREATE TABLE audit_log (
    seq INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, event TEXT NOT NULL, draft_id TEXT, owner TEXT,
    recipient_address TEXT, subject TEXT, body_sha256 TEXT, detail TEXT);
INSERT INTO drafts VALUES ('drf_old','o',1.0,9999999999.0,'pending','Ferdinand','ferdinand@example.org',
    'Alt','Alter Text','abc',NULL,NULL,NULL);
INSERT INTO audit_log(ts,event,draft_id,owner) VALUES (1.0,'prepared','drf_old','o');
"""


def test_existing_database_is_migrated_without_losing_data(tmp_path):
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as conn:
        conn.executescript(OLD_SCHEMA)
    db = Database(path)
    assert db.get_draft("drf_old", "o").body == "Alter Text"
    assert db.attachments_of("drf_old") == []
    assert len(db.audit_events()) == 1
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == Database.SCHEMA_VERSION
    Database(path)  # opening twice is harmless
    assert len(Database(path).audit_events()) == 1


def test_database_from_the_future_is_refused(tmp_path):
    path = tmp_path / "future.db"
    Database(path)
    with sqlite3.connect(path) as conn:
        conn.execute(f"PRAGMA user_version = {Database.SCHEMA_VERSION + 1}")
    with pytest.raises(ConfigError, match="newer"):
        Database(path)


# -- config -------------------------------------------------------------------------

def test_attachments_are_off_by_default():
    c = Config.from_env(BASE)
    assert c.attachment_root is None and c.attachment_allowed_folders == ()
    assert (c.max_attachments, c.max_attachment_bytes, c.max_attachments_total_bytes) == (3, 200_000, 500_000)


def test_attachment_settings_from_env():
    c = Config.from_env({
        **BASE, "OUTBOX_ATTACHMENT_ROOT": "/srv/vault",
        "OUTBOX_ATTACHMENT_ALLOWED_FOLDERS": "Projects, Notes/Public ,",
        "OUTBOX_ATTACHMENT_DENIED_FOLDERS": "Projects/Private",
        "OUTBOX_MAX_ATTACHMENTS": "2",
    })
    assert str(c.attachment_root) == "/srv/vault"
    assert c.attachment_allowed_folders == ("Projects", "Notes/Public")
    assert c.attachment_denied_folders == ("Projects/Private",)
    assert c.max_attachments == 2


def test_root_without_allowed_folders_is_a_config_error():
    with pytest.raises(ConfigError, match="ALLOWED_FOLDERS"):
        Config.from_env({**BASE, "OUTBOX_ATTACHMENT_ROOT": "/srv/vault"})


def test_relative_attachment_root_is_a_config_error():
    with pytest.raises(ConfigError, match="absolute"):
        Config.from_env({**BASE, "OUTBOX_ATTACHMENT_ROOT": "vault", "OUTBOX_ATTACHMENT_ALLOWED_FOLDERS": "Projects"})


# -- the rules are checked again when the mail is sent -------------------------------------

def other_service(att_config, recipients, db, sender, clock, **changes):
    return OutboxService(dataclasses.replace(att_config, **changes), recipients, db, sender, clock)


def test_the_origin_path_is_stored_with_the_attachment(svc, db):
    d = prep(svc, ["Projects/note.md"])
    assert db.attachments_of(d["draft_id"])[0].source == "Projects/note.md"


def test_send_is_refused_when_the_folder_is_no_longer_allowed(svc, att_config, recipients, db, sender, clock):
    d = prep(svc, ["Projects/note.md"])
    later = other_service(att_config, recipients, db, sender, clock, attachment_allowed_folders=("Elsewhere",))
    with pytest.raises(AttachmentRejected, match="no longer allowed"):
        later.send_email("o", d["draft_id"])
    assert sender.sent == []
    assert db.get_draft(d["draft_id"], "o").status == "pending"


def test_send_is_refused_when_the_folder_is_now_blocked(svc, att_config, recipients, db, sender, clock):
    d = prep(svc, ["Projects/note.md"])
    later = other_service(att_config, recipients, db, sender, clock, attachment_denied_folders=("Projects",))
    with pytest.raises(AttachmentRejected, match="no longer allowed"):
        later.send_email("o", d["draft_id"])
    assert sender.sent == []


def test_send_is_refused_when_attachments_are_switched_off(svc, att_config, recipients, db, sender, clock):
    d = prep(svc, ["Projects/note.md"])
    later = other_service(att_config, recipients, db, sender, clock, attachment_root=None)
    with pytest.raises(AttachmentRejected, match="no longer allowed"):
        later.send_email("o", d["draft_id"])
    assert sender.sent == []


def test_a_draft_without_a_stored_origin_is_not_sent(svc, db, sender):
    d = prep(svc)  # a draft that was prepared before the origin was recorded
    with sqlite3.connect(db.path) as conn:
        conn.execute(
            "INSERT INTO draft_attachments(draft_id,position,name,data,size_bytes,sha256)"
            " VALUES (?,?,?,?,?,?)", (d["draft_id"], 0, "old.md", b"alt", 3, "0" * 64))
    with pytest.raises(AttachmentRejected, match="prepare the mail again"):
        svc.send_email("o", d["draft_id"])
    assert sender.sent == []


def test_a_draft_without_attachments_does_not_depend_on_the_rules(svc, att_config, recipients, db, sender, clock):
    d = prep(svc)
    later = other_service(att_config, recipients, db, sender, clock, attachment_root=None)
    assert later.send_email("o", d["draft_id"])["status"] == "sent"


OLD_V2 = """
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
INSERT INTO draft_attachments VALUES ('drf_old',0,'old.md',x'616c74',3,'abc');
PRAGMA user_version = 2;
"""


def test_version_2_database_gets_the_origin_column(tmp_path):
    path = tmp_path / "v2.db"
    with sqlite3.connect(path) as conn:
        conn.executescript(OLD_V2)
    db = Database(path)
    old = db.attachments_of("drf_old")
    assert [(a.name, a.source) for a in old] == [("old.md", "")]
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == Database.SCHEMA_VERSION == 3
        columns = [r[1] for r in conn.execute("PRAGMA table_info(draft_attachments)")]
    assert columns.count("source_path") == 1
    Database(path)  # a second open is harmless
    with sqlite3.connect(path) as conn:
        assert [r[1] for r in conn.execute("PRAGMA table_info(draft_attachments)")].count("source_path") == 1


def test_an_interrupted_migration_is_finished_at_the_next_start(tmp_path):
    """The column was added but the version was not stored yet (a crash in between)."""
    path = tmp_path / "half.db"
    with sqlite3.connect(path) as conn:
        conn.executescript(OLD_V2)
        conn.execute("ALTER TABLE draft_attachments ADD COLUMN source_path TEXT")
    db = Database(path)
    assert [(a.name, a.source) for a in db.attachments_of("drf_old")] == [("old.md", "")]
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == Database.SCHEMA_VERSION
        assert [r[1] for r in conn.execute("PRAGMA table_info(draft_attachments)")].count("source_path") == 1
