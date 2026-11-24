"""The compatibility mode (OUTBOX_LEGACY_ERA): look like a server of the older protocol generation.

A client that handles both generations must fall back to initialize and then see every tool and
be able to call them. Requests without a token must still get the sign-in answer, and with the
mode off nothing changes (test_protocol_eras.py covers that).
"""

from __future__ import annotations

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

from auth_helpers import ISSUER
from outbox_mcp.compat import LegacyEra, mode_from_env, rejection
from outbox_mcp.http_server import build_http_app
from outbox_mcp.service import OutboxService

TOOLS = {"list_recipients", "prepare_email", "send_email", "cancel_draft",
         "suggest_recipient", "list_suggestions", "list_attachments", "draft_status"}
OLDER = ["2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25"]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(params=["old", "typed"])
def live_url(request, auth_config, store, config, recipients, db, sender):
    service = OutboxService(config, recipients, db, sender)
    app = LegacyEra(build_http_app(auth_config, store, service), request.param)
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    assert server.started, "test server did not start"
    yield f"http://127.0.0.1:{port}/mcp"
    server.should_exit = True
    thread.join(5)


def _token(store):
    family = store.new_family("some-client")
    return store.issue_tokens(
        family_id=family, client_id="some-client", scopes=["mail:send"],
        resource=ISSUER + "/mcp", subject="owner",
    ).access_token


def _http(token):
    headers = {"Host": "outbox.example.test"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return httpx2.AsyncClient(headers=headers)


class _Streams:
    def __init__(self, streams):
        self.streams = streams

    async def __aenter__(self):
        return self.streams

    async def __aexit__(self, *exc):
        return None


def _session(url, token, mode, work):
    async def main():
        async with _http(token) as http:
            async with streamable_http_client(url, http_client=http) as streams:
                async with Client(_Streams(streams), mode=mode) as client:
                    return await work(client)

    return anyio.run(main)


def test_a_client_of_the_newest_kind_falls_back_and_sees_every_tool(live_url, store):
    async def work(client):
        return client.protocol_version, {t.name for t in (await client.list_tools()).tools}

    version, names = _session(live_url, _token(store), "auto", work)
    assert version != "2026-07-28"
    assert names == TOOLS


def test_an_older_client_is_not_affected(live_url, store):
    async def work(client):
        return {t.name for t in (await client.list_tools()).tools}

    assert _session(live_url, _token(store), "legacy", work) == TOOLS


def test_a_tool_call_goes_through_after_the_fallback(live_url, store):
    async def work(client):
        result = await client.call_tool(
            "prepare_email", {"recipient": "Ferdinand", "subject": "Hi", "text": "   "}
        )
        return result.is_error, result.content[0].text

    failed, text = _session(live_url, _token(store), "auto", work)
    assert failed and "must not be empty" in text


def _post(url, body, headers, token=None):
    async def main():
        async with _http(token) as http:
            return await http.post(url, content=json.dumps(body), headers=headers)

    return anyio.run(main)


DISCOVER = {
    "jsonrpc": "2.0", "id": 7, "method": "server/discover",
    "params": {"_meta": {
        "io.modelcontextprotocol/protocolVersion": "2026-07-28",
        "io.modelcontextprotocol/clientInfo": {"name": "t", "version": "0"},
        "io.modelcontextprotocol/clientCapabilities": {},
    }},
}
MODERN_HEADERS = {
    "content-type": "application/json", "accept": "application/json, text/event-stream",
    "mcp-protocol-version": "2026-07-28", "mcp-method": "server/discover",
}


def test_without_a_token_the_sign_in_answer_is_unchanged(live_url):
    reply = _post(live_url, DISCOVER, MODERN_HEADERS)
    assert reply.status_code == 401


def test_the_refusal_has_the_expected_shape(live_url, store):
    reply = _post(live_url, DISCOVER, MODERN_HEADERS, _token(store))
    assert reply.status_code == 400
    error = reply.json()["error"]
    if error["code"] == -32022:
        assert reply.json()["id"] == 7
        assert error["data"] == {"supported": OLDER, "requested": "2026-07-28"}
    else:
        assert error["code"] == -32600 and reply.json()["id"] == "server-error"
        assert "2026-07-28" in error["message"] and "2025-11-25" in error["message"]


def test_a_modern_request_without_the_version_header_is_found_in_the_body(live_url, store):
    headers = {k: v for k, v in MODERN_HEADERS.items() if k != "mcp-protocol-version"}
    reply = _post(live_url, DISCOVER, headers, _token(store))
    assert reply.status_code == 400


def test_the_old_style_answer_matches_what_an_sdk_1x_server_sends():
    # Recorded from a stateless server on mcp 1.30.0 asked with protocol version 2026-07-28.
    assert rejection("old", "2026-07-28", 7) == {
        "jsonrpc": "2.0",
        "id": "server-error",
        "error": {
            "code": -32600,
            "message": "Bad Request: Unsupported protocol version: 2026-07-28. "
                       "Supported versions: 2024-11-05, 2025-03-26, 2025-06-18, 2025-11-25",
        },
    }


@pytest.mark.parametrize("value,expected", [
    (None, None), ("", None), ("0", None), ("no", None), ("off", None),
    ("1", "old"), ("true", "old"), ("YES", "old"), ("old", "old"), ("typed", "typed"), (" Typed ", "typed"),
])
def test_the_switch_reads_the_environment_value(value, expected):
    assert mode_from_env(value) == expected
