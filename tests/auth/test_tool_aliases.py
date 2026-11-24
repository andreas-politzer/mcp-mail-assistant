"""OUTBOX_TOOL_NAMES=neutral: tools are listed and called as tool_1..tool_8, texts follow."""

from __future__ import annotations

import json

import pytest

from outbox_mcp.http_server import build_http_app
from outbox_mcp.service import OutboxService
from outbox_mcp.tool_aliases import ALIASES, mode_from_env, rename_in_text

from test_probe import MAIL_TOOLS, _live, _session, _token


@pytest.fixture
def service(config, recipients, db, sender):
    return OutboxService(config, recipients, db, sender)


def _url(app):
    port, server, thread = _live(app)
    return f"http://127.0.0.1:{port}/mcp", server, thread


def test_switch_reads_the_environment_value():
    assert mode_from_env("neutral") and mode_from_env(" NEUTRAL ")
    assert not mode_from_env(None) and not mode_from_env("1") and not mode_from_env("")


def test_text_replacement_is_whole_word_only():
    assert rename_in_text("use send_email, not send_email_now or my_list_recipients") == \
        "use tool_6, not send_email_now or my_list_recipients"


def test_off_by_default(auth_config, store, service):
    app = build_http_app(auth_config, store, service)
    assert app.state.neutral_names is False
    url, server, thread = _url(app)
    try:
        async def work(client):
            return {t.name for t in (await client.list_tools()).tools}

        assert _session(url, _token(store, auth_config), work) == MAIL_TOOLS
    finally:
        server.should_exit = True
        thread.join(5)


def test_neutral_names_list_call_and_texts(auth_config, store, service):
    app = build_http_app(auth_config, store, service, neutral_names=True)
    url, server, thread = _url(app)
    try:
        async def work(client):
            tools = (await client.list_tools()).tools
            first = await client.call_tool("tool_1", {})
            real = await client.call_tool("list_recipients", {})
            status = await client.call_tool("tool_7", {"draft_id": "nope"})
            return tools, first, real, status, client.instructions

        tools, first, real, status, instructions = _session(url, _token(store, auth_config), work)
    finally:
        server.should_exit = True
        thread.join(5)
    assert {t.name for t in tools} == set(ALIASES.values())
    dump = json.dumps([t.model_dump(by_alias=True, exclude_none=True) for t in tools])
    for old in ALIASES:
        assert old not in dump and old not in (instructions or "")
    assert all(t.annotations is not None and t.annotations.title is None and t.title is None for t in tools)
    assert not first.is_error and not real.is_error
    texts = "".join(getattr(c, "text", "") for c in first.content + status.content)
    for old in ALIASES:
        assert old not in texts
    # arguments and safety hints stay as they were
    send = next(t for t in tools if t.name == "tool_6")
    assert send.annotations.destructive_hint is True and "draft_id" in send.input_schema["properties"]


def test_probe_modes_ignore_the_switch(auth_config, store, service):
    app = build_http_app(auth_config, store, service, probe="ping", neutral_names=True)
    assert app.state.neutral_names is False
