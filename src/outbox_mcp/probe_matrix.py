"""Probe matrix: one server, several tool lists, chosen by the address (?v=b).

For finding out which part of the real tool list makes the voice mode of claude.ai skip the
connector. Switched on with OUTBOX_PROBE=matrix (HTTP server only), off by default. Neutral server
metadata (name, title, instructions) is the same as in the plain probe for every variant.

The letter after `v=` picks what tools/list answers:

  (none)   only `ping`  (the existing connector "Probe" keeps working as a control)
  b        the 8 real tools, schemas without "anyOf"/null, no annotations
  c        the 8 real tools, schemas without "anyOf"/null, real annotations
  d        the 8 real tools, original schemas (anyOf/null), no annotations
  e        the 8 real tools, unchanged (original schemas and annotations)

Safety: the tools are registered against a dummy service whose methods only return
{"probe": True}. No mail can be read, prepared or sent, no recipient or file is touched, whatever
a client asks for. Remove OUTBOX_PROBE and the server is back to normal.
"""

from __future__ import annotations

import contextvars
import copy
import re
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs

from mcp.server.mcpserver import MCPServer
from mcp.types import Tool as MCPTool

from .probe import register_probe_tools
from .server import register_tools

VARIANTS = ("b", "c", "d", "e")

_variant: contextvars.ContextVar[str] = contextvars.ContextVar("probe_variant", default="")


class _DummyService:
    """Stands in for OutboxService: every method answers {"probe": True} and does nothing else."""

    approval = None

    def __getattr__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(name)

        def method(*args: Any, **kwargs: Any) -> dict:
            return {"probe": True}

        return method


def plain_schema(schema: Dict[str, Any]) -> Dict[str, Any]:
    """The same input schema without nullable types: anyOf [X, null] becomes X, null defaults go."""
    out = copy.deepcopy(schema)
    for prop in (out.get("properties") or {}).values():
        branches = prop.get("anyOf")
        if branches:
            real = [b for b in branches if b.get("type") != "null"]
            if len(real) == 1:
                del prop["anyOf"]
                prop.update(real[0])
        if "default" in prop and prop["default"] is None:
            del prop["default"]
    return out


def variant_tools(real: List[MCPTool], variant: str) -> List[MCPTool]:
    """The tool list for one variant (unknown variant: the original list)."""
    if variant not in VARIANTS:
        return list(real)
    out = []
    for tool in real:
        copy_ = tool.model_copy(deep=True)
        if variant in ("b", "c"):
            copy_.input_schema = plain_schema(tool.input_schema)
        if variant in ("b", "d"):
            copy_.annotations = None
        out.append(copy_)
    return out


def register_matrix_tools(mcp: MCPServer) -> None:
    """ping plus the real tools (dummy service); tools/list answers per variant."""
    register_probe_tools(mcp)
    register_tools(mcp, _DummyService(), lambda: "probe")  # type: ignore[arg-type]
    original = mcp.list_tools

    async def list_tools() -> List[MCPTool]:
        everything = await original()
        variant = _variant.get()
        if variant not in VARIANTS:
            return [t for t in everything if t.name == "ping"]
        return variant_tools([t for t in everything if t.name != "ping"], variant)

    mcp.list_tools = list_tools  # type: ignore[method-assign]


def variant_from_query(raw: bytes) -> str:
    try:
        values = parse_qs(raw.decode("ascii", "ignore")).get("v", [])
    except ValueError:
        return ""
    value = values[0] if values else ""
    return value if re.fullmatch(r"[a-z0-9]{1,8}", value) else ""


class VariantSelector:
    """ASGI middleware: remembers the ?v= letter for the length of the request."""

    def __init__(self, app, mcp_path: str = "/mcp") -> None:
        self.app = app
        self.mcp_path = mcp_path

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("path") != self.mcp_path:
            await self.app(scope, receive, send)
            return
        token = _variant.set(variant_from_query(scope.get("query_string", b"")))
        try:
            await self.app(scope, receive, send)
        finally:
            _variant.reset(token)
