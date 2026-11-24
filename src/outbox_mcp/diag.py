"""Optional request log for finding out why a client does not get the tool list.

Switched on with OUTBOX_DEBUG_HTTP=1 (HTTP server only). It writes one line per request to the
sign-in and tool endpoints: method, path, status and a few protocol headers. For the tool
endpoint it adds the JSON-RPC method (and the tool name for tool calls). For errors (status 400
and up) it adds the first 200 characters of the answer the server gave.

It never writes mail text, recipients, tool arguments, tokens, cookies, the Authorization header
or query strings, and it skips the approval pages, whose addresses carry secrets.
"""

from __future__ import annotations

import json
import sys
from typing import Any, Callable, Optional

_LOGGED_PATHS = {"/mcp", "/token", "/register", "/authorize"}
_LOGGED_PREFIXES = ("/.well-known/",)
_HEADERS = ("accept", "content-type", "mcp-protocol-version", "mcp-session-id", "user-agent")
_MAX_BODY = 64 * 1024
_ERROR_TEXT = 200


def _stderr(line: str) -> None:
    print(line, file=sys.stderr, flush=True)


def _wanted(path: str) -> bool:
    return path in _LOGGED_PATHS or path.startswith(_LOGGED_PREFIXES)


def rpc_summary(body: bytes) -> str:
    """JSON-RPC method names only (plus tool names for tools/call), never arguments."""
    try:
        data = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return "rpc=unparseable" if body else "rpc=none"
    items = data if isinstance(data, list) else [data]
    parts = []
    for item in items:
        if not isinstance(item, dict):
            parts.append("?")
            continue
        method = item.get("method")
        if not isinstance(method, str):
            parts.append("response" if "result" in item or "error" in item else "?")
            continue
        name = ""
        if method == "tools/call":
            params = item.get("params")
            tool = params.get("name") if isinstance(params, dict) else None
            if isinstance(tool, str):
                name = f"({tool[:40]})"
        parts.append(method[:60] + name)
    return "rpc=" + ",".join(parts)


class RequestLog:
    def __init__(self, app: Any, log: Optional[Callable[[str], None]] = None) -> None:
        self.app = app
        self.log = log or _stderr

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "") if scope.get("type") == "http" else ""
        if not _wanted(path):
            await self.app(scope, receive, send)
            return

        is_mcp = path == "/mcp"
        body = bytearray()
        status = 0
        error = bytearray()

        async def recv():
            message = await receive()
            if is_mcp and message.get("type") == "http.request" and len(body) < _MAX_BODY:
                body.extend(message.get("body", b"")[: _MAX_BODY - len(body)])
            return message

        async def out(message):
            nonlocal status
            if message.get("type") == "http.response.start":
                status = message.get("status", 0)
            elif message.get("type") == "http.response.body" and status >= 400 and len(error) < _ERROR_TEXT:
                error.extend(message.get("body", b"")[: _ERROR_TEXT - len(error)])
            await send(message)

        try:
            await self.app(scope, recv, out)
        finally:
            headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
            fields = [f"{scope.get('method', '?')} {path} -> {status}"]
            for name in _HEADERS:
                if name in headers:
                    fields.append(f"{name}={headers[name][:80]!r}")
            if is_mcp and scope.get("method") == "POST":
                fields.append(rpc_summary(bytes(body)))
            if status >= 400 and error:
                fields.append("answer=" + error.decode("utf-8", "replace").replace("\n", " ")[:_ERROR_TEXT])
            self.log("DIAG " + " ".join(fields))
