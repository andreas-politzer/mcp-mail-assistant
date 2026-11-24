"""Optional compatibility mode: look like a server of the older protocol generation.

Some clients (the voice mode of claude.ai, as far as we can tell) only offer a connector that
answers like a server from before 2026-07-28. This switch makes the HTTP server do that: a request
that speaks the newest protocol version is refused, so a client that handles both generations falls
back to the older `initialize` handshake. Requests of the older kind pass through unchanged.

Switched on with OUTBOX_LEGACY_ERA (HTTP server only), off by default:

  old    answer exactly like a server of the SDK's 1.x series: HTTP 400 and JSON-RPC code -32600
  typed  answer with the error the newer protocol defines for this case: HTTP 400, code -32022,
         and the list of older versions the server does speak

Only requests that carry a bearer token are refused. Without one the sign-in layer answers as
before, so connecting and signing in are not touched. Nothing is logged or stored here.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from mcp_types.version import HANDSHAKE_PROTOCOL_VERSIONS, MODERN_PROTOCOL_VERSIONS

MODE_OLD = "old"
MODE_TYPED = "typed"
_ON_VALUES = {"1", "true", "yes", "on", MODE_OLD}
_VERSION_META_KEY = "io.modelcontextprotocol/protocolVersion"
_MODERN_METHODS = {"server/discover", "subscriptions/listen"}
_PEEK_LIMIT = 64 * 1024


def mode_from_env(value: Optional[str]) -> Optional[str]:
    """The compatibility mode named by OUTBOX_LEGACY_ERA, or None when it is off."""
    text = (value or "").strip().lower()
    if text == MODE_TYPED:
        return MODE_TYPED
    if text in _ON_VALUES:
        return MODE_OLD
    return None


def _header(scope: dict, name: bytes) -> str:
    for key, value in scope.get("headers", []):
        if key.lower() == name:
            return value.decode("latin-1")
    return ""


def _body_looks_modern(body: bytes) -> bool:
    try:
        data = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return False
    items = data if isinstance(data, list) else [data]
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get("method") in _MODERN_METHODS:
            return True
        params = item.get("params")
        meta = params.get("_meta") if isinstance(params, dict) else None
        if isinstance(meta, dict) and _VERSION_META_KEY in meta:
            return True
    return False


def _first_id(body: bytes) -> Any:
    try:
        data = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    item = data[0] if isinstance(data, list) and data else data
    value = item.get("id") if isinstance(item, dict) else None
    return value if isinstance(value, (str, int)) else None


def _requested_version(scope: dict, body: bytes) -> str:
    header = _header(scope, b"mcp-protocol-version")
    if header:
        return header
    try:
        data = json.loads(body.decode("utf-8"))
        item = data[0] if isinstance(data, list) and data else data
        return str(item["params"]["_meta"][_VERSION_META_KEY])
    except (ValueError, UnicodeDecodeError, KeyError, TypeError, IndexError):
        return MODERN_PROTOCOL_VERSIONS[-1]


def rejection(mode: str, requested: str, request_id: Any) -> dict:
    """The JSON-RPC answer for a request of the newest protocol version."""
    if mode == MODE_TYPED:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {
                "code": -32022,
                "message": "Unsupported protocol version",
                "data": {"supported": list(HANDSHAKE_PROTOCOL_VERSIONS), "requested": requested},
            },
        }
    return {
        "jsonrpc": "2.0",
        "id": "server-error",
        "error": {
            "code": -32600,
            "message": (
                f"Bad Request: Unsupported protocol version: {requested}. "
                f"Supported versions: {', '.join(HANDSHAKE_PROTOCOL_VERSIONS)}"
            ),
        },
    }


class LegacyEra:
    """ASGI middleware that refuses requests of the newest protocol version on the tool endpoint."""

    def __init__(self, app: Any, mode: str, mcp_path: str = "/mcp") -> None:
        if mode not in (MODE_OLD, MODE_TYPED):
            raise ValueError(f"unknown compatibility mode: {mode!r}")
        self.app = app
        self.mode = mode
        self.mcp_path = mcp_path

    async def __call__(self, scope, receive, send):
        if (
            scope.get("type") != "http"
            or scope.get("method") != "POST"
            or scope.get("path") != self.mcp_path
            or not _header(scope, b"authorization").lower().startswith("bearer ")
        ):
            await self.app(scope, receive, send)
            return

        header_version = _header(scope, b"mcp-protocol-version")
        header_modern = header_version in MODERN_PROTOCOL_VERSIONS
        # A request that names an older version in its header is the older kind; only a request
        # without the header needs a look into the (small) body.
        need_body = not header_modern and not header_version
        body = b""
        replay = None
        if header_modern or need_body:
            chunks, size, more = [], 0, True
            while more and size <= _PEEK_LIMIT:
                message = await receive()
                if message["type"] != "http.request":
                    break
                chunks.append(message.get("body", b""))
                size += len(chunks[-1])
                more = message.get("more_body", False)
            body = b"".join(chunks)
            messages = [{"type": "http.request", "body": body, "more_body": False}]
            if more:
                # Too large to be a discover probe: pass on what was read, then the rest.
                messages = [{"type": "http.request", "body": body, "more_body": True}]
            replay = iter(messages)

            async def receive_again():
                if replay is not None:
                    nxt = next(replay, None)
                    if nxt is not None:
                        return nxt
                return await receive()

            downstream = receive_again
        else:
            downstream = receive

        if header_modern or (need_body and _body_looks_modern(body)):
            payload = json.dumps(
                rejection(self.mode, _requested_version(scope, body), _first_id(body))
            ).encode("utf-8")
            await send({
                "type": "http.response.start",
                "status": 400,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(payload)).encode("ascii")),
                ],
            })
            await send({"type": "http.response.body", "body": payload})
            return
        await self.app(scope, downstream, send)
