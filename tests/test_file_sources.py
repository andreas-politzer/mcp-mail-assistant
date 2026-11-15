"""Work package 6a: file sources, stored copies, migration to schema 5."""

import sqlite3
import threading

import pytest

from outbox_mcp import sources as src_rules
from outbox_mcp.errors import SourceRejected
from outbox_mcp.storage.database import Database

NOW = 1_000_000.0
ADMIN = "admin:test"


def sha(data: bytes) -> str:
    return src_rules.sha256_hex(data)


@pytest.fixture()
def db(tmp_path):
    return Database(tmp_path / "mail.db")


@pytest.fixture()
def up(db):
    """An upload folder source with the folder 'Projekte' allowed."""
    sid = db.create_source("upload", "folder", "Mac Projekte", NOW, ADMIN, origin_display="~/Documents")
    db.set_source_rule(sid, "Projekte", "allow", NOW, ADMIN)
    return sid


def put(db, sid, path, text="hallo", now=NOW):
    data = text.encode()
    return db.put_stored_file(sid, path, data, sha(data), now, ADMIN)


def rejected(category, fn, *args, **kwargs):
    with pytest.raises(SourceRejected) as info:
        fn(*args, **kwargs)
    assert info.value.category == category, str(info.value)
    return info.value


def audit_events(db, name):
    return [e for e in db.audit_events() if e["event"] == name]


# -- migration 4 -> 5 --------------------------------------------------------------------


def make_v4(path):
    """A database as schema 4 left it: the new tables and columns removed again."""
    db = Database(path)
    with sqlite3.connect(path) as conn:
        conn.executescript(
            "DROP TABLE stored_files; DROP TABLE file_sources;"
            "ALTER TABLE file_access_rules DROP COLUMN source_id;"
            "ALTER TABLE draft_attachments DROP COLUMN source_id;"
            "ALTER TABLE draft_attachments DROP COLUMN origin_key;"
            "PRAGMA user_version = 4;"
        )
    return db


def legacy_rule(path, root, rule_path, effect):
    """A rule as schema 4 wrote it (no source column)."""
    with sqlite3.connect(path) as conn:
        conn.execute(
            "INSERT INTO file_access_rules(scope,root_path,path_key,path_display,effect,changed_at,changed_by)"
            " VALUES ('attach',?,?,?,?,?,?)",
            (root, src_rules.path_key(rule_path), rule_path, effect, NOW, ADMIN),
        )


def test_version_4_database_is_upgraded_and_keeps_its_rows(tmp_path):
    path = tmp_path / "v4.db"
    make_v4(path)
    legacy_rule(path, "/notes", "Projekte", "allow")
    legacy_rule(path, "/notes", "Projekte/Privat", "deny")
    again = Database(path)
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 5
        assert conn.execute("SELECT COUNT(*) FROM file_access_rules").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM file_access_rules WHERE source_id IS NULL").fetchone()[0] == 2
        assert [r[1] for r in conn.execute("PRAGMA table_info(draft_attachments)")].count("origin_key") == 1
    assert again.rule_lists("attach", "/notes") == ([("projekte",)], [("projekte", "privat")])
    assert again.list_sources(include_removed=True) == []
    Database(path)  # a second open is harmless


def test_migration_turns_the_configured_root_into_one_source_and_takes_over_its_rules(tmp_path):
    path = tmp_path / "v4.db"
    make_v4(path)
    legacy_rule(path, "/notes", "Projekte", "allow")
    db = Database(path)
    sid = db.ensure_server_folder_source("/notes", NOW)
    src = db.get_source(sid)
    assert (src["kind"], src["scope"], src["status"], src["label"]) == ("server_folder", "folder", "active", "notes")
    assert db.source_rule_lists(sid) == ([("projekte",)], [])
    assert db.source_path_state(sid, "Projekte/a.md") == "allowed"
    assert db.ensure_server_folder_source("/notes", NOW + 1) == sid  # again: the same source
    assert len(db.list_sources()) == 1
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT source_id FROM file_access_rules").fetchone()[0] == sid
    # legacy path-based reads keep working during the transition
    assert db.rule_lists("attach", "/notes") == ([("projekte",)], [])


def test_rules_of_other_roots_and_scopes_stay_unassigned_and_never_reach_the_notes_source(tmp_path):
    db = Database(tmp_path / "a.db")
    db.set_file_rule("attach", "/notes", "Projekte", "allow", NOW, ADMIN)
    db.set_file_rule("attach", "/other", "Fremd", "allow", NOW, ADMIN)  # another root folder
    db.set_file_rule("read", "/notes", "Lesen", "allow", NOW, ADMIN)  # same root, other scope
    sid = db.ensure_server_folder_source("/notes", NOW)
    with sqlite3.connect(db.path) as conn:
        rows = {(r[0], r[1]): r[2] for r in conn.execute("SELECT scope, root_path, source_id FROM file_access_rules")}
    assert rows[("attach", "/notes")] == sid
    assert rows[("attach", "/other")] is None and rows[("read", "/notes")] is None
    assert db.source_rule_lists(sid) == ([("projekte",)], [])
    assert db.source_path_state(sid, "Fremd/a.md") == "none" and db.source_path_state(sid, "Lesen/a.md") == "none"
    other = db.ensure_server_folder_source("/other", NOW)  # a later source for that root takes over only its own rules
    assert db.source_rule_lists(other) == ([("fremd",)], [])


def test_rules_written_the_old_way_after_the_migration_are_seen_by_the_source(tmp_path):
    db = Database(tmp_path / "a.db")
    sid = db.ensure_server_folder_source("/notes", NOW)
    db.set_file_rule("attach", "/notes", "Projekte", "allow", NOW, ADMIN)
    assert db.source_path_state(sid, "Projekte/a.md") == "allowed"


def test_a_removed_server_folder_is_not_revived_when_added_again(tmp_path):
    db = Database(tmp_path / "a.db")
    db.set_file_rule("attach", "/notes", "Projekte", "allow", NOW, ADMIN)
    old = db.ensure_server_folder_source("/notes", NOW)
    assert db.source_path_state(old, "Projekte/a.md") == "allowed"
    db.remove_source(old, NOW + 1, ADMIN)
    new = db.ensure_server_folder_source("/notes", NOW + 2)
    assert new != old
    assert db.source_rule_lists(new) == ([], [])
    assert db.source_path_state(new, "Projekte/a.md") == "none"
    assert db.source_rule_lists(old) == ([], [])  # the removed source has no effective rules
    assert db.source_path_state(old, "Projekte/a.md") == "none"
    # the old rule row is still there as history, and a new rule on the same path does not overwrite it
    assert len(db.file_rules("attach", "/notes")) == 1
    db.set_source_rule(new, "Projekte", "deny", NOW + 3, ADMIN)
    (history,) = db.file_rules("attach", "/notes")
    assert (history["effect"], history["source_id"]) == ("allow", old)
    assert db.source_path_state(new, "Projekte/a.md") == "denied"


def test_ensure_source_picks_a_free_label(tmp_path):
    db = Database(tmp_path / "a.db")
    db.create_source("upload", "folder", "Notes", NOW, ADMIN)
    sid = db.ensure_server_folder_source("/n", NOW)
    assert db.get_source(sid)["label"] == "notes-2"


# -- creating sources --------------------------------------------------------------------


@pytest.mark.parametrize("label", ["", "   ", "a:b", "a/b", "a\\b", "x" * 61, "tab\there", None, 5])
def test_bad_labels_are_refused(db, label):
    rejected("input", db.create_source, "upload", "folder", label, NOW, ADMIN)


def test_labels_are_unique_among_active_sources_regardless_of_case_and_unicode(db):
    db.create_source("upload", "folder", "Büro", NOW, ADMIN)
    rejected("input", db.create_source, "upload", "folder", "BÜRO", NOW, ADMIN)
    rejected("input", db.create_source, "upload", "folder", "Büro", NOW, ADMIN)  # decomposed ü


def test_a_removed_sources_label_can_be_used_again(db):
    a = db.create_source("upload", "folder", "Büro", NOW, ADMIN)
    db.remove_source(a, NOW, ADMIN)
    assert db.create_source("upload", "folder", "Büro", NOW, ADMIN) != a


def test_unknown_kind_or_scope_is_refused(db):
    rejected("input", db.create_source, "cloud", "folder", "x", NOW, ADMIN)
    rejected("input", db.create_source, "upload", "tree", "x", NOW, ADMIN)


def test_upload_sources_have_no_server_path_and_server_folders_need_one(db):
    rejected("input", db.create_source, "upload", "folder", "x", NOW, ADMIN, real_path="/tmp/x")
    rejected("input", db.create_source, "server_folder", "folder", "x", NOW, ADMIN)
    rejected("input", db.create_source, "server_folder", "folder", "x", NOW, ADMIN, real_path="relative/dir")
    rejected("input", db.create_source, "server_folder", "folder", "x", NOW, ADMIN, real_path="/")
    rejected("input", db.create_source, "server_folder", "file", "x", NOW, ADMIN, real_path="/srv/n")


def test_server_folders_may_not_overlap(db):
    db.create_source("server_folder", "folder", "a", NOW, ADMIN, real_path="/srv/notes")
    rejected("input", db.create_source, "server_folder", "folder", "b", NOW, ADMIN, real_path="/srv/notes")
    rejected("input", db.create_source, "server_folder", "folder", "b", NOW, ADMIN, real_path="/srv/notes/sub")
    rejected("input", db.create_source, "server_folder", "folder", "b", NOW, ADMIN, real_path="/srv")
    db.create_source("server_folder", "folder", "c", NOW, ADMIN, real_path="/srv/notes2")  # sibling is fine


def test_single_file_sources_are_registered_by_a_clean_file_name(db):
    sid = db.create_source("upload", "file", "Plan", NOW, ADMIN, file_name="Plan.md")
    assert db.get_source(sid)["file_key"] == "plan.md"
    rejected("input", db.create_source, "upload", "file", "a", NOW, ADMIN)  # no file name
    rejected("input", db.create_source, "upload", "file", "b", NOW, ADMIN, file_name="dir/Plan.md")
    rejected("permission", db.create_source, "upload", "file", "c", NOW, ADMIN, file_name=".hidden.md")
    rejected("permission", db.create_source, "upload", "file", "d", NOW, ADMIN, file_name="Passwoerter.md")
    rejected("permission", db.create_source, "upload", "file", "e", NOW, ADMIN, file_name="Plan.pdf")
    rejected("input", db.create_source, "upload", "folder", "f", NOW, ADMIN, file_name="Plan.md")


def test_file_endings_depend_on_the_kind_of_source(db):
    assert db.create_source("upload", "file", "t", NOW, ADMIN, file_name="notiz.txt")
    src_rules.check_stored_name("upload", "a/notiz.txt")
    src_rules.check_stored_name("server_folder", "a/notiz.md")
    with pytest.raises(SourceRejected):
        src_rules.check_stored_name("server_folder", "a/notiz.txt")  # the notes folder stays Markdown only


def test_at_most_20_active_sources_and_removed_ones_do_not_count(db):
    ids = [db.create_source("upload", "folder", f"s{i}", NOW, ADMIN) for i in range(20)]
    rejected("quota", db.create_source, "upload", "folder", "one-too-many", NOW, ADMIN)
    db.remove_source(ids[0], NOW, ADMIN)
    db.create_source("upload", "folder", "again", NOW, ADMIN)


def test_creating_a_source_allows_nothing_and_is_audited_without_paths(db):
    sid = db.create_source("upload", "folder", "Mac", NOW, ADMIN, origin_display="/Users/x/Documents")
    assert db.source_rule_lists(sid) == ([], [])
    assert db.source_path_state(sid, "a/b.md") == "none"
    (event,) = audit_events(db, "source_created")
    assert sid in event["detail"] and "Documents" not in event["detail"] and "/Users" not in event["detail"]


# -- rules inside a source ---------------------------------------------------------------


def test_deny_wins_and_a_child_allow_cannot_undo_a_parent_deny(db, up):
    db.set_source_rule(up, "Projekte/Privat", "deny", NOW, ADMIN)
    assert db.source_path_state(up, "Projekte/a.md") == "allowed"
    assert db.source_path_state(up, "Projekte/Privat/a.md") == "denied"
    db.set_source_rule(up, "Projekte/Privat/Offen", "allow", NOW, ADMIN)
    assert db.source_path_state(up, "Projekte/Privat/Offen/a.md") == "denied"
    db.set_source_rule(up, "Projekte", "deny", NOW, ADMIN)
    assert db.source_path_state(up, "Projekte/a.md") == "denied"


def test_clearing_a_deny_gives_back_the_allow(db, up):
    db.set_source_rule(up, "Projekte/Privat", "deny", NOW, ADMIN)
    db.set_source_rule(up, "Projekte/Privat", "cleared", NOW, ADMIN)
    assert db.source_path_state(up, "Projekte/Privat/a.md") == "allowed"


def test_a_single_file_cannot_be_allowed_only_blocked(db, up):
    rejected("permission", db.set_source_rule, up, "Projekte/a.md", "allow", NOW, ADMIN)
    rejected("permission", db.set_source_rule, up, "Projekte/a.TXT", "allow", NOW, ADMIN)
    db.set_source_rule(up, "Projekte/a.md", "deny", NOW, ADMIN)
    assert db.source_path_state(up, "Projekte/a.md") == "denied"


def test_single_file_source_allows_only_its_file_and_it_can_be_blocked(db):
    sid = db.create_source("upload", "file", "Plan", NOW, ADMIN, file_name="Plan.md")
    assert db.source_path_state(sid, "Plan.md") == "allowed"
    assert db.source_path_state(sid, "Other.md") == "none"
    assert db.source_path_state(sid, "plan.MD") == "allowed"  # same file name, other case
    rejected("permission", db.set_source_rule, sid, "Plan.md", "allow", NOW, ADMIN)
    rejected("permission", db.set_source_rule, sid, "Other.md", "deny", NOW, ADMIN)
    rejected("permission", db.set_source_rule, sid, "Folder", "allow", NOW, ADMIN)
    db.set_source_rule(sid, "Plan.md", "deny", NOW, ADMIN)
    assert db.source_path_state(sid, "Plan.md") == "denied"
    db.set_source_rule(sid, "Plan.md", "cleared", NOW, ADMIN)
    assert db.source_path_state(sid, "Plan.md") == "allowed"


def test_rules_of_one_source_never_apply_to_another(db, up):
    other = db.create_source("upload", "folder", "Zweite", NOW, ADMIN)
    assert db.source_path_state(other, "Projekte/a.md") == "none"
    db.set_source_rule(other, "Projekte", "allow", NOW, ADMIN)
    db.set_source_rule(up, "Projekte", "deny", NOW, ADMIN)
    assert db.source_path_state(up, "Projekte/a.md") == "denied"
    assert db.source_path_state(other, "Projekte/a.md") == "allowed"  # a block in one source stays there


def test_rule_input_is_checked(db, up):
    for bad in ["", "/abs", "a/../b", "a//b", "a\\b", "~/x"]:
        rejected("input", db.set_source_rule, up, bad, "deny", NOW, ADMIN)
    rejected("input", db.set_source_rule, up, "Projekte", "maybe", NOW, ADMIN)
    rejected("state", db.set_source_rule, "src_missing", "Projekte", "deny", NOW, ADMIN)


def test_rule_changes_are_audited_and_rule_rows_are_never_deleted(db, up):
    db.set_source_rule(up, "Projekte/Privat", "deny", NOW, ADMIN)
    db.set_source_rule(up, "Projekte/Privat", "cleared", NOW, ADMIN)
    details = [e["detail"] for e in audit_events(db, "source_rule_set")]
    assert any("none -> deny" in d for d in details) and any("deny -> cleared" in d for d in details)
    with sqlite3.connect(db.path) as conn:
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute("DELETE FROM file_access_rules")
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute("DELETE FROM file_sources")


# -- removing a source -------------------------------------------------------------------


def test_removing_a_source_deletes_copies_keeps_rules_as_history_and_stops_everything(db, up):
    put(db, up, "Projekte/a.md")
    other = db.create_source("upload", "folder", "Zweite", NOW, ADMIN)
    db.set_source_rule(other, "Projekte", "allow", NOW, ADMIN)
    put(db, other, "Projekte/a.md")
    assert db.remove_source(up, NOW + 1, ADMIN)["purged_files"] == 1
    (row,) = db.list_stored_files(up)
    assert (row["state"], row["purged_reason"]) == ("purged", "source_removed")
    assert db.stored_content(up, "Projekte/a.md") is None
    assert db.source_rule_lists(up) == ([], [])
    rejected("state", put, db, up, "Projekte/b.md")
    rejected("state", db.set_source_rule, up, "Projekte", "allow", NOW, ADMIN)
    rejected("state", db.remove_source, up, NOW, ADMIN)
    assert db.stored_content(other, "Projekte/a.md") is not None  # the other source is untouched
    assert [e["detail"] for e in audit_events(db, "source_removed")] == [f"source={up} label=Mac Projekte purged_files=1 cancelled_drafts=0"]
    assert db.list_sources() != [] and all(s["id"] != up for s in db.list_sources())
    assert any(s["id"] == up and s["status"] == "removed" for s in db.list_sources(include_removed=True))


def test_a_removed_source_cannot_be_reactivated(db, up):
    db.remove_source(up, NOW, ADMIN)
    with sqlite3.connect(db.path) as conn:
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute("UPDATE file_sources SET status='active' WHERE id=?", (up,))
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute("UPDATE file_sources SET real_path='/x' WHERE id=?", (up,))


# -- storing copies ----------------------------------------------------------------------


def test_a_valid_file_is_stored_with_version_hash_and_audit(db, up):
    result = put(db, up, "Projekte/Plan.md", "# Plan")
    assert result["changed"] and result["version"] == 1 and result["sha256"] == sha(b"# Plan")
    got = db.stored_content(up, "Projekte/plan.md")  # key is case-insensitive
    assert got["data"] == b"# Plan" and got["version"] == 1 and got["sha256"] == sha(b"# Plan")
    (event,) = audit_events(db, "stored_file_stored")
    assert "version=1" in event["detail"] and sha(b"# Plan")[:12] in event["detail"]
    assert "# Plan" not in event["detail"] and sha(b"# Plan") not in event["detail"]


def test_same_content_again_changes_nothing_new_content_is_a_new_version(db, up):
    put(db, up, "Projekte/Plan.md", "eins")
    epoch = db.source_epoch(up)
    again = put(db, up, "Projekte/Plan.md", "eins")
    assert again["changed"] is False and again["version"] == 1
    assert db.source_epoch(up) == epoch and len(audit_events(db, "stored_file_stored")) == 1
    two = put(db, up, "Projekte/Plan.md", "zwei")
    assert two["changed"] and two["version"] == 2 and db.source_epoch(up) == epoch + 1
    assert db.stored_content(up, "Projekte/Plan.md")["data"] == b"zwei"


def test_the_hash_must_match_the_content(db, up):
    rejected("integrity", db.put_stored_file, up, "Projekte/a.md", b"inhalt", sha(b"anderer"), NOW, ADMIN)
    rejected("integrity", db.put_stored_file, up, "Projekte/a.md", b"inhalt", None, NOW, ADMIN)
    rejected("input", db.put_stored_file, up, "Projekte/a.md", "text", sha(b"text"), NOW, ADMIN)
    assert db.list_stored_files(up) == []


def test_path_rules_apply_to_uploads(db, up):
    for path in ["/abs/a.md", "Projekte/../a.md", "Projekte/.hidden/a.md", "Projekte/.a.md", "Projekte\\a.md", ""]:
        rejected("permission", put, db, up, path)
    rejected("permission", put, db, up, "Projekte/Passwoerter.md")
    rejected("permission", put, db, up, "Projekte/.env/a.md")
    rejected("permission", put, db, up, "Projekte/a.pdf")
    rejected("permission", put, db, up, "Projekte/a")
    rejected("permission", put, db, up, "Andere/a.md")  # not in an allowed folder
    rejected("permission", put, db, up, "a.md")  # the source root itself is never allowed
    assert db.list_stored_files(up) == []
    put(db, up, "Projekte/notiz.txt")  # .txt is fine in an upload source


def test_blocked_paths_are_refused(db, up):
    db.set_source_rule(up, "Projekte/Privat", "deny", NOW, ADMIN)
    rejected("permission", put, db, up, "Projekte/Privat/a.md")
    db.set_source_rule(up, "Projekte/Privat/Offen", "allow", NOW, ADMIN)
    rejected("permission", put, db, up, "Projekte/Privat/Offen/a.md")


def test_single_file_source_stores_only_its_file(db):
    sid = db.create_source("upload", "file", "Plan", NOW, ADMIN, file_name="Plan.md")
    assert put(db, sid, "Plan.md")["changed"]
    rejected("permission", put, db, sid, "Other.md")
    rejected("permission", put, db, sid, "Sub/Plan.md")


def test_content_checks_size_text_and_secrets(db, up):
    rejected("content", put, db, up, "Projekte/gross.md", "x" * (src_rules.MAX_FILE_BYTES + 1))
    put(db, up, "Projekte/genau.md", "x" * src_rules.MAX_FILE_BYTES)
    bad = b"\xff\xfe\x00caf\xe9"
    rejected("content", db.put_stored_file, up, "Projekte/a.md", bad, sha(bad), NOW, ADMIN)
    nul = b"a\x00b"
    rejected("content", db.put_stored_file, up, "Projekte/b.md", nul, sha(nul), NOW, ADMIN)
    rejected("content", put, db, up, "Projekte/c.md", "Passwort: hunter2hunter2")
    rejected("content", put, db, up, "Projekte/d.md", "-----BEGIN RSA PRIVATE KEY-----\nabc")
    assert [r["path_display"] for r in db.list_stored_files(up)] == ["Projekte/genau.md"]


def test_quota_files_per_source(db, up, monkeypatch):
    monkeypatch.setattr(src_rules, "MAX_FILES_PER_SOURCE", 3)
    for i in range(3):
        put(db, up, f"Projekte/{i}.md", f"n{i}")
    rejected("quota", put, db, up, "Projekte/vier.md")
    put(db, up, "Projekte/0.md", "geaendert")  # replacing one does not need a free slot


def test_quota_total_bytes_counts_current_files_only(db, up, monkeypatch):
    monkeypatch.setattr(src_rules, "MAX_TOTAL_BYTES", 20)
    put(db, up, "Projekte/a.md", "a" * 10)
    put(db, up, "Projekte/b.md", "b" * 10)
    rejected("quota", put, db, up, "Projekte/c.md", "c")
    put(db, up, "Projekte/a.md", "z" * 5)  # a smaller replacement frees room
    put(db, up, "Projekte/c.md", "c" * 5)
    rejected("quota", put, db, up, "Projekte/a.md", "z" * 11)  # the replaced bytes are not counted twice, the rest is
    db.set_source_rule(up, "Projekte/b.md", "deny", NOW, ADMIN)  # purging frees room
    put(db, up, "Projekte/d.md", "d" * 10)
    assert db.source_usage()["bytes"] == 20


def test_quota_is_kept_under_parallel_uploads(db, monkeypatch):
    monkeypatch.setattr(src_rules, "MAX_TOTAL_BYTES", 50)
    sid = db.create_source("upload", "folder", "P", NOW, ADMIN)
    db.set_source_rule(sid, "Projekte", "allow", NOW, ADMIN)
    results = []

    def work(i):
        try:
            put(db, sid, f"Projekte/{i}.md", "x" * 10)
            results.append("ok")
        except SourceRejected as exc:
            results.append(exc.category)

    threads = [threading.Thread(target=work, args=(i,)) for i in range(12)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert results.count("ok") == 5 and results.count("quota") == 7
    assert db.source_usage()["bytes"] == 50


# -- a block deletes the copies ----------------------------------------------------------


def test_a_block_deletes_the_stored_copy_at_once_and_unblocking_does_not_bring_it_back(db, up):
    put(db, up, "Projekte/Privat/a.md", "geheimer Plan")
    put(db, up, "Projekte/ok.md", "ok")
    purged = db.set_source_rule(up, "Projekte/Privat", "deny", NOW + 1, ADMIN)["purged_files"]
    assert purged == 1
    rows = {r["path_display"]: r for r in db.list_stored_files(up)}
    assert (rows["Projekte/Privat/a.md"]["state"], rows["Projekte/Privat/a.md"]["purged_reason"]) == ("purged", "blocked")
    assert rows["Projekte/ok.md"]["state"] == "current"
    assert db.stored_content(up, "Projekte/Privat/a.md") is None
    with sqlite3.connect(db.path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM stored_files WHERE content IS NOT NULL").fetchone()[0] == 1
        assert b"geheimer Plan" not in b"".join(r[0] for r in conn.execute("SELECT content FROM stored_files WHERE content IS NOT NULL"))
    assert [e["detail"] for e in audit_events(db, "stored_files_purged")] == [
        f"source={up} label=Mac Projekte count=1 reason=blocked"
    ]
    db.set_source_rule(up, "Projekte/Privat", "cleared", NOW + 2, ADMIN)
    assert db.stored_content(up, "Projekte/Privat/a.md") is None  # must be uploaded again
    again = put(db, up, "Projekte/Privat/a.md", "neu")
    assert again["version"] == 2 and db.stored_content(up, "Projekte/Privat/a.md")["data"] == b"neu"


def test_removing_an_allow_also_deletes_copies_that_are_no_longer_allowed(db, up):
    put(db, up, "Projekte/a.md")
    assert db.set_source_rule(up, "Projekte", "cleared", NOW, ADMIN)["purged_files"] == 1
    assert db.stored_content(up, "Projekte/a.md") is None


def test_blocking_the_registered_file_deletes_its_copy(db):
    sid = db.create_source("upload", "file", "Plan", NOW, ADMIN, file_name="Plan.md")
    put(db, sid, "Plan.md")
    assert db.set_source_rule(sid, "Plan.md", "deny", NOW, ADMIN)["purged_files"] == 1
    assert db.stored_content(sid, "Plan.md") is None


def test_stored_content_checks_the_rules_again_even_if_a_copy_is_current(db, up):
    put(db, up, "Projekte/a.md")
    with sqlite3.connect(db.path) as conn:  # a rule written behind the back of the purge
        conn.execute(
            "INSERT INTO file_access_rules(scope,root_path,path_key,path_display,effect,changed_at,changed_by,source_id)"
            " VALUES ('attach',?, 'projekte/a.md','Projekte/a.md','deny',1,'x',?)",
            (f"source:{up}", up),
        )
    assert db.stored_content(up, "Projekte/a.md") is None


def test_removed_local_files_lose_their_content(db, up):
    put(db, up, "Projekte/a.md")
    assert db.remove_stored_file(up, "Projekte/a.md", NOW, ADMIN) is True
    assert db.remove_stored_file(up, "Projekte/a.md", NOW, ADMIN) is False
    (row,) = db.list_stored_files(up)
    assert (row["state"], row["purged_reason"]) == ("removed", "removed_local")
    assert db.stored_content(up, "Projekte/a.md") is None
    assert put(db, up, "Projekte/a.md", "wieder")["version"] == 2


# -- atomicity ---------------------------------------------------------------------------


def test_a_failed_audit_entry_rolls_back_the_rule_and_the_deletion(db, up, monkeypatch):
    put(db, up, "Projekte/a.md")
    real = Database._audit

    def broken(conn, now, event, *a, **k):
        if event == "stored_files_purged":
            raise RuntimeError("audit failed")
        return real(conn, now, event, *a, **k)

    monkeypatch.setattr(Database, "_audit", staticmethod(broken))
    with pytest.raises(RuntimeError):
        db.set_source_rule(up, "Projekte", "deny", NOW, ADMIN)
    assert db.source_path_state(up, "Projekte/a.md") == "allowed"
    assert db.stored_content(up, "Projekte/a.md") is not None


def test_a_failed_audit_entry_rolls_back_storing_a_file(db, up, monkeypatch):
    def broken(conn, now, event, *a, **k):
        raise RuntimeError("audit failed")

    monkeypatch.setattr(Database, "_audit", staticmethod(broken))
    with pytest.raises(RuntimeError):
        put(db, up, "Projekte/a.md")
    monkeypatch.undo()
    assert db.list_stored_files(up) == []


def test_a_failed_audit_entry_rolls_back_removing_a_source(db, up, monkeypatch):
    put(db, up, "Projekte/a.md")
    monkeypatch.setattr(Database, "_audit", staticmethod(lambda *a, **k: (_ for _ in ()).throw(RuntimeError())))
    with pytest.raises(RuntimeError):
        db.remove_source(up, NOW, ADMIN)
    monkeypatch.undo()
    assert db.get_source(up)["status"] == "active" and db.stored_content(up, "Projekte/a.md") is not None


# -- independence and usage --------------------------------------------------------------


def test_the_same_path_in_two_upload_sources_is_two_independent_files(db, up):
    other = db.create_source("upload", "folder", "Zweite", NOW, ADMIN)
    db.set_source_rule(other, "Projekte", "allow", NOW, ADMIN)
    put(db, up, "Projekte/a.md", "eins")
    put(db, other, "Projekte/a.md", "zwei")
    db.set_source_rule(up, "Projekte", "deny", NOW, ADMIN)
    assert db.stored_content(up, "Projekte/a.md") is None
    assert db.stored_content(other, "Projekte/a.md")["data"] == b"zwei"


def test_stored_files_cannot_be_deleted_and_the_state_check_holds(db, up):
    put(db, up, "Projekte/a.md")
    with sqlite3.connect(db.path) as conn:
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute("DELETE FROM stored_files")
        with pytest.raises(sqlite3.DatabaseError):  # a current copy needs content
            conn.execute("UPDATE stored_files SET content=NULL")
        with pytest.raises(sqlite3.DatabaseError):  # a purged copy may not keep content
            conn.execute("UPDATE stored_files SET state='purged', purged_reason='blocked'")


def test_server_folder_sources_hold_no_stored_files(db):
    sid = db.create_source("server_folder", "folder", "notes", NOW, ADMIN, real_path="/srv/notes")
    db.set_source_rule(sid, "Projekte", "allow", NOW, ADMIN)
    rejected("state", put, db, sid, "Projekte/a.md")


def test_usage_counts_current_files_and_bytes(db, up):
    put(db, up, "Projekte/a.md", "abc")
    put(db, up, "Projekte/b.md", "de")
    db.set_source_rule(up, "Projekte/b.md", "deny", NOW, ADMIN)
    usage = db.source_usage()
    assert usage["sources"] == 1 and usage["bytes"] == 3
    assert usage["per_source"][up] == {"current": 1, "purged": 1, "removed": 0, "bytes": 3}


def test_short_version_is_the_first_12_hex_characters():
    assert src_rules.short_version(sha(b"x")) == sha(b"x")[:12] and len(src_rules.short_version(sha(b"x"))) == 12


def test_the_mail_service_registers_the_notes_folder_as_a_source(config, recipients, db, sender, clock, tmp_path):
    import dataclasses

    from outbox_mcp.service import OutboxService

    vault = tmp_path / "vault"
    (vault / "Projects").mkdir(parents=True)
    cfg = dataclasses.replace(
        config, attachment_root=vault, attachment_allowed_folders=("Projects",),
        attachment_denied_folders=("Projects/Private",),
    )
    OutboxService(cfg, recipients, db, sender, clock)
    OutboxService(cfg, recipients, db, sender, clock)  # a restart changes nothing
    (src,) = db.list_sources()
    assert src["kind"] == "server_folder" and src["label"] == "notes"
    assert db.source_path_state(src["id"], "Projects/a.md") == "allowed"
    assert db.source_path_state(src["id"], "Projects/Private/a.md") == "denied"
    assert len(audit_events(db, "source_created")) == 1
