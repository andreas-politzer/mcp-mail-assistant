"""The admin API for folders, rules, the confirmation step and the audit log."""

import dataclasses

import pytest
from starlette.testclient import TestClient

from outbox_mcp.admin.app import build_admin_app
from outbox_mcp.admin.config import AdminConfig
from outbox_mcp.admin.folders import evaluate, load_rules
from outbox_mcp.auth.passwords import hash_password
from outbox_mcp.errors import DraftNotSendable, AttachmentRejected, RecipientNotAllowed
from outbox_mcp.security.attachments import AttachmentPolicy, key_parts, path_key
from outbox_mcp.service import OutboxService
from outbox_mcp.storage.database import Database
from outbox_mcp.storage.recipient_store import DbRecipientList, RecipientStore

PASSPHRASE = "correct horse battery staple"


class FakeClock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "vault"
    for folder in ("Projects/Privat", "Projects/Privatkopie", "Banking", "Banking-Alt", ".obsidian", "Empty"):
        (r / folder).mkdir(parents=True)
    files = {
        "Projects/a.md": "a", "Projects/b.md": "b", "Projects/Passwoerter.md": "x", "Projects/notes.txt": "t",
        "Projects/Privat/p.md": "p", "Projects/Privatkopie/c.md": "c", "Banking/x.md": "x", "Banking-Alt/y.md": "y",
        ".obsidian/h.md": "h", "top.md": "top",
    }
    for name, text in files.items():
        (r / name).write_text(text, encoding="utf-8")
    return r


@pytest.fixture
def clock():
    return FakeClock()


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


def tree(client, auth, path=""):
    r = client.get("/admin/folders", params={"path": path}, headers=auth)
    assert r.status_code == 200, r.text
    return {e["name"]: e for e in r.json()["entries"]}


def put(client, auth, path, effect, **extra):
    return client.put("/admin/folders/rule", json={"path": path, "effect": effect, **extra}, headers=auth)


def allow_with_confirmation(client, auth, path):
    first = put(client, auth, path, "allow").json()
    assert first["status"] == "confirmation_required"
    return put(client, auth, path, "allow", confirmation_id=first["confirmation_id"])


# -- the tree ---------------------------------------------------------------------------------

def test_root_listing_shows_folders_then_markdown_files_only(client, auth):
    entries = list(tree(client, auth))
    assert entries == ["Banking", "Banking-Alt", "Empty", "Projects", "top.md"]  # hidden folder skipped


def test_listing_skips_hidden_non_markdown_and_symlinks(client, auth, root, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "Projects" / "link").symlink_to(outside)
    (root / "Projects" / "filelink.md").symlink_to(root / "top.md")
    names = list(tree(client, auth, "Projects"))
    assert names == ["Privat", "Privatkopie", "a.md", "b.md", "Passwoerter.md"]
    assert "notes.txt" not in names and "link" not in names and "filelink.md" not in names


def test_entries_start_without_rules_and_flag_sensitive_names(client, auth):
    projects = tree(client, auth)["Projects"]
    assert projects["state"] == "none" and projects["source"] is None and projects["own_rule"] is None
    inner = tree(client, auth, "Projects")
    assert inner["Passwoerter.md"]["sensitive"] is True and inner["a.md"]["sensitive"] is False


def test_state_comes_with_its_reason_and_inheritance(client, auth):
    assert allow_with_confirmation(client, auth, "Projects").json()["status"] == "done"
    assert put(client, auth, "Projects/Privat", "deny").json()["status"] == "done"
    inner = tree(client, auth, "Projects")
    assert inner["a.md"]["state"] == "allowed" and inner["a.md"]["inherited"] is True and inner["a.md"]["source"] == "Projects"
    assert inner["Privat"]["state"] == "denied" and inner["Privat"]["own_rule"] == "deny"
    assert inner["Privatkopie"]["state"] == "allowed"  # Privat does not hit Privatkopie
    deep = tree(client, auth, "Projects/Privat")
    assert deep["p.md"]["state"] == "denied" and deep["p.md"]["inherited"] is True and deep["p.md"]["source"] == "Projects/Privat"


def test_ambiguous_siblings_are_marked(client, auth, root):
    (root / "Banking" / "Konto.md").write_text("1", encoding="utf-8")
    (root / "Banking" / "KONTO.md").write_text("2", encoding="utf-8")
    entries = tree(client, auth, "Banking")
    assert entries["Konto.md"]["ambiguous"] and entries["KONTO.md"]["ambiguous"] and not entries["x.md"]["ambiguous"]


def test_long_listings_are_cut_off_and_say_so(client, auth, root):
    big = root / "Big"
    big.mkdir()
    for i in range(505):
        (big / f"n{i:03}.md").write_text("x", encoding="utf-8")
    r = client.get("/admin/folders", params={"path": "Big"}, headers=auth).json()
    assert len(r["entries"]) == 500 and r["truncated"] is True


@pytest.mark.parametrize("bad", ["../x", "/etc", "a//b", ".obsidian", "Projects/.x", "nope", "top.md/x", "Projects/a.md", "Projects/notes.txt", "~"])
def test_bad_or_unknown_paths_are_refused(client, auth, bad):
    assert client.get("/admin/folders", params={"path": bad}, headers=auth).status_code == 400


def test_a_symlinked_folder_is_not_part_of_the_tree(client, auth, root, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "link").symlink_to(outside)
    assert client.get("/admin/folders", params={"path": "link"}, headers=auth).status_code == 400
    assert put(client, auth, "link", "deny").status_code == 400


def test_the_tree_needs_a_session_and_a_configured_root(client, tmp_path, clock):
    assert client.get("/admin/folders").status_code == 401
    cfg = AdminConfig(db_path=tmp_path / "other.db", passphrase_hash=hash_password(PASSPHRASE, n=2**4), allowed_hosts=("testserver",))
    with TestClient(build_admin_app(cfg, clock)) as c:
        h = sign_in(c)
        assert c.get("/admin/folders", headers=h).status_code == 400
        assert c.get("/admin/status", headers=h).json()["attachment_root"] is None


# -- the decision matches the attachment policy -----------------------------------------------

def test_evaluation_agrees_with_the_attachment_policy(root, tmp_path):
    db = Database(tmp_path / "x.db")
    for path, effect in [("Projects", "allow"), ("Projects/Privat", "deny"), ("Banking", "allow"), ("Banking/x.md", "deny"),
                         ("Projects/Privatkopie", "allow"), ("Banking-Alt", "deny")]:
        db.set_file_rule("attach", root, path, effect, 1.0, "t")
    db.set_file_rule("attach", root, "Projects/Privatkopie", "cleared", 2.0, "t")
    rules = load_rules(db, root)
    policy = AttachmentPolicy(root, [], [], rules=lambda: db.rule_lists("attach", root))
    for path in ["Projects/a.md", "Projects/Privat/p.md", "Projects/Privatkopie/c.md", "Banking/x.md",
                 "Banking/other.md", "Banking-Alt/y.md", "top.md", "Empty/z.md", "projects/A.md", "PROJECTS/privat/p.md"]:
        try:
            policy.still_allowed(path)
            by_policy = True
        except AttachmentRejected:
            by_policy = False
        assert (evaluate(key_parts(path_key(path)), rules)[0] == "allowed") == by_policy, path


# -- rules, tightening and loosening ----------------------------------------------------------------

def test_blocking_needs_no_confirmation_and_is_logged(client, auth, admin_config):
    r = put(client, auth, "Banking", "deny")
    assert r.status_code == 200 and r.json()["status"] == "done" and r.json()["effective"]["state"] == "denied"
    events = [e for e in Database(admin_config.db_path).audit_events() if e["event"] == "file_rule_set"]
    assert events[-1]["owner"] == "admin:web" and events[-1]["detail"] == "scope=attach path=Banking none -> deny"


def test_allowing_a_folder_asks_first_and_changes_nothing_until_confirmed(client, auth, admin_config, root):
    first = put(client, auth, "Projects", "allow").json()
    assert first["status"] == "confirmation_required" and first["expires_in"] == 120
    assert first["preview"]["files_attachable_before"] == 0
    assert first["preview"]["files_attachable_after"] == 4
    assert Database(admin_config.db_path).file_rules("attach", root) == []
    done = put(client, auth, "Projects", "allow", confirmation_id=first["confirmation_id"]).json()
    assert done["status"] == "done" and done["effective"]["state"] == "allowed"


def test_the_preview_counts_what_would_become_attachable(client, auth):
    first = put(client, auth, "Projects", "allow").json()["preview"]
    # Projects holds a.md, b.md, Privat/p.md, Privatkopie/c.md; Passwoerter.md is refused by name, notes.txt is no note
    assert (first["files_attachable_before"], first["files_attachable_after"]) == (0, 4)


def test_a_confirmation_works_once_only(client, auth):
    cid = put(client, auth, "Projects", "allow").json()["confirmation_id"]
    assert put(client, auth, "Projects", "allow", confirmation_id=cid).json()["status"] == "done"
    put(client, auth, "Projects", "clear")
    again = put(client, auth, "Projects", "allow", confirmation_id=cid)
    assert again.status_code == 409 and again.json()["error"] == "confirmation_invalid"


def test_a_confirmation_expires_after_two_minutes(client, auth, clock):
    cid = put(client, auth, "Projects", "allow").json()["confirmation_id"]
    clock.t += 121
    auth = sign_in(client)
    assert put(client, auth, "Projects", "allow", confirmation_id=cid).status_code == 409


def test_a_confirmation_is_bound_to_its_action_and_target(client, auth):
    cid = put(client, auth, "Projects", "allow").json()["confirmation_id"]
    assert put(client, auth, "Banking", "allow", confirmation_id=cid).status_code == 409
    # it was used up by the wrong try
    assert put(client, auth, "Projects", "allow", confirmation_id=cid).status_code == 409


def test_a_confirmation_is_bound_to_the_session(client, auth):
    cid = put(client, auth, "Projects", "allow").json()["confirmation_id"]
    other = sign_in(client)
    assert put(client, auth=other, path="Projects", effect="allow", confirmation_id=cid).status_code == 409


def test_a_confirmation_is_void_when_the_rules_changed_meanwhile(client, auth):
    cid = put(client, auth, "Projects", "allow").json()["confirmation_id"]
    assert put(client, auth, "Banking", "deny").json()["status"] == "done"
    assert put(client, auth, "Projects", "allow", confirmation_id=cid).status_code == 409


def test_invented_or_odd_confirmation_ids_are_refused(client, auth):
    for bad in ("nonsense", 5, [], ""):
        assert put(client, auth, "Projects", "allow", confirmation_id=bad).status_code == 409


def test_clearing_a_block_is_loosening_but_clearing_an_allow_is_not(client, auth):
    allow_with_confirmation(client, auth, "Projects")
    put(client, auth, "Banking", "deny")
    assert put(client, auth, "Projects", "clear").json()["status"] == "done"
    assert put(client, auth, "Banking", "clear").json()["status"] == "confirmation_required"


def test_repeating_a_rule_changes_nothing(client, auth, admin_config, root):
    put(client, auth, "Banking", "deny")
    before = len(Database(admin_config.db_path).audit_events())
    assert put(client, auth, "Banking", "deny").json()["status"] == "unchanged"
    assert put(client, auth, "Empty", "clear").json()["status"] == "unchanged"
    assert len(Database(admin_config.db_path).audit_events()) == before


def test_single_files_can_only_be_blocked(client, auth):
    assert put(client, auth, "top.md", "allow").status_code == 400
    assert put(client, auth, "top.md", "deny").json()["status"] == "done"


def test_allowing_is_refused_for_ambiguous_names(client, auth, root):
    (root / "Banking" / "Konto").mkdir()
    (root / "Banking" / "KONTO").mkdir()
    assert put(client, auth, "Banking/Konto", "allow").status_code == 400
    assert put(client, auth, "Banking/Konto", "deny").json()["status"] == "done"


@pytest.mark.parametrize("path", ["", "../x", "/abs", "a//b", ".obsidian", "nope", "Banking/", "Projects/notes.txt"])
def test_rule_paths_are_checked(client, auth, path):
    assert put(client, auth, path, "deny").status_code == 400


def test_bad_effects_and_bodies_are_refused(client, auth):
    assert put(client, auth, "Banking", "maybe").status_code == 400
    assert client.put("/admin/folders/rule", json={"effect": "deny"}, headers=auth).status_code == 400
    assert client.put("/admin/folders/rule", json={"path": 5, "effect": "deny"}, headers=auth).status_code == 400
    assert client.put("/admin/folders/rule", json={"path": "Banking", "effect": "deny"}).status_code == 401


def test_the_preview_shows_a_block_removal_too(client, auth):
    allow_with_confirmation(client, auth, "Projects")
    put(client, auth, "Projects/Privat", "deny")
    preview = put(client, auth, "Projects/Privat", "clear").json()["preview"]
    assert (preview["files_attachable_before"], preview["files_attachable_after"]) == (0, 1)


# -- unblock with confirmation ---------------------------------------------------------------------

@pytest.fixture
def blocked(client, auth):
    rid = client.post("/admin/recipients", json={"name": "Dora", "address": "dora@example.org"}, headers=auth).json()["recipient"]["id"]
    client.post(f"/admin/recipients/{rid}/block", headers=auth)
    return rid


def test_unblock_asks_first_and_then_lifts_only_the_block(client, auth, blocked):
    first = client.post(f"/admin/recipients/{blocked}/unblock", json={}, headers=auth).json()
    assert first["status"] == "confirmation_required" and first["preview"]["address"] == "dora@example.org"
    assert client.get("/admin/recipients?status=blocked", headers=auth).json()["recipients"][0]["id"] == blocked
    done = client.post(f"/admin/recipients/{blocked}/unblock", json={"confirmation_id": first["confirmation_id"]}, headers=auth)
    assert done.status_code == 200 and done.json()["recipient"]["status"] != "active"


def test_unblock_confirmation_is_single_use_and_bound(client, auth, blocked):
    cid = client.post(f"/admin/recipients/{blocked}/unblock", json={}, headers=auth).json()["confirmation_id"]
    other = client.post("/admin/recipients", json={"name": "Egon", "address": "egon@example.org"}, headers=auth).json()["recipient"]["id"]
    client.post(f"/admin/recipients/{other}/block", headers=auth)
    assert client.post(f"/admin/recipients/{other}/unblock", json={"confirmation_id": cid}, headers=auth).status_code == 409
    assert client.post(f"/admin/recipients/{blocked}/unblock", json={"confirmation_id": cid}, headers=auth).status_code == 409


def test_unblock_needs_a_blocked_address(client, auth):
    rid = client.post("/admin/recipients", json={"name": "Dora", "address": "dora@example.org"}, headers=auth).json()["recipient"]["id"]
    assert client.post(f"/admin/recipients/{rid}/unblock", json={}, headers=auth).status_code == 400
    assert client.post("/admin/recipients/999/unblock", json={}, headers=auth).status_code == 400


# -- audit endpoint ------------------------------------------------------------------------------------

def test_audit_lists_newest_first_and_pages(client, auth):
    for name in ("Banking", "Empty", "Banking-Alt"):
        put(client, auth, name, "deny")
    first = client.get("/admin/audit", params={"limit": 2}, headers=auth).json()
    assert len(first["entries"]) == 2 and first["entries"][0]["seq"] > first["entries"][1]["seq"]
    assert first["entries"][0]["detail"] == "scope=attach path=Banking-Alt none -> deny"
    rest = client.get("/admin/audit", params={"limit": 200, "before": first["next_before"]}, headers=auth).json()
    assert all(e["seq"] < first["next_before"] for e in rest["entries"]) and rest["next_before"] is None


def test_audit_shows_no_mail_content_and_no_secrets(client, auth, admin_config):
    client.post("/admin/login", json={"passphrase": "wrong"})
    entries = client.get("/admin/audit", headers=auth).json()["entries"]
    assert set(entries[0]) == {"seq", "ts", "event", "draft_id", "by", "address", "detail"}
    assert PASSPHRASE not in repr(entries) and auth["Authorization"].split()[1] not in repr(entries)


def test_audit_input_is_checked(client, auth):
    assert client.get("/admin/audit?limit=abc", headers=auth).status_code == 400
    assert client.get("/admin/audit?before=-1", headers=auth).status_code == 400
    assert len(client.get("/admin/audit?limit=0", headers=auth).json()["entries"]) <= 1
    assert client.get("/admin/audit").status_code == 401


# -- the point of it all: rules set here are what the mail service enforces ---------------------------

def test_a_rule_set_through_the_api_governs_the_mail_service(config, sender, clock, admin_config, root):
    db = Database(config.db_path)
    store = RecipientStore(db)
    store.add("Ferdinand", "ferdinand@example.org", [], 1.0, "admin:test")
    cfg = dataclasses.replace(config, attachment_root=root) if hasattr(config, "attachment_root") else config
    service = OutboxService(cfg, DbRecipientList(store), db, sender, clock, recipient_store=store)
    shared = dataclasses.replace(admin_config, db_path=config.db_path)
    with TestClient(build_admin_app(shared, FakeClock())) as c:
        h = sign_in(c)
        assert allow_with_confirmation(c, h, "Projects").json()["status"] == "done"
        draft = service.prepare_email("o", "Ferdinand", "S", "T", attachments=["Projects/a.md"])["draft_id"]
        assert put(c, h, "Projects", "deny").json()["status"] == "done"
    with pytest.raises(DraftNotSendable):  # the block cancelled the open draft at once
        service.send_email("o", draft)
    assert sender.sent == []
    assert db.get_draft(draft, "o").status == "cancelled"


# -- review round: deny is final, no symlink races, rule and audit are one unit, one protection list ---------

import sqlite3

from outbox_mcp.admin import folders as folders_module
from outbox_mcp.security import attachments as attachments_module
from outbox_mcp.security.attachments import is_sensitive_name


def _policy(db, root):
    return AttachmentPolicy(root, [], [], rules=lambda: db.rule_lists("attach", root))


def test_a_deeper_allow_never_overrides_a_higher_deny(client, auth, admin_config, root):
    put(client, auth, "Banking", "deny")
    (root / "Banking" / "Sub").mkdir()
    (root / "Banking" / "Sub" / "z.md").write_text("z", encoding="utf-8")
    # the API lets the owner store the deeper allow (it asks to confirm) ...
    assert allow_with_confirmation(client, auth, "Banking/Sub").status_code == 200
    # ... but the effect stays "denied" in the tree, in the evaluation and in the mail service's policy
    assert tree(client, auth, "Banking")["Sub"]["state"] == "denied"
    assert tree(client, auth, "Banking/Sub")["z.md"]["state"] == "denied"
    db = Database(admin_config.db_path)
    assert evaluate(key_parts(path_key("Banking/Sub/z.md")), load_rules(db, root))[0] == "denied"
    db.set_file_rule("attach", root, "Projects", "allow", 5.0, "t")
    with pytest.raises(AttachmentRejected):
        _policy(db, root).still_allowed("Banking/Sub/z.md")


def test_deny_wins_in_every_order_of_the_rules(tmp_path, root):
    for order in (("deny", "allow"), ("allow", "deny")):
        db = Database(tmp_path / f"{order[0]}.db")
        paths = {"deny": "Projects", "allow": "Projects/Privat"}
        for effect in order:
            db.set_file_rule("attach", root, paths[effect], effect, 1.0, "t")
        db.set_file_rule("attach", root, "Projects/Privat", "allow", 2.0, "t")
        assert evaluate(key_parts(path_key("Projects/Privat/p.md")), load_rules(db, root))[0] == "denied"
        with pytest.raises(AttachmentRejected):
            _policy(db, root).still_allowed("Projects/Privat/p.md")


def test_the_preview_never_counts_through_symbolic_links(client, auth, root, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    for i in range(3):
        (outside / f"o{i}.md").write_text("x", encoding="utf-8")
    (root / "Projects" / "link").symlink_to(outside)
    (root / "Projects" / "Privat" / "deeplink").symlink_to(outside)
    (root / "Projects" / "filelink.md").symlink_to(outside / "o0.md")
    preview = put(client, auth, "Projects", "allow").json()["preview"]
    assert preview["files_attachable_after"] == 4  # a, b, p, c: nothing from outside, no linked file


def test_a_link_that_appears_during_the_walk_is_not_followed(client, auth, root, tmp_path, monkeypatch):
    outside = tmp_path / "outside"
    outside.mkdir()
    for i in range(3):
        (outside / f"o{i}.md").write_text("x", encoding="utf-8")
    (root / "Race").mkdir()
    (root / "Race" / "r.md").write_text("r", encoding="utf-8")
    real = folders_module._children_fd
    swapped = []

    def listing_then_swap(fd):
        found = real(fd)
        if any(name == "Race" for name, _ in found) and not swapped:
            swapped.append(True)  # after the folder was listed as a real folder, before it is opened
            (root / "Race" / "r.md").unlink()
            (root / "Race").rmdir()
            (root / "Race").symlink_to(outside)
        return found

    monkeypatch.setattr(folders_module, "_children_fd", listing_then_swap)
    files, _ = folders_module.attachable_files(root, "")
    assert swapped and not any(f.startswith("Race") for f in files)
    assert not any("o0" in f or "o1" in f for f in files)


def test_a_folder_swapped_for_a_link_is_refused_as_a_start_point(client, auth, root, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "o.md").write_text("x", encoding="utf-8")
    (root / "Empty").rmdir()
    (root / "Empty").symlink_to(outside)
    assert client.get("/admin/folders", params={"path": "Empty"}, headers=auth).status_code == 400
    assert put(client, auth, "Empty", "allow").status_code == 400


def _break_audit(db_path):
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TRIGGER audit_breaks BEFORE INSERT ON audit_log WHEN NEW.event='file_rule_set' "
        "BEGIN SELECT RAISE(ABORT, 'audit is down'); END"
    )
    conn.commit()
    conn.close()


def test_rule_and_audit_entry_are_one_unit_in_the_database(admin_config, root):
    db = Database(admin_config.db_path)
    db.set_file_rule("attach", root, "Banking", "deny", 1.0, "t")
    _break_audit(admin_config.db_path)
    with pytest.raises(sqlite3.Error):
        db.set_file_rule("attach", root, "Banking", "cleared", 2.0, "t")
        db.set_file_rule("attach", root, "Projects", "deny", 3.0, "t")
    rows = {r["path_display"]: r["effect"] for r in db.file_rules("attach", root)}
    assert rows == {"Banking": "deny"}  # the failed change left nothing behind


def test_a_failing_audit_leaves_no_rule_change_through_the_endpoints(admin_config, root, clock):
    with TestClient(build_admin_app(admin_config, clock), raise_server_exceptions=False) as c:
        h = sign_in(c)
        assert put(c, h, "Banking", "deny").status_code == 200
        first = put(c, h, "Projects", "allow").json()
        _break_audit(admin_config.db_path)
        failed_block = put(c, h, "Empty", "deny")
        failed_allow = put(c, h, "Projects", "allow", confirmation_id=first["confirmation_id"])
        for r in (failed_block, failed_allow):
            assert r.status_code == 500 and r.json()["error"] == "internal"
            assert "audit" not in r.text  # no internals in the answer
    rows = {r["path_display"]: r["effect"] for r in Database(admin_config.db_path).file_rules("attach", root)}
    assert rows == {"Banking": "deny"}


def test_a_failing_audit_leaves_a_block_on_an_address_in_place(admin_config, clock):
    with TestClient(build_admin_app(admin_config, clock), raise_server_exceptions=False) as c:
        h = sign_in(c)
        rid = c.post("/admin/recipients", json={"name": "Dora", "address": "dora@example.org"}, headers=h).json()["recipient"]["id"]
        c.post(f"/admin/recipients/{rid}/block", headers=h)
        cid = c.post(f"/admin/recipients/{rid}/unblock", json={}, headers=h).json()["confirmation_id"]
        conn = sqlite3.connect(admin_config.db_path)
        conn.execute(
            "CREATE TRIGGER audit_breaks2 BEFORE INSERT ON audit_log WHEN NEW.event LIKE 'recipient_%' "
            "BEGIN SELECT RAISE(ABORT, 'audit is down'); END"
        )
        conn.commit()
        conn.close()
        assert c.post(f"/admin/recipients/{rid}/unblock", json={"confirmation_id": cid}, headers=h).status_code == 500
    assert RecipientStore(Database(admin_config.db_path)).find(rid).status == "blocked"


def test_one_protection_list_serves_policy_tree_and_preview(client, auth, admin_config, root):
    # every entry of the list is recognised by the shared helper
    for part in attachments_module._SENSITIVE_NAME_PARTS:
        assert is_sensitive_name(f"x{part}y.md"), part
    # the policy no longer carries its own copy of the check
    source = open(attachments_module.__file__, encoding="utf-8").read()
    assert source.count("_SENSITIVE_NAME_PARTS") == 2  # the definition and the helper
    # the test name really is protected, in the policy and in the tree
    assert is_sensitive_name("Passwoerter.md")
    db = Database(admin_config.db_path)
    db.set_file_rule("attach", root, "Projects", "allow", 1.0, "t")
    with pytest.raises(AttachmentRejected, match="sensitive"):
        _policy(db, root).still_allowed("Projects/Passwoerter.md")
    _policy(db, root).still_allowed("Projects/a.md")
    assert tree(client, auth, "Projects")["Passwoerter.md"]["sensitive"] is True
    assert put(client, auth, "Empty", "allow").json()["preview"]["files_attachable_after"] == 0


def test_a_sensitive_name_is_not_counted_in_the_preview(client, auth):
    # Projects holds a, b, Privat/p, Privatkopie/c and Passwoerter.md: only the first four count
    assert put(client, auth, "Projects", "allow").json()["preview"]["files_attachable_after"] == 4
