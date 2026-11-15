"""The admin API: sign-in, sessions, browser protections, recipients, and independence from the mail service."""

import os
import pathlib
import subprocess
import sys

import pytest
from starlette.testclient import TestClient

from outbox_mcp.admin.app import build_admin_app
from outbox_mcp.admin.config import AdminConfig
from outbox_mcp.auth.passwords import hash_password
from outbox_mcp.errors import ConfigError, RecipientNotAllowed
from outbox_mcp.service import OutboxService
from outbox_mcp.storage.database import Database
from outbox_mcp.storage.recipient_store import DbRecipientList, RecipientStore

PASSPHRASE = "correct horse battery staple"
ORIGIN = "http://app.test"


class FakeClock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def admin_config(tmp_path):
    return AdminConfig(
        db_path=tmp_path / "mail.db",
        passphrase_hash=hash_password(PASSPHRASE, n=2**4),
        allowed_hosts=("testserver",),
        allowed_origins=(ORIGIN,),
    )


@pytest.fixture
def client(admin_config, clock):
    with TestClient(build_admin_app(admin_config, clock)) as c:
        yield c


def login(client, passphrase=PASSPHRASE, **headers):
    return client.post("/admin/login", json={"passphrase": passphrase}, headers=headers)


@pytest.fixture
def auth(client):
    token = login(client).json()["token"]
    return {"Authorization": f"Bearer {token}"}


# -- configuration ---------------------------------------------------------------------

def test_config_needs_only_database_and_passphrase_hash(tmp_path):
    cfg = AdminConfig.from_env({"OUTBOX_DB_PATH": str(tmp_path / "x.db"), "OUTBOX_ADMIN_PASSPHRASE_HASH": hash_password("abcdefghijkl", n=2**4)})
    assert cfg.allowed_origins == () and "localhost" in cfg.allowed_hosts


def test_config_refuses_a_missing_or_bad_hash(tmp_path):
    with pytest.raises(ConfigError):
        AdminConfig.from_env({"OUTBOX_DB_PATH": "x.db"})
    with pytest.raises(ConfigError):
        AdminConfig.from_env({"OUTBOX_DB_PATH": "x.db", "OUTBOX_ADMIN_PASSPHRASE_HASH": "plain-text"})


@pytest.mark.parametrize("origin", ["*", "https://*.example.com", "http://app.test/"])
def test_config_refuses_wildcard_and_sloppy_origins(origin):
    with pytest.raises(ConfigError):
        AdminConfig.from_env({
            "OUTBOX_DB_PATH": "x.db", "OUTBOX_ADMIN_PASSPHRASE_HASH": hash_password("abcdefghijkl", n=2**4),
            "OUTBOX_ADMIN_ALLOWED_ORIGINS": origin,
        })


# -- sign-in and sessions ----------------------------------------------------------------

def test_sign_in_gives_a_token_that_opens_the_api(client):
    r = login(client)
    assert r.status_code == 200 and len(r.json()["token"]) >= 32
    assert client.get("/admin/status", headers={"Authorization": f"Bearer {r.json()['token']}"}).status_code == 200


def test_everything_but_sign_in_needs_a_token(client):
    for method, path in [("get", "/admin/status"), ("get", "/admin/recipients"), ("post", "/admin/recipients"),
                         ("post", "/admin/recipients/1/approve"), ("post", "/admin/logout")]:
        assert getattr(client, method)(path).status_code == 401, path
    assert client.get("/admin/status", headers={"Authorization": "Bearer nonsense"}).status_code == 401
    assert client.get("/admin/status", headers={"Authorization": "Basic abc"}).status_code == 401


def test_wrong_passphrase_and_lockout_look_the_same(client, clock):
    wrong = login(client, "not the passphrase")
    for _ in range(4):
        login(client, "still wrong")
    locked = login(client, PASSPHRASE)  # the right one, but the lockout is on
    assert wrong.status_code == locked.status_code == 401
    assert wrong.json() == locked.json()
    clock.t += 15 * 60 + 1
    assert login(client).status_code == 200


def test_a_successful_sign_in_resets_the_failure_count(client):
    for _ in range(4):
        login(client, "wrong")
    assert login(client).status_code == 200
    for _ in range(4):
        login(client, "wrong")
    assert login(client).status_code == 200


def test_session_ends_after_20_minutes_without_use(client, clock, auth):
    clock.t += 19 * 60
    assert client.get("/admin/status", headers=auth).status_code == 200  # extends the idle time
    clock.t += 19 * 60
    assert client.get("/admin/status", headers=auth).status_code == 200
    clock.t += 21 * 60
    assert client.get("/admin/status", headers=auth).status_code == 401


def test_session_ends_after_4_hours_even_when_in_use(client, clock, auth):
    for _ in range(12):
        clock.t += 19 * 60
        client.get("/admin/status", headers=auth)
    clock.t += 19 * 60  # now past 4 hours
    assert client.get("/admin/status", headers=auth).status_code == 401


def test_logout_ends_the_session(client, auth):
    assert client.post("/admin/logout", headers=auth).status_code == 200
    assert client.get("/admin/status", headers=auth).status_code == 401


def test_only_a_few_sessions_stay_open(client):
    tokens = [login(client).json()["token"] for _ in range(7)]
    live = [t for t in tokens if client.get("/admin/status", headers={"Authorization": f"Bearer {t}"}).status_code == 200]
    assert live == tokens[-5:]


def test_a_restart_signs_everybody_out(admin_config, clock):
    with TestClient(build_admin_app(admin_config, clock)) as first:
        token = login(first).json()["token"]
    with TestClient(build_admin_app(admin_config, clock)) as second:
        assert second.get("/admin/status", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_sign_in_events_are_logged_without_secrets(client, admin_config):
    login(client, "wrong")
    token = login(client).json()["token"]
    events = Database(admin_config.db_path).audit_events()
    assert [e["event"] for e in events if e["event"].startswith("admin_")] == ["admin_login_failed", "admin_login"]
    dump = repr([tuple(e) for e in events])
    assert PASSPHRASE not in dump and token not in dump and "wrong" not in dump


def test_lockout_is_logged_once(client, admin_config):
    for _ in range(8):
        login(client, "wrong")
    names = [e["event"] for e in Database(admin_config.db_path).audit_events()]
    assert names.count("admin_login_failed") == 5 and names.count("admin_login_locked") == 1


# -- browser protections -----------------------------------------------------------------

def test_unknown_host_is_refused(client):
    assert client.post("/admin/login", json={"passphrase": PASSPHRASE}, headers={"Host": "evil.example"}).status_code == 400


def test_forwarded_host_is_not_trusted(client):
    r = client.post("/admin/login", json={"passphrase": PASSPHRASE}, headers={"X-Forwarded-Host": "testserver", "Host": "evil.example"})
    assert r.status_code == 400


def test_foreign_origin_is_refused_even_with_a_valid_token(client, auth):
    assert client.get("/admin/status", headers={**auth, "Origin": "http://evil.example"}).status_code == 403
    assert login(client, **{"Origin": "http://evil.example"}).status_code == 403


def test_allowed_origin_gets_exact_cors_headers(client, auth):
    r = client.get("/admin/status", headers={**auth, "Origin": ORIGIN})
    assert r.headers["access-control-allow-origin"] == ORIGIN and r.headers["vary"] == "Origin"
    assert "access-control-allow-credentials" not in r.headers
    assert "access-control-allow-origin" not in client.get("/admin/status", headers=auth).headers


def test_preflight_is_answered_only_for_allowed_origins(client):
    ok = client.options("/admin/recipients", headers={"Origin": ORIGIN, "Access-Control-Request-Method": "POST"})
    assert ok.status_code == 204 and ok.headers["access-control-allow-origin"] == ORIGIN
    assert "Authorization" in ok.headers["access-control-allow-headers"]
    assert client.options("/admin/recipients", headers={"Origin": "http://evil.example"}).status_code == 403
    assert client.options("/admin/recipients").status_code == 403


def test_large_and_malformed_bodies_are_refused(client, auth):
    assert client.post("/admin/recipients", content=b"x" * 20_000, headers={**auth, "Content-Type": "application/json"}).status_code == 413
    assert client.post("/admin/recipients", content=b"not json", headers=auth).status_code == 400
    assert client.post("/admin/recipients", content=b"[1]", headers=auth).status_code == 400
    assert login(client, 12345).status_code == 401


def test_responses_are_not_cached(client, auth):
    assert client.get("/admin/status", headers=auth).headers["cache-control"] == "no-store"


# -- recipients ----------------------------------------------------------------------------

def post(client, auth, path, body=None):
    return client.post(path, json=body or {}, headers=auth)


def test_status_counts_recipients_and_names_the_schema(client, auth):
    post(client, auth, "/admin/recipients", {"name": "Anna", "address": "anna@example.org"})
    s = client.get("/admin/status", headers=auth).json()
    assert s["schema_version"] == Database.SCHEMA_VERSION and s["recipients"]["active"] == 1


def test_add_list_and_filter(client, auth):
    r = post(client, auth, "/admin/recipients", {"name": "Anna", "address": "anna@example.org", "aliases": ["Anni"]})
    assert r.status_code == 201 and r.json()["recipient"]["status"] == "active"
    assert client.get("/admin/recipients?status=active", headers=auth).json()["recipients"][0]["aliases"] == ["Anni"]
    assert client.get("/admin/recipients?status=blocked", headers=auth).json()["recipients"] == []
    assert client.get("/admin/recipients?status=bogus", headers=auth).status_code == 400


def test_add_refuses_bad_input(client, auth):
    assert post(client, auth, "/admin/recipients", {"name": "", "address": "a@example.org"}).status_code == 400
    assert post(client, auth, "/admin/recipients", {"name": "A", "address": "no-address"}).status_code == 400
    assert post(client, auth, "/admin/recipients", {"name": "A", "address": "a@example.org", "aliases": "x"}).status_code == 400
    assert post(client, auth, "/admin/recipients", {"name": 5, "address": "a@example.org"}).status_code == 400


def test_approve_reject_block_remove(client, auth, admin_config):
    store = RecipientStore(Database(admin_config.db_path))
    a = store.suggest("claude", "Berta", "berta@example.org", "", 1.0).id
    b = store.suggest("claude", "Carl", "carl@example.org", "", 1.0).id
    ok = post(client, auth, f"/admin/recipients/{a}/approve", {"name": "Berta B.", "aliases": ["bb"]})
    assert ok.status_code == 200 and ok.json()["recipient"]["name"] == "Berta B."
    assert post(client, auth, f"/admin/recipients/{b}/reject").json()["recipient"]["status"] == "rejected"
    assert post(client, auth, f"/admin/recipients/{a}/block").json()["recipient"]["status"] == "blocked"
    c = post(client, auth, "/admin/recipients", {"name": "Dora", "address": "dora@example.org"}).json()["recipient"]["id"]
    assert post(client, auth, f"/admin/recipients/{c}/remove").json()["recipient"]["status"] == "removed"


def test_wrong_transitions_and_unknown_ids_are_refused(client, auth):
    c = post(client, auth, "/admin/recipients", {"name": "Dora", "address": "dora@example.org"}).json()["recipient"]["id"]
    assert post(client, auth, f"/admin/recipients/{c}/approve").status_code == 400  # not a suggestion
    assert post(client, auth, "/admin/recipients/999/block").status_code == 400
    assert post(client, auth, "/admin/recipients/abc/block").status_code in (404, 405)


def test_a_blocked_address_cannot_be_added_again(client, auth):
    c = post(client, auth, "/admin/recipients", {"name": "Dora", "address": "dora@example.org"}).json()["recipient"]["id"]
    post(client, auth, f"/admin/recipients/{c}/block")
    assert post(client, auth, "/admin/recipients", {"name": "Dora", "address": "dora@example.org"}).status_code == 409


def test_recipient_changes_are_logged_as_admin_web(client, auth, admin_config):
    post(client, auth, "/admin/recipients", {"name": "Dora", "address": "dora@example.org"})
    rows = [e for e in Database(admin_config.db_path).audit_events() if e["event"] == "recipient_added"]
    assert len(rows) == 1 and rows[0]["owner"] == "admin:web"


# -- independence from the mail service -------------------------------------------------------

def test_admin_code_does_not_load_the_mail_service_or_the_oauth_server():
    code = (
        "import sys, outbox_mcp.admin.app, outbox_mcp.admin.server\n"
        "bad = [m for m in sys.modules if m.startswith(('outbox_mcp.mail', 'outbox_mcp.server', "
        "'outbox_mcp.service', 'outbox_mcp.http_server', 'outbox_mcp.auth.app', 'outbox_mcp.auth.provider', "
        "'outbox_mcp.auth.store', 'outbox_mcp.auth.web', 'smtplib'))]\n"
        "print(bad)\n"
        "assert not bad, bad"
    )
    root = pathlib.Path(__file__).resolve().parents[1]
    done = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, cwd=root,
        env={"PYTHONPATH": str(root / "src"), "PATH": os.environ.get("PATH", "")},
    )
    assert done.returncode == 0, done.stdout + done.stderr


@pytest.fixture
def mail_service(config, sender, clock):
    db = Database(config.db_path)
    store = RecipientStore(db)
    store.add("Ferdinand", "ferdinand@example.org", [], 1.0, "admin:test")
    return OutboxService(config, DbRecipientList(store), db, sender, clock, recipient_store=store)


def test_mail_goes_out_without_any_admin_session(mail_service, sender):
    """No admin app, no session at all: sending through the connector works."""
    draft = mail_service.prepare_email("o", "Ferdinand", "S", "T")["draft_id"]
    assert mail_service.send_email("o", draft)["status"] == "sent"
    assert len(sender.sent) == 1


def test_an_expired_admin_session_does_not_touch_the_mail_service(config, mail_service, sender):
    admin_clock = FakeClock()
    admin_cfg = AdminConfig(
        db_path=config.db_path, passphrase_hash=hash_password(PASSPHRASE, n=2**4), allowed_hosts=("testserver",)
    )
    with TestClient(build_admin_app(admin_cfg, admin_clock)) as admin:
        token = login(admin).json()["token"]
        admin_clock.t += 5 * 60 * 60
        assert admin.get("/admin/status", headers={"Authorization": f"Bearer {token}"}).status_code == 401
    draft = mail_service.prepare_email("o", "Ferdinand", "S", "T")["draft_id"]
    assert mail_service.send_email("o", draft)["status"] == "sent"


def test_rules_are_checked_at_every_send_whatever_the_admin_session(config, mail_service, sender):
    """A block made through the admin API stops a draft that was already prepared."""
    draft = mail_service.prepare_email("o", "Ferdinand", "S", "T")["draft_id"]
    admin_cfg = AdminConfig(
        db_path=config.db_path, passphrase_hash=hash_password(PASSPHRASE, n=2**4), allowed_hosts=("testserver",)
    )
    with TestClient(build_admin_app(admin_cfg)) as admin:
        token = login(admin).json()["token"]
        rid = admin.get("/admin/recipients", headers={"Authorization": f"Bearer {token}"}).json()["recipients"][0]["id"]
        assert admin.post(f"/admin/recipients/{rid}/block", headers={"Authorization": f"Bearer {token}"}).status_code == 200
    with pytest.raises(RecipientNotAllowed):
        mail_service.send_email("o", draft)
    assert sender.sent == []
