"""Work package 6d: list_attachments shows only what the user has allowed, and nothing else."""

import base64
import dataclasses
import os

import pytest

from outbox_mcp import sources as src_rules
from outbox_mcp.errors import AttachmentRejected
from outbox_mcp.service import OutboxService

ADMIN = "admin:test"
NOW = 1_000_000.0


@pytest.fixture
def vault(tmp_path):
    v = tmp_path / "vault"
    for d in ("Projects", "Projects/Privat", "Banking"):
        (v / d).mkdir(parents=True, exist_ok=True)
    (v / "Projects/a.md").write_text("alpha", encoding="utf-8")
    (v / "Projects/b.md").write_text("beta beta", encoding="utf-8")
    (v / "Projects/Privat/p.md").write_text("privat", encoding="utf-8")
    (v / "Projects/Passwoerter.md").write_text("x", encoding="utf-8")
    (v / "Projects/readme.txt").write_text("nur md", encoding="utf-8")
    (v / "Projects/.hidden.md").write_text("h", encoding="utf-8")
    (v / "Banking/konto.md").write_text("konto", encoding="utf-8")
    return v


@pytest.fixture
def svc(config, recipients, db, sender, clock, vault):
    cfg = dataclasses.replace(
        config, attachment_root=vault, attachment_allowed_folders=("Projects",),
        attachment_denied_folders=("Projects/Privat",),
    )
    return OutboxService(cfg, recipients, db, sender, clock)


def upload(db, sid, path, text="inhalt"):
    data = text.encode()
    db.put_stored_file(sid, path, data, src_rules.sha256_hex(data), NOW, ADMIN)


@pytest.fixture
def mac(db):
    sid = db.create_source("upload", "folder", "Mac", NOW, ADMIN)
    db.set_source_rule(sid, "Projekte", "allow", NOW, ADMIN)
    return sid


def paths(result):
    return [(i["source"], i["path"]) for i in result["items"]]


def test_the_notes_folder_lists_only_allowed_markdown_files(svc):
    result = svc.list_attachments()
    assert paths(result) == [("notes", "Projects/a.md"), ("notes", "Projects/b.md")]
    assert result["items"][0]["size_bytes"] == 5 and result["items"][0]["attach_as"] == "notes:Projects/a.md"
    assert "as_of" not in result["items"][0]


def test_uploaded_files_list_with_version_and_date_and_without_content(svc, db, mac):
    upload(db, mac, "Projekte/Plan.md", "geheimer-inhalt-xyz")
    result = svc.list_attachments(source="mac")
    (item,) = result["items"]
    assert item["source"] == "Mac" and item["path"] == "Projekte/Plan.md"
    assert item["version"] == src_rules.short_version(src_rules.sha256_hex(b"geheimer-inhalt-xyz"))
    assert item["as_of"] == "1970-01-12" and item["attach_as"] == "Mac:Projekte/Plan.md"
    assert "geheimer-inhalt-xyz" not in str(result) and "sha256" not in str(result)


def test_blocked_purged_and_removed_things_are_not_listed(svc, db, mac):
    upload(db, mac, "Projekte/ok.md")
    upload(db, mac, "Projekte/weg.md")
    upload(db, mac, "Projekte/Privat/x.md")
    db.set_source_rule(mac, "Projekte/Privat", "deny", NOW, ADMIN)
    db.remove_stored_file(mac, "Projekte/weg.md", NOW, ADMIN)
    assert [p for s, p in paths(svc.list_attachments(source="Mac"))] == ["Projekte/ok.md"]
    db.remove_source(mac, NOW, ADMIN)
    assert paths(svc.list_attachments()) == [("notes", "Projects/a.md"), ("notes", "Projects/b.md")]
    with pytest.raises(AttachmentRejected, match="Unknown source"):
        svc.list_attachments(source="Mac")


def test_a_block_in_the_notes_folder_hides_the_file_at_once(svc, db, vault):
    db.set_file_rule("attach", vault, "Projects/a.md", "deny", NOW, ADMIN)
    assert paths(svc.list_attachments()) == [("notes", "Projects/b.md")]


def test_nothing_outside_an_allowed_folder_and_no_links_are_listed(svc, vault, tmp_path):
    outside = tmp_path / "outside.md"
    outside.write_text("außen", encoding="utf-8")
    os.symlink(outside, vault / "Projects/link.md")
    os.symlink(tmp_path, vault / "Projects/dir")
    listed = paths(svc.list_attachments())
    assert listed == [("notes", "Projects/a.md"), ("notes", "Projects/b.md")]


def test_paging_returns_everything_once_in_order(svc, db, mac):
    for i in range(5):
        upload(db, mac, f"Projekte/f{i}.md")
    seen, cursor = [], None
    while True:
        page = svc.list_attachments(source="Mac", cursor=cursor, limit=2)
        seen += [p for s, p in paths(page)]
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert seen == [f"Projekte/f{i}.md" for i in range(5)]


def test_the_limit_is_clamped_and_the_prefix_narrows(svc, db, mac):
    for i in range(3):
        upload(db, mac, f"Projekte/Eins/f{i}.md")
    upload(db, mac, "Projekte/Zwei/g.md")
    assert len(svc.list_attachments(source="Mac", limit=0)["items"]) == 1
    assert len(svc.list_attachments(source="Mac", limit=100000)["items"]) == 4
    assert len(svc.list_attachments(source="Mac", limit="x")["items"]) == 4
    assert [p for s, p in paths(svc.list_attachments(source="Mac", path_prefix="projekte/zwei"))] == ["Projekte/Zwei/g.md"]
    assert svc.list_attachments(source="Mac", path_prefix="Projekte/Zw")["items"] == []  # whole components only
    with pytest.raises(AttachmentRejected):
        svc.list_attachments(path_prefix="../x")


def test_a_bad_cursor_is_refused_and_the_list_is_empty_without_sources(config, recipients, db, sender, clock):
    empty = OutboxService(config, recipients, db, sender, clock)
    assert empty.list_attachments()["items"] == []
    with pytest.raises(AttachmentRejected, match="cursor"):
        empty.list_attachments(cursor="!!!")
    bad = base64.urlsafe_b64encode(b"no separator").decode()
    with pytest.raises(AttachmentRejected, match="cursor"):
        empty.list_attachments(cursor=bad)


def test_attach_as_can_be_used_to_prepare_a_mail(svc, db, mac):
    upload(db, mac, "Projekte/Plan.md", "Plan")
    for item in svc.list_attachments()["items"]:
        draft = svc.prepare_email("o", "Ferdinand", "S", "T", [item["attach_as"]])
        assert draft["attachments"][0]["path"] == item["path"]


def test_listing_changes_nothing(svc, db, mac):
    upload(db, mac, "Projekte/Plan.md", "Plan")
    before = (db.source_epoch(mac), len(db.audit_events()))
    svc.list_attachments()
    svc.list_attachments(source="Mac")
    assert (db.source_epoch(mac), len(db.audit_events())) == before


def test_a_rule_written_behind_the_back_of_the_purge_still_hides_the_file(svc, db, mac):
    import sqlite3
    upload(db, mac, "Projekte/Plan.md")
    with sqlite3.connect(db.path) as conn:
        conn.execute(
            "INSERT INTO file_access_rules(scope,root_path,path_key,path_display,effect,changed_at,changed_by,source_id)"
            " VALUES ('attach',?,'projekte/plan.md','Projekte/Plan.md','deny',0,'x',?)", (f"source:{mac}", mac),
        )
    assert svc.list_attachments(source="Mac")["items"] == []

