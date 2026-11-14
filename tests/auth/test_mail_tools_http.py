"""The mail tools behind the OAuth-protected HTTP endpoint (stateless transport).

Everything runs against the real ASGI app. Mail goes to a fake sender, never to a server.
"""

from __future__ import annotations

import asyncio
import json
import threading

import httpx
import pytest
from starlette.testclient import TestClient

from auth_helpers import ISSUER, PASSWORD, RESOURCE
from outbox_mcp.auth.passwords import hash_password
from outbox_mcp.auth.store import AuthStore
from outbox_mcp.http_server import build_app_from_env, build_http_app
from outbox_mcp.service import OutboxService

HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def rpc(method, params=None, rid=1):
    body = {"jsonrpc": "2.0", "id": rid, "method": method}
    if params is not None:
        body["params"] = params
    return body


def call_body(name, arguments):
    return rpc("tools/call", {"name": name, "arguments": arguments})


def issue(store, *, subject="owner", resource=RESOURCE, scopes=("mail:send",)):
    family = store.new_family("some-client")
    return store.issue_tokens(
        family_id=family, client_id="some-client", scopes=list(scopes),
        resource=resource, subject=subject,
    ).access_token


def auth(token):
    return {**HEADERS, "Authorization": f"Bearer {token}"}


def tool_result(response):
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    text = result["content"][0]["text"]
    return result.get("isError", False), text


@pytest.fixture
def service(config, recipients, db, sender):
    return OutboxService(config, recipients, db, sender)


@pytest.fixture
def mail_app(auth_config, store, service):
    return build_http_app(auth_config, store, service, json_response=True)


@pytest.fixture
def mail_client(mail_app):
    with TestClient(mail_app, base_url=ISSUER, follow_redirects=False) as c:
        yield c


@pytest.fixture
def token(store):
    return issue(store)


def prepare(client, token, recipient="Ferdinand", subject="Hallo", text="Gruss"):
    resp = client.post("/mcp", json=call_body(
        "prepare_email", {"recipient": recipient, "subject": subject, "text": text}), headers=auth(token))
    failed, text_out = tool_result(resp)
    assert not failed, text_out
    return json.loads(text_out)


# --- access control ---------------------------------------------------------

def test_tools_need_a_token(mail_client):
    assert mail_client.post("/mcp", json=rpc("tools/list"), headers=HEADERS).status_code == 401
    resp = mail_client.post("/mcp", json=call_body("list_recipients", {}), headers=HEADERS)
    assert resp.status_code == 401
    assert "resource_metadata" in resp.headers["www-authenticate"]


def test_token_for_another_resource_is_refused(mail_client, store):
    t = issue(store, resource="https://vault.example.test/mcp")
    assert mail_client.post("/mcp", json=rpc("tools/list"), headers=auth(t)).status_code == 401


def test_token_with_foreign_scope_is_forbidden(mail_client, store):
    t = issue(store, scopes=("vault:read",))
    assert mail_client.post("/mcp", json=rpc("tools/list"), headers=auth(t)).status_code == 403


def test_revoked_token_stops_working_at_once(mail_client, store, token):
    assert mail_client.post("/mcp", json=rpc("tools/list"), headers=auth(token)).status_code == 200
    store.revoke_all()
    assert mail_client.post("/mcp", json=rpc("tools/list"), headers=auth(token)).status_code == 401


def test_a_real_login_token_works_for_the_tools(mail_client, store, clock):
    from auth_helpers import Flow

    login = Flow(mail_client, store, clock).full_login()
    resp = mail_client.post("/mcp", json=call_body("list_recipients", {}), headers=auth(login["access"]))
    failed, text = tool_result(resp)
    assert not failed and "ferdinand@example.org" in text


# --- tools ------------------------------------------------------------------

def test_tool_list_and_honest_annotations(mail_client, token):
    resp = mail_client.post("/mcp", json=rpc("tools/list"), headers=auth(token))
    tools = {t["name"]: t for t in resp.json()["result"]["tools"]}
    assert set(tools) == {"list_recipients", "prepare_email", "send_email", "cancel_draft"}
    ann = {name: t["annotations"] for name, t in tools.items()}
    assert ann["list_recipients"]["readOnlyHint"] is True
    for name in ("prepare_email", "send_email", "cancel_draft"):
        assert ann[name]["readOnlyHint"] is False
    assert ann["send_email"]["destructiveHint"] is True
    assert ann["send_email"]["openWorldHint"] is True
    assert ann["prepare_email"]["openWorldHint"] is False
    assert ann["prepare_email"]["destructiveHint"] is False


def test_prepare_then_send_sends_exactly_once(mail_client, token, sender, db):
    draft = prepare(mail_client, token)
    assert draft["to_address"] == "ferdinand@example.org"
    assert sender.sent == []  # preparing sends nothing
    resp = mail_client.post("/mcp", json=call_body("send_email", {"draft_id": draft["draft_id"]}),
                            headers=auth(token))
    failed, text = tool_result(resp)
    assert not failed and json.loads(text)["status"] == "sent"
    assert len(sender.sent) == 1 and sender.sent[0][2] == "ferdinand@example.org"
    again = mail_client.post("/mcp", json=call_body("send_email", {"draft_id": draft["draft_id"]}),
                             headers=auth(token))
    assert tool_result(again)[0] is True
    assert len(sender.sent) == 1


def test_recipient_outside_the_list_is_refused(mail_client, token, sender):
    resp = mail_client.post("/mcp", json=call_body(
        "prepare_email", {"recipient": "stranger@example.net", "subject": "x", "text": "y"}),
        headers=auth(token))
    assert tool_result(resp)[0] is True
    assert sender.sent == []


def test_cancelled_draft_cannot_be_sent(mail_client, token, sender):
    draft = prepare(mail_client, token)
    cancel = mail_client.post("/mcp", json=call_body("cancel_draft", {"draft_id": draft["draft_id"]}),
                              headers=auth(token))
    assert not tool_result(cancel)[0]
    send = mail_client.post("/mcp", json=call_body("send_email", {"draft_id": draft["draft_id"]}),
                            headers=auth(token))
    assert tool_result(send)[0] is True
    assert sender.sent == []


def test_owner_comes_from_the_token_subject(mail_client, store, token, db, sender):
    draft = prepare(mail_client, token)
    assert db.get_draft(draft["draft_id"], "owner").id == draft["draft_id"]  # stored under the subject
    other = issue(store, subject="someone-else")
    resp = mail_client.post("/mcp", json=call_body("send_email", {"draft_id": draft["draft_id"]}),
                            headers=auth(other))
    assert tool_result(resp)[0] is True
    assert sender.sent == []


def test_missing_subject_fails_closed():
    from mcp.server.auth.middleware.auth_context import auth_context_var
    from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
    from mcp.server.auth.provider import AccessToken

    from outbox_mcp.server import owner_from_token

    with pytest.raises(PermissionError):
        owner_from_token()  # no authenticated request at all
    tok = AccessToken(token="t", client_id="c", scopes=["mail:send"], subject=None)
    reset = auth_context_var.set(AuthenticatedUser(tok))
    try:
        with pytest.raises(PermissionError):
            owner_from_token()
    finally:
        auth_context_var.reset(reset)


# --- stateless and not blocking ---------------------------------------------

def test_stateless_calls_need_no_session_and_survive_a_restart(
        auth_config, store, service, token):
    first = build_http_app(auth_config, store, service, json_response=True)
    with TestClient(first, base_url=ISSUER) as c:
        r = c.post("/mcp", json=call_body("list_recipients", {}), headers=auth(token))
        assert r.status_code == 200 and "mcp-session-id" not in r.headers
    restarted = build_http_app(auth_config, AuthStore(auth_config.db_path), service, json_response=True)
    with TestClient(restarted, base_url=ISSUER) as c:  # new process, same database
        r = c.post("/mcp", json=call_body("list_recipients", {}), headers=auth(token))
        assert r.status_code == 200 and not tool_result(r)[0]


class SlowSender:
    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()
        self.sent = []

    def send(self, message, envelope_from, envelope_to):
        self.started.set()
        assert self.release.wait(10), "test never released the sender"
        self.sent.append(envelope_to)


def test_slow_smtp_does_not_block_the_login_page(auth_config, store, config, recipients, db, token):
    slow = SlowSender()
    app = build_http_app(auth_config, store, OutboxService(config, recipients, db, slow), json_response=True)
    draft_id = None

    async def scenario():
        nonlocal draft_id
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url=ISSUER) as c:
                prep = await c.post("/mcp", json=call_body(
                    "prepare_email", {"recipient": "Ferdinand", "subject": "s", "text": "t"}),
                    headers=auth(token))
                draft_id = json.loads(prep.json()["result"]["content"][0]["text"])["draft_id"]
                send = asyncio.create_task(c.post(
                    "/mcp", json=call_body("send_email", {"draft_id": draft_id}), headers=auth(token)))
                assert await asyncio.to_thread(slow.started.wait, 5), "send never started"
                # The mail is "in flight". Other requests must still be answered quickly.
                meta = await asyncio.wait_for(c.get("/.well-known/oauth-authorization-server"), 2)
                assert meta.status_code == 200
                other = await asyncio.wait_for(
                    c.post("/mcp", json=rpc("tools/list"), headers=auth(token)), 2)
                assert other.status_code == 200
                assert not send.done()
                slow.release.set()
                done = await asyncio.wait_for(send, 5)
                return json.loads(done.json()["result"]["content"][0]["text"])

    result = asyncio.run(scenario())
    assert result["status"] == "sent" and slow.sent == ["ferdinand@example.org"]


# --- start from environment ---------------------------------------------------

def _env(tmp_path):
    (tmp_path / "recipients.json").write_text(json.dumps({"recipients": [
        {"name": "Me", "address": "me@example.com", "aliases": ["myself"]}]}))
    return {
        "OUTBOX_SMTP_HOST": "localhost", "OUTBOX_SMTP_PORT": "2525", "OUTBOX_SMTP_SECURITY": "none",
        "OUTBOX_FROM_ADDRESS": "me@example.com",
        "OUTBOX_RECIPIENTS_FILE": str(tmp_path / "recipients.json"),
        "OUTBOX_DB_PATH": str(tmp_path / "mail.db"),
        "OUTBOX_PUBLIC_BASE_URL": ISSUER,
        "OUTBOX_OAUTH_PASSWORD_HASH": hash_password(PASSWORD, n=2**4),
        "OUTBOX_OAUTH_DB_PATH": str(tmp_path / "auth.db"),
    }


def test_app_from_environment_is_protected(tmp_path):
    app = build_app_from_env(_env(tmp_path))
    with TestClient(app, base_url=ISSUER) as c:
        assert c.post("/mcp", json=rpc("tools/list"), headers=HEADERS).status_code == 401
        assert c.get("/.well-known/oauth-protected-resource/mcp").status_code == 200


def test_environment_without_login_settings_is_refused(tmp_path):
    env = _env(tmp_path)
    del env["OUTBOX_OAUTH_PASSWORD_HASH"]
    from outbox_mcp.errors import ConfigError
    with pytest.raises(ConfigError):
        build_app_from_env(env)


def test_interrupted_sends_are_marked_unknown_at_start(tmp_path):
    from outbox_mcp.storage.database import Database

    env = _env(tmp_path)
    db = Database(tmp_path / "mail.db")
    from outbox_mcp.config import Config
    from outbox_mcp.security.recipients import RecipientList
    cfg = Config.from_env(env)
    svc = OutboxService(cfg, RecipientList.from_file(cfg.recipients_file), db, type("S", (), {"send": lambda *a: None})())
    draft_id = svc.prepare_email("owner", "Me", "s", "b")["draft_id"]
    db.claim_for_sending(draft_id, "owner", svc.clock(), cfg.limits)  # now stuck in 'sending'
    build_app_from_env(env)
    assert db.get_draft(draft_id, "owner").status == "unknown"
