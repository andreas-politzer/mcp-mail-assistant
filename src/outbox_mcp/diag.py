"""Optional request log for finding out why a client does not get the tool list.

Switched on with OUTBOX_DEBUG_HTTP=1 (HTTP server only). It writes one line per request to the
sign-in and tool endpoints: method, path, status and a few protocol headers. For the tool
endpoint it adds the JSON-RPC method (and the tool name for tool calls). For errors (status 400
and up) it adds the first 200 characters of the answer the server gave.

It never writes mail text, recipients, tool arguments, tokens, cookies, the Authorization header
or query strings (except the probe variant letter, see probe.py), and it skips the approval pages, whose addresses carry secrets.
"""

from __future__ import annotations

import json
import re
import sys
import time
from urllib.parse import parse_qs
from typing import Any, Callable, Optional

_LOGGED_PATHS = {"/mcp", "/token", "/register", "/authorize"}
_LOGGED_PREFIXES = ("/.well-known/",)
_HEADERS = ("accept", "content-type", "mcp-protocol-version", "mcp-session-id", "user-agent")
_MAX_BODY = 64 * 1024
_ERROR_TEXT = 200
_PEEK_TEXT = 400
# For these JSON-RPC methods the answer (and for subscriptions/listen the request) holds only
# server metadata, client identity and subscription filters, so a longer excerpt is logged even on success.
_PEEK_METHODS = ("server/discover", "subscriptions/listen", "initialize")


def variant_of(scope: dict) -> str:
    """The probe variant named in the address (?v=b): one to eight lower-case letters or digits, else ''."""
    try:
        values = parse_qs(scope.get("query_string", b"").decode("ascii", "ignore")).get("v", [])
    except ValueError:
        return ""
    value = values[0] if values else ""
    return value if re.fullmatch(r"[a-z0-9]{1,8}", value) else ""


def _stderr(line: str) -> None:
    print(line, file=sys.stderr, flush=True)


def _wanted(path: str) -> bool:
    return path in _LOGGED_PATHS or path.startswith(_LOGGED_PREFIXES)


def rpc_methods(body: bytes) -> list:
    try:
        data = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return []
    items = data if isinstance(data, list) else [data]
    return [i["method"] for i in items if isinstance(i, dict) and isinstance(i.get("method"), str)]


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
    def __init__(
        self, app: Any, log: Optional[Callable[[str], None]] = None, mcp_path: str = "/mcp"
    ) -> None:
        self.app = app
        self.mcp_path = mcp_path
        self.log = log or _stderr

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "") if scope.get("type") == "http" else ""
        if not (_wanted(path) or (path == self.mcp_path and scope.get("type") == "http")):
            await self.app(scope, receive, send)
            return

        is_mcp = path == self.mcp_path
        body = bytearray()
        status = 0
        error = bytearray()
        peek = bytearray()
        size = 0
        started = time.monotonic()

        async def recv():
            message = await receive()
            if is_mcp and message.get("type") == "http.request" and len(body) < _MAX_BODY:
                body.extend(message.get("body", b"")[: _MAX_BODY - len(body)])
            return message

        async def out(message):
            nonlocal status, size
            if message.get("type") == "http.response.start":
                status = message.get("status", 0)
            elif message.get("type") == "http.response.body":
                chunk = message.get("body", b"")
                size += len(chunk)
                if status >= 400 and len(error) < _ERROR_TEXT:
                    error.extend(chunk[: _ERROR_TEXT - len(error)])
                if len(peek) < _PEEK_TEXT:
                    peek.extend(chunk[: _PEEK_TEXT - len(peek)])
            await send(message)

        try:
            await self.app(scope, recv, out)
        finally:
            headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
            fields = [f"{scope.get('method', '?')} {path} -> {status}"]
            for name in _HEADERS:
                if name in headers:
                    fields.append(f"{name}={headers[name][:80]!r}")
            fields.append(f"bytes={size} ms={int((time.monotonic() - started) * 1000)}")
            variant = variant_of(scope)
            if variant:
                fields.append(f"variant={variant}")
            if is_mcp and scope.get("method") == "POST":
                fields.append(rpc_summary(bytes(body)))
                if any(m in _PEEK_METHODS for m in rpc_methods(bytes(body))):
                    fields.append("request=" + bytes(body[:_PEEK_TEXT]).decode("utf-8", "replace").replace("\n", " "))
                    fields.append("peek=" + bytes(peek).decode("utf-8", "replace").replace("\n", " "))
            if status >= 400 and error:
                fields.append("answer=" + error.decode("utf-8", "replace").replace("\n", " ")[:_ERROR_TEXT])
            self.log("DIAG " + " ".join(fields))
