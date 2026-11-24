"""The diagnostic probe (OUTBOX_PROBE): one harmless tool, nothing else, normal sign-in or none.

In both modes no mail tool exists, so no client can read, prepare or send anything.
"""

from __future__ import annotations

import dataclasses
import json
import socket
import threading
import time

import anyio
import httpx2
import pytest
import uvicorn
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from starlette.testclient import TestClient

from auth_helpers import CALLBACK, ISSUER, Flow
from outbox_mcp.diag import RequestLog
from outbox_mcp.http_server import build_http_app
from outbox_mcp.probe import mode_from_env
from outbox_mcp.service import OutboxService

MAIL_TOOLS = {"list_recipients", "prepare_email", "send_email", "cancel_draft",
              "suggest_recipient", "list_suggestions", "list_attachments", "draft_status"}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _live(app):
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    assert server.started, "test server did not start"
    return port, server, thread


def _root_config(auth_config):
    return dataclasses.replace(auth_config, mcp_path="/", scopes=())


@pytest.fixture
def service(config, recipients, db, sender):
    return OutboxService(config, recipients, db, sender)


@pytest.fixture(params=["ping", "ping-root"])
def probe_url(request, auth_config, store, service):
    cfg = _root_config(auth_config) if request.param == "ping-root" else auth_config
    app = build_http_app(cfg, store, service, probe=request.param)
    port, server, thread = _live(app)
    yield request.param, cfg, f"http://127.0.0.1:{port}{cfg.mcp_path}"
    server.should_exit = True
    thread.join(5)


def _token(store, cfg):
    family = store.new_family("some-client")
    return store.issue_tokens(
        family_id=family, client_id="some-client", scopes=list(cfg.scopes),
        resource=cfg.resource_url, subject="owner",
    ).access_token


class _Streams:
    def __init__(self, streams):
        self.streams = streams

    async def __aenter__(self):
        return self.streams

    async def __aexit__(self, *exc):
        return None


def _session(url, token, work, mode="auto"):
    async def main():
        headers = {"Authorization": f"Bearer {token}", "Host": "outbox.example.test"}
        async with httpx2.AsyncClient(headers=headers) as http:
            async with streamable_http_client(url, http_client=http) as streams:
                async with Client(_Streams(streams), mode=mode) as client:
                    return await work(client)

    return anyio.run(main)


@pytest.mark.parametrize("value,expected", [
    (None, None), ("", None), ("0", None), ("1", None), ("PING", "ping"),
    (" ping-root ", "ping-root"), ("ping-everything", None),
])
def test_the_switch_reads_the_environment_value(value, expected):
    assert mode_from_env(value) == expected


@pytest.mark.parametrize("mode", ["auto", "legacy"])
def test_only_ping_is_offered_and_it_answers(probe_url, store, mode):
    _, cfg, url = probe_url

    async def work(client):
        listed = await client.list_tools()
        names = {t.name for t in listed.tools}
        called = await client.call_tool("ping", {})
        return names, listed.tools[0].annotations, called.is_error, client.server_info, client.instructions

    names, annotations, failed, info, instructions = _session(url, _token(store, cfg), work, mode)
    assert names == {"ping"}
    assert not (names & MAIL_TOOLS)
    assert annotations.read_only_hint is True and annotations.open_world_hint is False
    assert not failed
    assert "mail" not in (instructions or "").lower() and "mail" not in (info.title or "").lower()


def test_the_probe_does_not_change_the_normal_server(auth_config, store, service):
    app = build_http_app(auth_config, store, service)
    assert app.state.probe is None and app.state.mcp_path == "/mcp"
    port, server, thread = _live(app)
    try:
        async def work(client):
            return {t.name for t in (await client.list_tools()).tools}

        names = _session(f"http://127.0.0.1:{port}/mcp", _token(store, auth_config), work)
    finally:
        server.should_exit = True
        thread.join(5)
    assert names == MAIL_TOOLS


def test_in_root_mode_the_endpoint_moves_and_the_old_path_is_gone(auth_config, store, service):
    cfg = _root_config(auth_config)
    app = build_http_app(cfg, store, service, probe="ping-root")
    assert app.state.mcp_path == "/" and app.state.probe == "ping-root"
    with TestClient(app, base_url=ISSUER, follow_redirects=False) as client:
        token = _token(store, cfg)
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json, text/event-stream",
                   "Content-Type": "application/json"}
        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        assert client.post("/mcp", json=body, headers=headers).status_code in (404, 405)
        assert client.get("/.well-known/oauth-protected-resource").json()["resource"] == cfg.resource_url
        assert client.get("/.well-known/oauth-protected-resource").json().get("scopes_supported") in ([], None)


def test_sign_in_without_a_scope_works_end_to_end(auth_config, store, service, clock):
    cfg = _root_config(auth_config)
    app = build_http_app(cfg, store, service, probe="ping-root", json_response=True)
    with TestClient(app, base_url=ISSUER, follow_redirects=False) as client:
        flow = Flow(client, store, clock)
        client_id = flow.new_client_id(scope="")
        verifier, challenge = flow.pkce()
        # With no scopes on offer a client sends no scope parameter at all.
        asked = client.get("/authorize", params={
            "response_type": "code", "client_id": client_id, "redirect_uri": CALLBACK, "state": "st-1",
            "code_challenge": challenge, "code_challenge_method": "S256"})
        request_id = flow.request_id(asked)
        done = flow.login(request_id)
        assert done.status_code == 303, done.text
        code = flow.query_of(done)["code"][0]
        tok = flow.exchange(client_id, code, verifier)
        assert tok.status_code == 200, tok.text
        access = tok.json()["access_token"]
        headers = {"Authorization": f"Bearer {access}", "Accept": "application/json, text/event-stream",
                   "Content-Type": "application/json"}
        assert client.post("/", json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-03-26", "capabilities": {},
            "clientInfo": {"name": "t", "version": "0"}}}, headers=headers).status_code == 200
        assert client.post("/", json={"jsonrpc": "2.0", "id": 2, "method": "initialize", "params": {
            "protocolVersion": "2025-03-26", "capabilities": {},
            "clientInfo": {"name": "t", "version": "0"}}}).status_code == 401


def test_the_request_log_follows_the_endpoint_to_the_root():
    lines = []

    async def inner(scope, receive, send):
        await receive()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})

    log = RequestLog(inner, log=lines.append, mcp_path="/")

    async def main():
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).encode()
        sent = [{"type": "http.request", "body": body, "more_body": False}]

        async def receive():
            return sent.pop(0)

        async def send(message):
            return None

        await log({"type": "http", "method": "POST", "path": "/", "headers": []}, receive, send)

    anyio.run(main)
    assert len(lines) == 1 and lines[0].startswith("DIAG POST / -> 200") and "rpc=tools/list" in lines[0]


def test_the_compatibility_mode_follows_the_endpoint_to_the_root():
    from outbox_mcp.compat import LegacyEra

    reached = []

    async def inner(scope, receive, send):
        reached.append(scope["path"])
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})

    wrapped = LegacyEra(inner, "typed", "/")

    async def call(path):
        statuses = []
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "server/discover"}).encode()
        sent = [{"type": "http.request", "body": body, "more_body": False}]

        async def receive():
            return sent.pop(0) if sent else {"type": "http.disconnect"}

        async def send(message):
            if message["type"] == "http.response.start":
                statuses.append(message["status"])

        headers = [(b"authorization", b"Bearer x"), (b"mcp-protocol-version", b"2026-07-28")]
        await wrapped({"type": "http", "method": "POST", "path": path, "headers": headers}, receive, send)
        return statuses[0]

    assert anyio.run(call, "/") == 400 and reached == []
    assert anyio.run(call, "/mcp") == 200 and reached == ["/mcp"]
