"""Work package 6c: the admin API for file sources, their rules and the synchronisation."""

import hashlib

import pytest
from starlette.testclient import TestClient

from outbox_mcp import sources as src_rules
from outbox_mcp.admin.app import build_admin_app
from outbox_mcp.admin.config import AdminConfig
from outbox_mcp.auth.passwords import hash_password
from outbox_mcp.service import OutboxService
from outbox_mcp.storage.database import Database

PASSPHRASE = "correct horse battery staple"


class FakeClock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "vault"
    (r / "Projects").mkdir(parents=True)
    (r / "Projects/a.md").write_text("a", encoding="utf-8")
    return r


@pytest.fixture
def admin_config(tmp_path, root):
    return AdminConfig(
        db_path=tmp_path / "mail.db", passphrase_hash=hash_password(PASSPHRASE, n=2**4),
        allowed_hosts=("testserver",), attachment_root=root,
    )


@pytest.fixture
def client(admin_config, clock):
    with TestClient(build_admin_app(admin_config, clock)) as c:
        yield c


def sign_in(client):
    return {"Authorization": "Bearer " + client.post("/admin/login", json={"passphrase": PASSPHRASE}).json()["token"]}


@pytest.fixture
def auth(client):
    return sign_in(client)


@pytest.fixture
def db(admin_config):
    return Database(admin_config.db_path)


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def entry(path, text):
    return {"path": path, "sha256": sha(text), "size": len(text.encode())}


def full(path, text):
    return {"path": path, "sha256": sha(text), "content": text}


def create(client, auth, label="Mac", scope="folder", **extra):
    r = client.post("/admin/sources", json={"label": label, "kind": "upload", "scope": scope, **extra}, headers=auth)
    assert r.status_code == 201, r.text
    return r.json()["source"]["id"]


def rule(client, auth, sid, path, effect, **extra):
    return client.put(f"/admin/sources/{sid}/rule", json={"path": path, "effect": effect, **extra}, headers=auth)


def allow(client, auth, sid, path):
    first = rule(client, auth, sid, path, "allow").json()
    assert first["status"] == "confirmation_required", first
    done = rule(client, auth, sid, path, "allow", confirmation_id=first["confirmation_id"]).json()
    assert done["status"] == "done", done


def plan(client, auth, sid, manifest):
    return client.post(f"/admin/sources/{sid}/sync/plan", json={"manifest": manifest}, headers=auth)


def apply(client, auth, sid, cid, files):
    return client.post(f"/admin/sources/{sid}/sync/apply", json={"confirmation_id": cid, "files": files}, headers=auth)


@pytest.fixture
def src(client, auth):
    sid = create(client, auth)
    allow(client, auth, sid, "Projekte")
    return sid


# -- sources: list, create, rename, remove ----------------------------------------------------


def test_every_source_endpoint_needs_the_session(client):
    calls = [
        ("get", "/admin/sources"), ("post", "/admin/sources"), ("post", "/admin/sources/x/rename"),
        ("post", "/admin/sources/x/remove"), ("post", "/admin/sources/x/evaluate"), ("put", "/admin/sources/x/rule"),
        ("get", "/admin/sources/x/folders"), ("get", "/admin/sources/x/files"),
        ("post", "/admin/sources/x/sync/plan"), ("post", "/admin/sources/x/sync/apply"),
    ]
    for method, url in calls:
        assert getattr(client, method)(url).status_code == 401, url


def test_list_shows_sources_with_usage_and_the_quota_block(client, auth):
    sid = create(client, auth, origin_display="Documents")
    body = client.get("/admin/sources", headers=auth).json()
    (row,) = body["sources"]
    assert row["id"] == sid and row["label"] == "Mac" and row["kind"] == "upload" and row["files"] == 0
    assert row["origin_display"] == "Documents"
    q = body["quota"]
    assert q["sources"] == 1 and q["max_sources"] == 20 and q["max_files_per_source"] == 500
    assert q["max_file_bytes"] == 200_000 and q["max_total_bytes"] == 50_000_000
    assert q["apply_files"] == 40 and q["apply_bytes"] == 8 * 1024 * 1024


def test_only_upload_sources_can_be_created_and_names_are_unique(client, auth):
    create(client, auth)
    bad = client.post("/admin/sources", json={"label": "X", "kind": "server_folder", "scope": "folder"}, headers=auth)
    assert bad.status_code == 400
    assert client.post("/admin/sources", json={"label": "mac", "kind": "upload", "scope": "folder"}, headers=auth).status_code == 400
    for label in ("a:b", "a/b", ""):
        assert client.post("/admin/sources", json={"label": label, "kind": "upload", "scope": "folder"}, headers=auth).status_code == 400
    assert client.post("/admin/sources", json={"label": "Y", "kind": "upload", "scope": "weird"}, headers=auth).status_code == 400


def test_rename_changes_the_name_only_and_refuses_a_taken_name(client, auth, db):
    sid, other = create(client, auth), create(client, auth, "Zwei")
    r = client.post(f"/admin/sources/{sid}/rename", json={"label": "Laptop"}, headers=auth)
    assert r.status_code == 200 and r.json()["source"]["label"] == "Laptop" and r.json()["source"]["id"] == sid
    taken = client.post(f"/admin/sources/{other}/rename", json={"label": "laptop"}, headers=auth)
    assert taken.status_code == 409 and taken.json()["error"] == "conflict"
    assert db.count_events("source_renamed") == 1
    assert client.post("/admin/sources/nope/rename", json={"label": "Q"}, headers=auth).status_code == 404


def test_removing_returns_the_consequences_and_cannot_repeat(client, auth, db, src):
    p = plan(client, auth, src, [entry("Projekte/a.md", "x")]).json()
    assert apply(client, auth, src, p["confirmation_id"], [full("Projekte/a.md", "x")]).json()["status"] == "done"
    r = client.post(f"/admin/sources/{src}/remove", headers=auth).json()
    assert r["status"] == "done" and r["purged_files"] == 1 and r["cancelled_drafts"] == 0
    assert client.post(f"/admin/sources/{src}/remove", headers=auth).status_code == 404
    assert client.get("/admin/sources", headers=auth).json()["sources"] == []
    assert plan(client, auth, src, []).status_code == 404  # a removed source takes no more uploads


# -- evaluate and rules -------------------------------------------------------------------------


def test_evaluate_reports_state_rule_and_sensitivity(client, auth, src):
    rule(client, auth, src, "Projekte/Privat", "deny")
    paths = ["Projekte/a.md", "Projekte/Privat/b.md", "Anderes/c.md", "Projekte/Passwoerter.md", "../x"]
    res = client.post(f"/admin/sources/{src}/evaluate", json={"paths": paths}, headers=auth).json()["results"]
    by = {r["path"]: r for r in res}
    assert by["Projekte/a.md"]["state"] == "allowed" and by["Projekte/a.md"]["inherited"] is True
    assert by["Projekte/a.md"]["source_rule"] == "Projekte" and by["Projekte/a.md"]["own_rule"] is None
    assert by["Projekte/Privat/b.md"]["state"] == "denied" and by["Projekte/Privat/b.md"]["source_rule"] == "Projekte/Privat"
    assert by["Anderes/c.md"]["state"] == "none" and by["Anderes/c.md"]["source_rule"] is None
    assert by["Projekte/Passwoerter.md"]["sensitive"] is True
    assert res[-1]["state"] == "invalid"
    too_many = client.post(f"/admin/sources/{src}/evaluate", json={"paths": ["a.md"] * 1001}, headers=auth)
    assert too_many.status_code == 400


def test_loosening_needs_a_confirmation_that_is_bound_to_the_state(client, auth, db, src):
    first = rule(client, auth, src, "Weiteres", "allow").json()
    assert first["status"] == "confirmation_required" and first["preview"]["change"] == "none -> allow"
    rule(client, auth, src, "Anderes", "deny")  # the rules change in between
    stale = rule(client, auth, src, "Weiteres", "allow", confirmation_id=first["confirmation_id"])
    assert stale.status_code == 409 and stale.json()["error"] == "confirmation_invalid"
    allow(client, auth, src, "Weiteres")
    assert db.source_path_state(src, "Weiteres/x.md") == "allowed"


def test_a_block_is_immediate_and_clearing_it_needs_a_confirmation(client, auth, db, src):
    done = rule(client, auth, src, "Projekte/Privat", "deny").json()
    assert done["status"] == "done" and done["purged_files"] == 0 and done["cancelled_drafts"] == 0
    again = rule(client, auth, src, "Projekte/Privat", "deny").json()
    assert again["status"] == "unchanged"
    needs = rule(client, auth, src, "Projekte/Privat", "clear").json()
    assert needs["status"] == "confirmation_required"
    assert db.source_path_state(src, "Projekte/Privat/x.md") == "denied"
    assert rule(client, auth, src, "Projekte/Privat", "clear", confirmation_id=needs["confirmation_id"]).json()["status"] == "done"
    assert db.source_path_state(src, "Projekte/Privat/x.md") == "allowed"


def test_a_block_reports_what_it_purged_and_the_preview_counts_stored_files(client, auth, db, src):
    p = plan(client, auth, src, [entry("Projekte/Privat/a.md", "1"), entry("Projekte/b.md", "2")]).json()
    apply(client, auth, src, p["confirmation_id"], [full("Projekte/Privat/a.md", "1"), full("Projekte/b.md", "2")])
    assert rule(client, auth, src, "Projekte/Privat", "deny").json()["purged_files"] == 1
    pre = rule(client, auth, src, "Projekte/Privat", "clear").json()["preview"]
    assert pre["stored_files_allowed_before"] == 1 and pre["stored_files_allowed_after"] == 1  # the copy is gone


def test_bad_rule_input_is_refused(client, auth, src):
    assert rule(client, auth, src, "Projekte", "maybe").status_code == 400
    assert rule(client, auth, src, "../x", "deny").status_code == 400
    first = rule(client, auth, src, "Projekte/datei.md", "allow").json()  # the confirmation round comes first
    r = rule(client, auth, src, "Projekte/datei.md", "allow", confirmation_id=first["confirmation_id"])
    assert r.status_code == 400 and "single file" in r.json()["message"]
    assert rule(client, auth, "nope", "Projekte", "deny").status_code == 404


def test_the_notes_source_is_ruled_through_the_same_endpoint(client, auth, db, root):
    nid = db.ensure_server_folder_source(root, 1.0)
    listing = client.get(f"/admin/sources/{nid}/folders", headers=auth).json()
    assert [e["name"] for e in listing["entries"]] == ["Projects"]
    first = rule(client, auth, nid, "Projects", "allow").json()
    assert first["status"] == "confirmation_required"
    done = rule(client, auth, nid, "Projects", "allow", confirmation_id=first["confirmation_id"]).json()
    assert done["status"] == "done"
    assert db.source_path_state(nid, "Projects/a.md") == "allowed"
    assert client.get(f"/admin/sources/{nid}/files", headers=auth).status_code == 400  # not an upload source
    up = create(client, auth)
    assert client.get(f"/admin/sources/{up}/folders", headers=auth).status_code == 400


# -- synchronisation ----------------------------------------------------------------------------


def test_plan_then_apply_stores_the_files_in_one_audited_step(client, auth, db, src):
    manifest = [entry("Projekte/a.md", "# A"), entry("Projekte/b.md", "# B")]
    p = plan(client, auth, src, manifest).json()
    assert p["summary"] == {"new": 2, "changed": 0, "removed": 0, "unchanged": 0}
    assert p["passes"] == 1 and p["rejected"] == [] and p["expires_in"] >= 120
    assert sorted(i["path"] for i in p["items"]) == ["Projekte/a.md", "Projekte/b.md"]
    assert db.list_stored_files(src) == []  # planning stores nothing
    done = apply(client, auth, src, p["confirmation_id"], [full("Projekte/a.md", "# A"), full("Projekte/b.md", "# B")]).json()
    assert done["status"] == "done" and done["stored"] == 2 and done["removed"] == 0 and done["remaining"] == 0
    assert [r["path_display"] for r in db.list_stored_files(src)] == ["Projekte/a.md", "Projekte/b.md"]
    assert db.count_events("source_synced") == 1 and db.count_events("stored_file_stored") == 0
    again = plan(client, auth, src, manifest).json()
    assert again["summary"] == {"new": 0, "changed": 0, "removed": 0, "unchanged": 2}


def test_changes_and_removals_are_planned_and_applied(client, auth, db, src):
    p = plan(client, auth, src, [entry("Projekte/a.md", "1"), entry("Projekte/b.md", "2")]).json()
    apply(client, auth, src, p["confirmation_id"], [full("Projekte/a.md", "1"), full("Projekte/b.md", "2")])
    p = plan(client, auth, src, [entry("Projekte/a.md", "neu")]).json()
    assert p["summary"] == {"new": 0, "changed": 1, "removed": 1, "unchanged": 0}
    assert {(i["path"], i["change"]) for i in p["items"]} == {("Projekte/a.md", "changed"), ("Projekte/b.md", "removed")}
    done = apply(client, auth, src, p["confirmation_id"], [full("Projekte/a.md", "neu")]).json()
    assert done["stored"] == 1 and done["removed"] == 1
    states = {r["path_display"]: (r["state"], r["version"]) for r in db.list_stored_files(src)}
    assert states == {"Projekte/a.md": ("current", 2), "Projekte/b.md": ("removed", 1)}
    only_removal = plan(client, auth, src, []).json()
    assert only_removal["summary"]["removed"] == 1 and only_removal["passes"] == 1
    assert apply(client, auth, src, only_removal["confirmation_id"], []).json()["removed"] == 1


def test_the_plan_rejects_what_the_server_would_reject(client, auth, src):
    rule(client, auth, src, "Projekte/Privat", "deny")
    manifest = [
        entry("Projekte/ok.md", "ok"), entry("Projekte/Privat/x.md", "x"), entry("Anderes/y.md", "y"),
        entry("Projekte/Passwoerter.md", "p"), entry("Projekte/bild.png", "b"), entry("Projekte/ok.md", "doppelt"),
        {"path": "Projekte/riesig.md", "sha256": sha("r"), "size": 300_000}, entry("../escape.md", "e"),
    ]
    p = plan(client, auth, src, manifest).json()
    reasons = {r["path"]: r["reason"] for r in p["rejected"]}
    assert p["summary"]["new"] == 1 and p["rejected_count"] == 7
    assert reasons["Projekte/Privat/x.md"] == "Blocked."
    assert "allowed folder" in reasons["Anderes/y.md"]
    assert "sensitive" in reasons["Projekte/Passwoerter.md"] and "too large" in reasons["Projekte/riesig.md"].lower()
    assert reasons["Projekte/ok.md"] == "Listed twice."


@pytest.mark.parametrize("manifest", [
    "x", [1], [{"path": "a.md"}], [{"path": "a.md", "sha256": "zz", "size": 1}],
    [{"path": "a.md", "sha256": "a" * 64, "size": -1}], [{"path": "a.md", "sha256": "a" * 64, "size": True}],
])
def test_a_malformed_manifest_is_refused(client, auth, src, manifest):
    assert plan(client, auth, src, manifest).status_code == 400


def test_the_plan_refuses_when_the_quota_would_be_exceeded(client, auth, src, monkeypatch):
    monkeypatch.setattr(src_rules, "MAX_FILES_PER_SOURCE", 1)
    r = plan(client, auth, src, [entry("Projekte/a.md", "1"), entry("Projekte/b.md", "2")])
    assert r.status_code == 409 and r.json()["error"] == "quota"
    monkeypatch.setattr(src_rules, "MAX_FILES_PER_SOURCE", 500)
    monkeypatch.setattr(src_rules, "MAX_TOTAL_BYTES", 3)
    assert plan(client, auth, src, [entry("Projekte/a.md", "12"), entry("Projekte/b.md", "34")]).status_code == 409


def test_more_than_one_pass_is_applied_in_steps_and_removals_wait_for_the_last(client, auth, db, src):
    old = plan(client, auth, src, [entry("Projekte/alt.md", "alt")]).json()
    apply(client, auth, src, old["confirmation_id"], [full("Projekte/alt.md", "alt")])
    names = [f"Projekte/f{i:02d}.md" for i in range(45)]
    p = plan(client, auth, src, [entry(n, n) for n in names]).json()
    assert p["passes"] == 2 and p["summary"]["new"] == 45 and p["summary"]["removed"] == 1
    assert len(p["next_pass"]) == 40
    first = apply(client, auth, src, p["confirmation_id"], [full(n, n) for n in p["next_pass"]]).json()
    assert first["status"] == "partial" and first["stored"] == 40 and first["removed"] == 0 and first["remaining"] == 5
    assert len(first["next_pass"]) == 5
    current = {r["path_display"]: r["state"] for r in db.list_stored_files(src)}
    assert current["Projekte/alt.md"] == "current"
    second = apply(client, auth, src, first["confirmation_id"], [full(n, n) for n in first["next_pass"]]).json()
    assert second["status"] == "done" and second["stored"] == 5 and second["removed"] == 1 and second["remaining"] == 0
    assert db.list_stored_files(src, "current").__len__() == 45
    assert db.count_events("source_synced") == 3  # the first small sync and one entry per pass


def test_a_pass_may_not_take_more_than_the_limits(client, auth, db, src):
    names = [f"Projekte/f{i:02d}.md" for i in range(41)]
    p = plan(client, auth, src, [entry(n, n) for n in names]).json()
    r = apply(client, auth, src, p["confirmation_id"], [full(n, n) for n in names])
    assert r.status_code == 400 and db.list_stored_files(src) == []
    big = "x" * 190_000
    names = [f"Projekte/g{i}.md" for i in range(45)]
    p = plan(client, auth, src, [entry(n, big) for n in names[:3]]).json()  # 570 KB, fine
    assert apply(client, auth, src, p["confirmation_id"], [full(n, big) for n in names[:3]]).status_code == 200


def test_the_plan_cannot_be_widened_by_the_apply_step(client, auth, db, src):
    base = [entry("Projekte/a.md", "A")]
    attempts = {
        "unplanned file": [full("Projekte/zusatz.md", "Z")],
        "other content": [full("Projekte/a.md", "B")],
        "twice": [full("Projekte/a.md", "A"), full("Projekte/a.md", "A")],
        "wrong hash": [{"path": "Projekte/a.md", "sha256": "0" * 64, "content": "A"}],
        "bad shape": [{"path": "Projekte/a.md"}],
    }
    for name, files in attempts.items():
        p = plan(client, auth, src, base).json()
        r = apply(client, auth, src, p["confirmation_id"], files)
        assert r.status_code == 400, name
        assert db.list_stored_files(src) == [], name


def test_a_pass_is_all_or_nothing_for_integrity_violations(client, auth, db, src, monkeypatch):
    p = plan(client, auth, src, [entry("Projekte/a.md", "A"), entry("Projekte/b.md", "B")]).json()
    # the second file claims the planned hash but sends other bytes: the whole pass is undone
    r = apply(client, auth, src, p["confirmation_id"], [full("Projekte/a.md", "A"),
                                                         {"path": "Projekte/b.md", "sha256": sha("B"), "content": "anders"}])
    assert r.status_code == 400
    assert db.list_stored_files(src) == [] and db.count_events("source_synced") == 0


def test_a_file_with_unacceptable_content_is_skipped_and_reported_the_rest_is_stored(client, auth, db, src):
    manifest = [entry("Projekte/gut.md", "gut"), entry("Projekte/nul.md", "a\x00b")]
    p = plan(client, auth, src, manifest).json()
    done = apply(client, auth, src, p["confirmation_id"], [full("Projekte/gut.md", "gut"), full("Projekte/nul.md", "a\x00b")]).json()
    assert done["status"] == "done" and done["stored"] == 1 and len(done["skipped"]) == 1
    assert done["skipped"][0]["path"] == "Projekte/nul.md"
    assert [r["path_display"] for r in db.list_stored_files(src, "current")] == ["Projekte/gut.md"]


def test_a_confirmation_works_once_and_only_for_its_session_and_source(client, auth, db, src):
    other = create(client, auth, "Zwei")
    allow(client, auth, other, "Projekte")
    p = plan(client, auth, src, [entry("Projekte/a.md", "A")]).json()
    assert apply(client, auth, other, p["confirmation_id"], [full("Projekte/a.md", "A")]).status_code == 409
    q = plan(client, auth, src, [entry("Projekte/a.md", "A")]).json()
    second_session = sign_in(client)
    assert apply(client, auth=second_session, sid=src, cid=q["confirmation_id"], files=[full("Projekte/a.md", "A")]).status_code == 409
    r = plan(client, auth, src, [entry("Projekte/a.md", "A")]).json()
    assert apply(client, auth, src, r["confirmation_id"], [full("Projekte/a.md", "A")]).status_code == 200
    assert apply(client, auth, src, r["confirmation_id"], [full("Projekte/a.md", "A")]).json()["replayed"] is True
    assert apply(client, auth, src, r["confirmation_id"], [full("Projekte/b.md", "B")]).status_code == 409
    assert apply(client, auth, src, "unbekannt", []).status_code == 409
    assert db.count_events("source_synced") == 1


def test_a_change_between_plan_and_apply_invalidates_the_plan(client, auth, db, src):
    p = plan(client, auth, src, [entry("Projekte/a.md", "A")]).json()
    rule(client, auth, src, "Projekte/Privat", "deny")  # any rule change moves the epoch
    r = apply(client, auth, src, p["confirmation_id"], [full("Projekte/a.md", "A")])
    assert r.status_code == 409 and r.json()["error"] == "confirmation_invalid"
    assert db.list_stored_files(src) == []
    p = plan(client, auth, src, [entry("Projekte/a.md", "A")]).json()
    data = b"B"
    db.put_stored_file(src, "Projekte/c.md", data, hashlib.sha256(data).hexdigest(), 1.0, "x")  # another upload
    assert apply(client, auth, src, p["confirmation_id"], [full("Projekte/a.md", "A")]).status_code == 409


def test_a_block_between_plan_and_apply_is_never_overridden(client, auth, db, src):
    p = plan(client, auth, src, [entry("Projekte/Privat/a.md", "A")]).json()
    rule(client, auth, src, "Projekte/Privat", "deny")
    assert apply(client, auth, src, p["confirmation_id"], [full("Projekte/Privat/a.md", "A")]).status_code == 409
    assert db.list_stored_files(src) == []


def test_a_single_file_source_takes_its_one_file_only(client, auth, db):
    sid = create(client, auth, "Plan", "file", file_name="Plan.md")
    p = plan(client, auth, sid, [entry("Plan.md", "P"), entry("Other.md", "O")]).json()
    assert p.get("single_file") is True and p["summary"]["new"] == 1 and p["rejected_count"] == 1
    assert apply(client, auth, sid, p["confirmation_id"], [full("Plan.md", "P")]).json()["stored"] == 1


def test_the_upload_route_takes_big_bodies_other_routes_stay_small(client, auth, src):
    big = "x" * 150_000
    p = plan(client, auth, src, [entry("Projekte/g1.md", big), entry("Projekte/g2.md", big)]).json()
    assert apply(client, auth, src, p["confirmation_id"], [full("Projekte/g1.md", big), full("Projekte/g2.md", big)]).status_code == 200
    r = client.post(f"/admin/sources/{src}/evaluate", json={"paths": ["a.md"], "pad": "x" * 20_000}, headers=auth)
    assert r.status_code == 413


def test_listing_the_stored_files_pages_and_shows_state_and_version(client, auth, db, src):
    names = [f"Projekte/f{i:03d}.md" for i in range(130)]
    for start in range(0, 130, 40):
        chunk = names[start:start + 40]
        p = plan(client, auth, src, [entry(n, n) for n in names[: start + len(chunk)]]).json()
        todo = [n for n in chunk]
        apply(client, auth, src, p["confirmation_id"], [full(n, n) for n in todo])
    first = client.get(f"/admin/sources/{src}/files", headers=auth).json()
    assert len(first["files"]) == 100 and first["next_cursor"]
    second = client.get(f"/admin/sources/{src}/files", params={"cursor": first["next_cursor"]}, headers=auth).json()
    assert len(second["files"]) == 30 and second["next_cursor"] is None
    f = first["files"][0]
    assert f["state"] == "current" and f["version"] == 1 and f["allowed_now"] is True and len(f["id"]) == 12
    some = client.get(f"/admin/sources/{src}/files", params={"path": "Projekte"}, headers=auth).json()
    assert len(some["files"]) == 100


def test_what_was_synced_is_visible_to_claude_through_the_mail_service(client, auth, db, src, admin_config, config, recipients, sender):
    p = plan(client, auth, src, [entry("Projekte/Plan.md", "Plan")]).json()
    apply(client, auth, src, p["confirmation_id"], [full("Projekte/Plan.md", "Plan")])
    import dataclasses
    svc = OutboxService(dataclasses.replace(config, db_path=admin_config.db_path), recipients, Database(admin_config.db_path), sender)
    items = svc.list_attachments()["items"]
    assert [(i["source"], i["path"]) for i in items] == [("Mac", "Projekte/Plan.md")]
    draft = svc.prepare_email("o", "Ferdinand", "S", "T", [items[0]["attach_as"]])
    assert svc.send_email("o", draft["draft_id"])["status"] == "sent"


def test_the_database_refuses_a_pass_for_a_stale_epoch_on_its_own(db, client, auth, src):
    """Second line of defence: even without the confirmation, a pass for an old epoch is refused."""
    from outbox_mcp.errors import SourceRejected
    epoch = db.source_epoch(src)
    db.set_source_rule(src, "Projekte/Privat", "deny", 1.0, "x")
    data = b"A"
    with pytest.raises(SourceRejected) as exc:
        db.apply_sync(src, [("Projekte/a.md", data, hashlib.sha256(data).hexdigest())], [], 2.0, "x", epoch)
    assert exc.value.category == "conflict" and db.list_stored_files(src) == []
    ok = db.apply_sync(src, [("Projekte/a.md", data, hashlib.sha256(data).hexdigest())], [], 2.0, "x", db.source_epoch(src))
    assert ok["stored"] == 1


# -- review round: failures, repeats and lost answers ----------------------------------------------


def _two_pass_plan(client, auth, src):
    names = [f"Projekte/f{i:02d}.md" for i in range(45)]
    p = plan(client, auth, src, [entry(n, n) for n in names]).json()
    return names, p


def test_a_failed_pass_voids_the_rest_of_the_plan_but_keeps_earlier_passes(client, auth, db, src):
    old = plan(client, auth, src, [entry("Projekte/alt.md", "alt")]).json()
    apply(client, auth, src, old["confirmation_id"], [full("Projekte/alt.md", "alt")])
    names, p = _two_pass_plan(client, auth, src)
    first = apply(client, auth, src, p["confirmation_id"], [full(n, n) for n in p["next_pass"]]).json()
    assert first["status"] == "partial"
    bad = [{"path": n, "sha256": sha(n), "content": "anders"} for n in first["next_pass"]]
    assert apply(client, auth, src, first["confirmation_id"], bad).status_code == 400
    # the rest of the plan is void, also for a correct retry with the same confirmation
    retry = apply(client, auth, src, first["confirmation_id"], [full(n, n) for n in first["next_pass"]])
    assert retry.status_code == 409
    assert len(db.list_stored_files(src, "current")) == 41  # alt + 40 of the first pass, nothing lost
    assert [r["state"] for r in db.list_stored_files(src) if r["path_display"] == "Projekte/alt.md"] == ["current"]  # removal still deferred
    again = plan(client, auth, src, [entry(n, n) for n in names]).json()
    assert again["summary"] == {"new": 5, "changed": 0, "removed": 1, "unchanged": 40}  # nothing is uploaded twice
    assert again["passes"] == 1 and len(again["next_pass"]) == 5
    done = apply(client, auth, src, again["confirmation_id"], [full(n, n) for n in again["next_pass"]]).json()
    assert done["status"] == "done" and done["stored"] == 5 and done["removed"] == 1


def test_a_lost_answer_can_be_repeated_without_applying_twice(client, auth, db, src):
    p = plan(client, auth, src, [entry("Projekte/a.md", "A"), entry("Projekte/b.md", "B")]).json()
    files = [full("Projekte/a.md", "A"), full("Projekte/b.md", "B")]
    first = apply(client, auth, src, p["confirmation_id"], files).json()
    again = apply(client, auth, src, p["confirmation_id"], list(reversed(files)))  # the same files, any order
    assert again.status_code == 200 and again.json()["replayed"] is True
    assert {k: v for k, v in again.json().items() if k != "replayed"} == first
    assert db.count_events("source_synced") == 1 and len(db.list_stored_files(src)) == 2


def test_a_repeat_of_a_partial_pass_returns_the_same_next_step(client, auth, db, src):
    names, p = _two_pass_plan(client, auth, src)
    files = [full(n, n) for n in p["next_pass"]]
    first = apply(client, auth, src, p["confirmation_id"], files).json()
    again = apply(client, auth, src, p["confirmation_id"], files).json()
    assert again["replayed"] is True and again["confirmation_id"] == first["confirmation_id"]
    second = apply(client, auth, src, again["confirmation_id"], [full(n, n) for n in again["next_pass"]]).json()
    assert second["status"] == "done" and db.count_events("source_synced") == 2


def test_a_repeat_is_only_answered_for_exactly_the_same_request(client, auth, db, src):
    p = plan(client, auth, src, [entry("Projekte/a.md", "A"), entry("Projekte/b.md", "B")]).json()
    apply(client, auth, src, p["confirmation_id"], [full("Projekte/a.md", "A"), full("Projekte/b.md", "B")])
    for files in ([full("Projekte/a.md", "A")], [full("Projekte/a.md", "A"), full("Projekte/b.md", "B"), full("Projekte/c.md", "C")], []):
        assert apply(client, auth, src, p["confirmation_id"], files).status_code == 409
    assert apply(client, sign_in(client), src, p["confirmation_id"],
                 [full("Projekte/a.md", "A"), full("Projekte/b.md", "B")]).status_code == 409  # other session
    other = create(client, auth, "Zwei")
    assert apply(client, auth, other, p["confirmation_id"],
                 [full("Projekte/a.md", "A"), full("Projekte/b.md", "B")]).status_code == 409  # other source


def test_a_replay_expires_and_a_new_plan_shows_the_real_outcome(client, auth, db, src, clock):
    p = plan(client, auth, src, [entry("Projekte/a.md", "A")]).json()
    files = [full("Projekte/a.md", "A")]
    apply(client, auth, src, p["confirmation_id"], files)
    clock.t += 1000  # past the replay time of 900 s, still inside the sign-in
    assert apply(client, auth, src, p["confirmation_id"], files).status_code == 409
    # the app finds out what happened by planning again: everything is already there
    assert plan(client, auth, src, [entry("Projekte/a.md", "A")]).json()["summary"]["unchanged"] == 1


def test_two_identical_requests_at_the_same_moment_apply_once(admin_config, clock, db):
    import threading
    with TestClient(build_admin_app(admin_config, clock)) as c:
        h = sign_in(c)
        sid = create(c, h)
        allow(c, h, sid, "Projekte")
        p = plan(c, h, sid, [entry("Projekte/a.md", "A")]).json()
        results = []
        barrier = threading.Barrier(2)

        def go():
            barrier.wait()
            results.append(apply(c, h, sid, p["confirmation_id"], [full("Projekte/a.md", "A")]).status_code)

        threads = [threading.Thread(target=go) for _ in range(2)]
        [t.start() for t in threads]
        [t.join() for t in threads]
    assert sorted(results) in ([200, 200], [200, 409])  # a duplicate in flight is told to wait or gets the answer
    assert db.count_events("source_synced") == 1 and len(db.list_stored_files(sid)) == 1
