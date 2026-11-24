"""The probe matrix (OUTBOX_PROBE=matrix): tool list per ?v= letter, dummy tools only."""

from __future__ import annotations

import json

import pytest

from outbox_mcp.diag import RequestLog, variant_of
from outbox_mcp.http_server import build_http_app
from outbox_mcp.probe import mode_from_env
from outbox_mcp.probe_matrix import plain_schema, variant_from_query
from outbox_mcp.service import OutboxService

from test_probe import MAIL_TOOLS, _live, _session, _token


@pytest.fixture
def service(config, recipients, db, sender):
    return OutboxService(config, recipients, db, sender)


@pytest.fixture
def matrix_url(auth_config, store, service):
    app = build_http_app(auth_config, store, service, probe="matrix")
    port, server, thread = _live(app)
    yield f"http://127.0.0.1:{port}/mcp"
    server.should_exit = True
    thread.join(5)


def _tools(url, token, query="", mode="auto"):
    async def work(client):
        return (await client.list_tools()).tools

    return _session(url + query, token, work, mode)


def _has_anyof(tool):
    return any("anyOf" in p for p in (tool.input_schema.get("properties") or {}).values())


def test_matrix_is_a_known_mode():
    assert mode_from_env("matrix") == "matrix"


@pytest.mark.parametrize("raw,expected", [
    (b"v=b", "b"), (b"x=1&v=e", "e"), (b"v=B", ""), (b"v=", ""), (b"", ""),
    (b"v=abcdefghi", ""), (b"v=b%00", ""), (b"v=../x", ""),
])
def test_only_a_short_lowercase_letter_is_taken_from_the_address(raw, expected):
    assert variant_from_query(raw) == expected
    assert variant_of({"query_string": raw}) == expected


def test_default_and_unknown_variant_offer_only_ping(matrix_url, store, auth_config):
    token = _token(store, auth_config)
    for query in ("", "?x=5", "?v=z", "?v=PING"):
        assert {t.name for t in _tools(matrix_url, token, query)} == {"ping"}


@pytest.mark.parametrize("mode", ["auto", "legacy"])
@pytest.mark.parametrize("letter", ["b", "c", "d", "e"])
def test_each_variant_offers_the_eight_tools(matrix_url, store, auth_config, letter, mode):
    tools = _tools(matrix_url, _token(store, auth_config), f"?v={letter}", mode)
    assert {t.name for t in tools} == MAIL_TOOLS


def test_variants_differ_exactly_as_described(matrix_url, store, auth_config):
    token = _token(store, auth_config)
    got = {v: {t.name: t for t in _tools(matrix_url, token, f"?v={v}")} for v in "bcde"}
    for letter, (plain, annotated) in {"b": (True, False), "c": (True, True),
                                       "d": (False, False), "e": (False, True)}.items():
        tools = got[letter].values()
        assert all(not _has_anyof(t) for t in tools) == plain, letter
        assert all(t.annotations is not None for t in tools) == annotated, letter
        assert all(t.annotations is None for t in tools) == (not annotated), letter
    assert _has_anyof(got["e"]["prepare_email"]) and _has_anyof(got["e"]["list_attachments"])
    assert not _has_anyof(got["b"]["prepare_email"])
    # names, descriptions and required fields are the same everywhere
    for name in MAIL_TOOLS:
        assert len({got[v][name].description for v in "bcde"}) == 1
        assert len({json.dumps(got[v][name].input_schema.get("required")) for v in "bcde"}) == 1


def test_tools_only_reach_the_dummy(matrix_url, store, auth_config):
    token = _token(store, auth_config)

    async def work(client):
        out = []
        for name, args in [("list_recipients", {}), ("prepare_email", {"recipient": "x@example.org", "subject": "s", "text": "t"}),
                           ("send_email", {"draft_id": "d"}), ("list_attachments", {})]:
            res = await client.call_tool(name, args)
            out.append((res.is_error, "".join(getattr(c, "text", "") for c in res.content)))
        return out

    for is_error, content in _session(matrix_url + "?v=e", token, work):
        assert not is_error and json.loads(content) == {"probe": True}


def test_plain_schema_drops_nullable_branches():
    schema = {"properties": {
        "a": {"anyOf": [{"items": {"type": "string"}, "type": "array"}, {"type": "null"}], "default": None, "title": "A"},
        "b": {"type": "integer", "default": 50, "title": "B"},
    }, "type": "object"}
    plain = plain_schema(schema)
    assert plain["properties"]["a"] == {"type": "array", "items": {"type": "string"}, "title": "A"}
    assert plain["properties"]["b"] == {"type": "integer", "default": 50, "title": "B"}
    assert "anyOf" in schema["properties"]["a"]  # input untouched


def test_the_log_names_the_variant_and_nothing_else_of_the_query(auth_config, store, service):
    lines = []
    app = RequestLog(build_http_app(auth_config, store, service, probe="matrix"), log=lines.append)
    port, server, thread = _live(app)
    try:
        _tools(f"http://127.0.0.1:{port}/mcp", _token(store, auth_config), "?v=d&secret=hunter2")
    finally:
        server.should_exit = True
        thread.join(5)
    text = "\n".join(lines)
    assert "variant=d" in text
    assert "hunter2" not in text and "secret" not in text
