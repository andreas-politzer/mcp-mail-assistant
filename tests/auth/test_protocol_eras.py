"""Both protocol eras against the real HTTP server, with the SDK's own client.

Since 2026-07-28 clients open with server/discover and send no initialize. Older clients still
open with initialize. The server must serve both, or a client of the newer kind sees no tools.
"""

from __future__ import annotations

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
from outbox_mcp import __version__
from outbox_mcp.http_server import build_http_app
from outbox_mcp.service import OutboxService

TOOLS = {"list_recipients", "prepare_email", "send_email", "cancel_draft",
         "suggest_recipient", "list_suggestions", "list_attachments", "draft_status"}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def live_url(auth_config, store, config, recipients, db, sender):
    service = OutboxService(config, recipients, db, sender)
    app = build_http_app(auth_config, store, service)
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
    return httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}", "Host": "outbox.example.test"})


def _tool_names(url, token, mode):
    async def main():
        async with _http(token) as http:
            async with streamable_http_client(url, http_client=http) as streams:
                async with Client(_Streams(streams), mode=mode) as client:
                    names = {t.name for t in (await client.list_tools()).tools}
                    return client.protocol_version, names

    return anyio.run(main)


class _Streams:
    """Hands the open streams to Client as a transport."""

    def __init__(self, streams):
        self.streams = streams

    async def __aenter__(self):
        return self.streams

    async def __aexit__(self, *exc):
        return None


def test_a_client_of_the_newest_kind_sees_the_tools(live_url, store):
    version, names = _tool_names(live_url, _token(store), "auto")
    assert version == "2026-07-28"
    assert names == TOOLS


def test_an_older_client_with_initialize_still_sees_the_tools(live_url, store):
    version, names = _tool_names(live_url, _token(store), "legacy")
    assert version != "2026-07-28"
    assert names == TOOLS


def test_the_server_names_itself_and_says_what_it_is_for(live_url, store):
    async def main():
        async with _http(_token(store)) as http:
            async with streamable_http_client(live_url, http_client=http) as streams:
                async with Client(_Streams(streams), mode="auto") as client:
                    return client.server_info, client.instructions

    info, instructions = anyio.run(main)
    assert info.title == "Mail Assistant"
    assert info.version == __version__ and info.version
    assert "send_email" in instructions and "e-mail" in instructions
