"""Section 3: external recipients need a passkey approval per draft; own addresses do not.
The approval pages are tested through the real routes with real signatures."""

import io
import re
import sqlite3

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from approval_helpers import Device, ORIGIN, RP_ID
from outbox_mcp import admin_cli
from outbox_mcp.approval.service import ApprovalConfig, ApprovalService
from outbox_mcp.approval.web import AttemptLimiter, FAILURES_PER_TARGET, build_approval_routes
from outbox_mcp.errors import DraftNotSendable
from outbox_mcp.service import OutboxService
from outbox_mcp.storage.database import Database
from outbox_mcp.storage.recipient_store import DbRecipientList, RecipientStore

OWN = "me@example.com"
EXT = "ferdinand@example.org"


class World:
    def __init__(self, config, sender, clock, with_approval=True):
        self.clock, self.sender = clock, sender
        self.db = Database(config.db_path)
        self.store = RecipientStore(self.db)
        self.store.add("Me", OWN, ["myself"], 1.0, "admin:test", scope="own")
        self.store.add("Ferdinand", EXT, [], 1.0, "admin:test")
        self.approval = ApprovalService(self.db, ApprovalConfig(rp_id=RP_ID), clock) if with_approval else None
        self.svc = OutboxService(
            config, DbRecipientList(self.store), self.db, sender, clock,
            recipient_store=self.store, approval=self.approval,
        )
        self.device = Device()

    def enroll(self):
        token = self.approval.create_enrollment()
        ok, opts = self.approval.enroll_options(token)
        cid = opts["challenge_id"]
        ok, msg = self.approval.enroll_verify(token, {"challenge_id": cid, "credential": self.device.register(cid)})
        assert ok, msg

    def approve(self, draft_id):
        ok, opts = self.approval.approve_options(draft_id)
        assert ok, opts
        cid = opts["challenge_id"]
        ok, msg = self.approval.approve_verify(
            draft_id, {"challenge_id": cid, "credential": self.device.assert_(cid)}
        )
        assert ok, msg


@pytest.fixture
def world(config, sender, clock):
    w = World(config, sender, clock)
    w.enroll()
    return w


# -- sending ---------------------------------------------------------------------------

def test_own_address_needs_no_approval(world, sender):
    d = world.svc.prepare_email("o", "me", "S", "T")
    assert "approval_required" not in d
    assert world.svc.send_email("o", d["draft_id"])["status"] == "sent"
    assert len(sender.sent) == 1


def test_external_prepare_carries_the_link(world):
    d = world.svc.prepare_email("o", "Ferdinand", "S", "T")
    assert d["approval_required"] is True
    assert d["approval_url"] == f"{ORIGIN}/approve/{d['draft_id']}"
    assert "approval_url" in d["instructions"]


def test_external_is_not_sent_without_approval(world, sender):
    d = world.svc.prepare_email("o", "Ferdinand", "S", "T")["draft_id"]
    result = world.svc.send_email("o", d)
    assert result["status"] == "approval_required" and result["approval_url"].endswith(d)
    assert sender.sent == []
    # the draft is still there and still pending: after the approval it goes out
    world.approve(d)
    assert world.svc.send_email("o", d)["status"] == "sent"
    assert len(sender.sent) == 1


def test_approval_is_used_once(world, sender):
    d = world.svc.prepare_email("o", "Ferdinand", "S", "T")["draft_id"]
    world.approve(d)
    assert world.svc.send_email("o", d)["status"] == "sent"
    with pytest.raises(DraftNotSendable):
        world.svc.send_email("o", d)
    assert len(sender.sent) == 1


def test_approval_of_one_draft_does_not_cover_another(world, sender):
    a = world.svc.prepare_email("o", "Ferdinand", "S", "A")["draft_id"]
    b = world.svc.prepare_email("o", "Ferdinand", "S", "B")["draft_id"]
    world.approve(a)
    assert world.svc.send_email("o", b)["status"] == "approval_required"
    assert sender.sent == []


def test_approval_for_other_content_does_not_count(world, sender):
    d = world.svc.prepare_email("o", "Ferdinand", "S", "T")["draft_id"]
    with sqlite3.connect(world.db.path) as c:
        c.execute(
            "INSERT INTO draft_approvals(draft_id, body_sha256, credential_id, approved_at) VALUES (?,?,?,?)",
            (d, "0" * 64, "x", 1.0),
        )
    assert world.svc.send_email("o", d)["status"] == "approval_required"
    assert sender.sent == []


def test_expired_approval_cannot_send(world, sender, clock):
    d = world.svc.prepare_email("o", "Ferdinand", "S", "T")["draft_id"]
    world.approve(d)
    clock.t += 601
    with pytest.raises(DraftNotSendable):
        world.svc.send_email("o", d)
    assert sender.sent == []


def test_scope_switched_to_external_after_prepare_needs_approval(world, sender):
    d = world.svc.prepare_email("o", "me", "S", "T")["draft_id"]
    world.store.set_scope(OWN, "external", 2.0, "admin:test")
    assert world.svc.send_email("o", d)["status"] == "approval_required"
    assert sender.sent == []


def test_without_approval_setup_external_is_refused(config, sender, clock):
    w = World(config, sender, clock, with_approval=False)
    d = w.svc.prepare_email("o", "Ferdinand", "S", "T")
    assert d["approval_required"] is True and d["approval_url"] is None
    r = w.svc.send_email("o", d["draft_id"])
    assert r["status"] == "approval_required" and r["approval_url"] is None
    assert sender.sent == []
    # own addresses keep working
    assert w.svc.send_email("o", w.svc.prepare_email("o", "me", "S", "T")["draft_id"])["status"] == "sent"


def test_without_passkey_external_is_refused(config, sender, clock):
    w = World(config, sender, clock)
    d = w.svc.prepare_email("o", "Ferdinand", "S", "T")
    assert d["approval_url"] is None
    assert w.svc.send_email("o", d["draft_id"])["status"] == "approval_required"
    assert sender.sent == []


def test_audit_trail(world):
    d = world.svc.prepare_email("o", "Ferdinand", "S", "T")["draft_id"]
    world.approve(d)
    ok, opts = world.approval.approve_options(world.svc.prepare_email("o", "Ferdinand", "S", "U")["draft_id"])
    with sqlite3.connect(world.db.path) as c:
        events = [r[0] for r in c.execute("SELECT event FROM audit_log WHERE event LIKE 'approval%' ORDER BY seq")]
    assert events[:2] == ["approval_issued", "approval_done"]


# -- the pages ---------------------------------------------------------------------------

@pytest.fixture
def web(world):
    app = Starlette(routes=build_approval_routes(world.approval, AttemptLimiter(world.clock)))
    return TestClient(app, base_url=ORIGIN)


JSON = {"Origin": ORIGIN, "Content-Type": "application/json"}


def test_page_shows_the_whole_draft_and_changes_nothing(world, web):
    text = "Zeile 1\n" + "x" * 3000 + "\n<b>Ende</b>"
    d = world.svc.prepare_email("o", "Ferdinand", "Hallo <i>Welt</i>", text)["draft_id"]
    before = _dump(world)
    r = web.get(f"/approve/{d}")
    assert r.status_code == 200
    assert "Ende" in r.text and "x" * 3000 in r.text
    assert "&lt;b&gt;Ende&lt;/b&gt;" in r.text and "<b>Ende</b>" not in r.text  # escaped
    assert "Hallo &lt;i&gt;Welt&lt;/i&gt;" in r.text and ferdinand_shown(r.text)
    assert "Approve with passkey" in r.text
    assert _dump(world) == before  # a GET writes nothing


def test_approved_page_tells_the_person_to_go_back_and_never_redirects(world, web):
    d = world.svc.prepare_email("o", "Ferdinand", "S", "T")["draft_id"]
    page = web.get(f"/approve/{d}").text
    assert '<div id="done" class="done" hidden' in page and 'id="close"' in page
    assert "claude.ai" not in page  # no link away from the page
    js = web.get("/approval/app.js").text
    assert "location.assign" not in js and "location.replace" not in js and "claude.ai" not in js
    assert "window.close()" in js


def test_pages_offer_help_when_the_window_cannot_do_passkeys(world, web):
    d = world.svc.prepare_email("o", "Ferdinand", "S", "T")["draft_id"]
    token = world.approval.create_enrollment()
    for page in (web.get(f"/approve/{d}").text, web.get(f"/passkey/enroll/{token}").text):
        assert '<div id="help" class="note" hidden>' in page and 'id="copy"' in page
    js = web.get("/approval/app.js").text
    assert "window.PublicKeyCredential" in js and "navigator.clipboard.writeText" in js


def ferdinand_shown(html):
    return "ferdinand@example.org" in html


def _dump(world):
    with sqlite3.connect(world.db.path) as c:
        return [
            c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in ("drafts", "audit_log", "passkey_challenges", "draft_approvals", "passkey_enrollments")
        ]


def test_page_lists_attachments_with_size(config, sender, clock, tmp_path):
    from outbox_mcp.security.attachments import Attachment
    import hashlib
    w = World(config, sender, clock)
    w.enroll()
    att = Attachment(name="plan.txt", data=b"a" * 2048, sha256=hashlib.sha256(b"a" * 2048).hexdigest(), source="")
    draft = w.db.create_draft("o", "Ferdinand", EXT, "S", "T", clock.t, 600, w.svc.config.limits, [att])
    app = Starlette(routes=build_approval_routes(w.approval))
    r = TestClient(app, base_url=ORIGIN).get(f"/approve/{draft.id}")
    assert "plan.txt (2.0 KB)" in r.text


def test_security_headers_everywhere(world, web):
    d = world.svc.prepare_email("o", "Ferdinand", "S", "T")["draft_id"]
    for r in (web.get(f"/approve/{d}"), web.get("/approve/nope"), web.get("/approval/app.js"),
              web.get("/approval/style.css"), web.post(f"/approve/{d}/options", headers=JSON, json={})):
        h = r.headers
        assert h["cache-control"] == "no-store" and h["x-frame-options"] == "DENY"
        assert h["referrer-policy"] == "no-referrer" and h["x-content-type-options"] == "nosniff"
        assert "script-src 'self'" in h["content-security-policy"] and "unsafe-inline" not in h["content-security-policy"]
    assert "<script>" not in web.get(f"/approve/{d}").text.replace('<script src="/approval/app.js">', "")


def test_unknown_draft_and_methods(world, web):
    assert web.get("/approve/drf_nope").status_code == 404
    assert web.get("/approve/drf_nope/verify").status_code == 405
    assert web.get("/passkey/enroll/nope").status_code == 404
    assert web.post("/approve/x", headers=JSON, json={}).status_code == 405


def test_post_needs_the_right_origin_and_json(world, web):
    d = world.svc.prepare_email("o", "Ferdinand", "S", "T")["draft_id"]
    url = f"/approve/{d}/options"
    assert web.post(url, json={}).status_code == 400                                   # no Origin
    assert web.post(url, headers={**JSON, "Origin": "https://evil.example.org"}, json={}).status_code == 400
    assert web.post(url, headers={"Origin": ORIGIN, "Content-Type": "text/plain"}, content="{}").status_code == 400
    assert web.post(url, headers=JSON, content="not json").status_code == 400
    assert web.post(url, headers=JSON, content=b"{" + b"1" * 70_000).status_code == 400
    assert web.post(url, headers=JSON, json={}).status_code == 200


def test_full_approval_through_the_pages(world, web, sender):
    d = world.svc.prepare_email("o", "Ferdinand", "S", "T")["draft_id"]
    o = web.post(f"/approve/{d}/options", headers=JSON, json={}).json()
    assert o["ok"] and o["publicKey"]["userVerification"] == "required"
    cid = o["challenge_id"]
    bad = web.post(f"/approve/{d}/verify", headers=JSON,
                   json={"challenge_id": cid, "credential": world.device.assert_(cid, tamper=True)})
    assert bad.status_code == 400 and bad.json()["ok"] is False
    assert world.svc.send_email("o", d)["status"] == "approval_required"
    o = web.post(f"/approve/{d}/options", headers=JSON, json={}).json()
    cid = o["challenge_id"]
    ok = web.post(f"/approve/{d}/verify", headers=JSON,
                  json={"challenge_id": cid, "credential": world.device.assert_(cid)})
    assert ok.status_code == 200 and ok.json()["ok"] is True
    assert "approved" in web.get(f"/approve/{d}").text.lower() and "Approve with passkey" not in web.get(f"/approve/{d}").text
    assert world.svc.send_email("o", d)["status"] == "sent"
    assert len(sender.sent) == 1


def test_failed_attempts_lock_the_draft_page(world, web):
    d = world.svc.prepare_email("o", "Ferdinand", "S", "T")["draft_id"]
    for _ in range(FAILURES_PER_TARGET):
        o = web.post(f"/approve/{d}/options", headers=JSON, json={}).json()
        cid = o["challenge_id"]
        r = web.post(f"/approve/{d}/verify", headers=JSON,
                     json={"challenge_id": cid, "credential": world.device.assert_(cid, tamper=True)})
        assert r.status_code == 400
    assert web.post(f"/approve/{d}/options", headers=JSON, json={}).status_code == 429


def test_global_post_limit(world, web):
    codes = {web.post("/approve/drf_nope/options", headers=JSON, json={}).status_code for _ in range(40)}
    assert 429 in codes


def test_enrollment_through_the_pages(config, sender, clock):
    w = World(config, sender, clock)
    app = Starlette(routes=build_approval_routes(w.approval))
    web = TestClient(app, base_url=ORIGIN)
    token = w.approval.create_enrollment()
    assert "Register passkey" in web.get(f"/passkey/enroll/{token}").text
    o = web.post(f"/passkey/enroll/{token}/options", headers=JSON, json={}).json()
    assert o["ok"]
    cid = o["challenge_id"]
    r = web.post(f"/passkey/enroll/{token}/verify", headers=JSON,
                 json={"challenge_id": cid, "credential": w.device.register(cid)})
    assert r.json()["ok"] is True and w.approval.passkey_count() == 1
    assert web.get(f"/passkey/enroll/{token}").status_code == 404  # used up
    assert web.post(f"/passkey/enroll/{token}/options", headers=JSON, json={}).status_code == 400


def test_http_app_serves_the_pages_only_when_approval_is_set_up(config, sender, clock, tmp_path):
    from outbox_mcp.auth.store import AuthStore
    from outbox_mcp.http_server import build_http_app
    w = World(config, sender, clock)
    paths = lambda svc: {getattr(r, "path", "") for r in build_http_app(_auth(tmp_path), AuthStore(tmp_path / "a.db"), svc).router.routes}
    assert "/approve/{draft_id}" in paths(w.svc)
    w.svc.approval = None
    assert "/approve/{draft_id}" not in paths(w.svc)


def _auth(tmp_path):
    from outbox_mcp.auth.config import AuthConfig
    return AuthConfig(base_url="https://relay.example.org", password_hash="x", db_path=tmp_path / "a.db")


# -- CLI ---------------------------------------------------------------------------------

def run_cli(argv, tmp_path, host=RP_ID):
    out = io.StringIO()
    env = {"OUTBOX_DB_PATH": str(tmp_path / "cli.db")}
    if host:
        env["OUTBOX_APPROVAL_HOST"] = host
    code = admin_cli.main(argv, env=env, stdout=out)
    return code, out.getvalue()


def test_cli_enroll_list_remove(tmp_path):
    code, text = run_cli(["passkey-enroll"], tmp_path)
    assert code == 0 and re.search(rf"{re.escape(ORIGIN)}/passkey/enroll/\S{{20,}}", text)
    assert run_cli(["passkey-list"], tmp_path)[1].strip() == "No passkeys registered."
    code, text = run_cli(["passkey-enroll"], tmp_path, host="")
    assert code == 2 and "OUTBOX_APPROVAL_HOST" in text
    assert run_cli(["passkey-remove", "abcdef"], tmp_path)[0] == 1
