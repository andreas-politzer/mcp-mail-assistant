import asyncio
import json

from outbox_mcp.diag import RequestLog, rpc_summary


def run(app, scope, body=b""):
    lines = []
    wrapped = RequestLog(app, lines.append)
    sent = []

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        sent.append(message)

    asyncio.run(wrapped(scope, receive, send))
    return lines, sent


def make_app(status, answer=b"ok"):
    async def app(scope, receive, send):
        await receive()
        await send({"type": "http.response.start", "status": status, "headers": []})
        await send({"type": "http.response.body", "body": answer})

    return app


def scope(path="/mcp", method="POST", headers=None):
    return {
        "type": "http",
        "path": path,
        "method": method,
        "query_string": b"code=SECRET",
        "headers": headers or [],
    }


def test_rpc_summary_names_methods_and_tools_but_no_arguments():
    body = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "send_email", "arguments": {"text": "GEHEIM"}}}
    ).encode()
    out = rpc_summary(body)
    assert out == "rpc=tools/call(send_email)"
    assert "GEHEIM" not in out


def test_rpc_summary_handles_batches_and_garbage():
    assert rpc_summary(b"") == "rpc=none"
    assert rpc_summary(b"nonsense") == "rpc=unparseable"
    batch = json.dumps([{"method": "initialize"}, {"method": "tools/list"}]).encode()
    assert rpc_summary(batch) == "rpc=initialize,tools/list"


def test_logs_status_headers_and_method_for_mcp():
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).encode()
    headers = [(b"mcp-protocol-version", b"2025-11-25"), (b"user-agent", b"claude-test"), (b"authorization", b"Bearer TOPSECRET")]
    lines, _ = run(make_app(200), scope(headers=headers), body)
    assert len(lines) == 1
    line = lines[0]
    assert line.startswith("DIAG POST /mcp -> 200")
    assert "mcp-protocol-version='2025-11-25'" in line
    assert "rpc=tools/list" in line
    assert "TOPSECRET" not in line and "SECRET" not in line.replace("TOPSECRET", "")


def test_error_answer_is_logged_and_cut():
    lines, _ = run(make_app(400, b"x" * 1000), scope(), b"{}")
    assert "-> 400" in lines[0]
    assert "answer=" + "x" * 200 in lines[0]
    assert "x" * 201 not in lines[0]


def test_success_answer_is_not_logged():
    lines, _ = run(make_app(200, b"secret-answer"), scope(), b"{}")
    assert "secret-answer" not in lines[0]


def test_other_paths_are_not_logged_and_still_served():
    for path in ("/approve/abc123", "/login", "/health"):
        lines, sent = run(make_app(200), scope(path=path))
        assert lines == []
        assert sent[0]["status"] == 200


def test_sign_in_paths_log_without_query_string():
    lines, _ = run(make_app(200), scope(path="/token"))
    assert lines[0].startswith("DIAG POST /token -> 200")
    assert "SECRET" not in lines[0]


def test_discover_and_listen_get_a_longer_excerpt_and_timing():
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "server/discover"}).encode()
    lines, _ = run(make_app(200, b'{"result":{"supportedVersions":["2026-07-28"]}}'), scope(), body)
    assert "bytes=" in lines[0] and "ms=" in lines[0]
    assert "peek=" in lines[0] and "supportedVersions" in lines[0]
    assert "request=" in lines[0]


def test_ordinary_methods_log_no_excerpt():
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "send_email", "arguments": {"text": "GEHEIM"}}}).encode()
    lines, _ = run(make_app(200, b'{"result":"GEHEIM-ANTWORT"}'), scope(), body)
    assert "peek=" not in lines[0] and "request=" not in lines[0]
    assert "GEHEIM" not in lines[0]


def test_initialize_gets_the_excerpt_too():
    body = json.dumps({"jsonrpc": "2.0", "id": 0, "method": "initialize",
                       "params": {"protocolVersion": "2025-11-25", "clientInfo": {"name": "some-client"}}}).encode()
    lines, _ = run(make_app(200, b'{"result":{"serverInfo":{"name":"outbox"}}}'), scope(), body)
    assert "request=" in lines[0] and "some-client" in lines[0]
    assert "peek=" in lines[0] and "serverInfo" in lines[0]
