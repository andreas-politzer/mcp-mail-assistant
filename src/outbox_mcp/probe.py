"""Diagnostic probe: the HTTP server offers one harmless tool and nothing else.

For finding out why a client does not offer the connector (voice mode of claude.ai). Switched on
with OUTBOX_PROBE (HTTP server only), off by default:

  ping       only the tool `ping` (read-only, no arguments), the same address and sign-in as always
  ping-root  like `ping`, but the tool endpoint sits at "/" and sign-in asks for no special scope
  matrix     `ping` by default; with ?v=b|c|d|e the real tool list in four variants, answered by a
             dummy (see probe_matrix.py)

In `ping` and `ping-root` NO mail tool is registered; in `matrix` the tools only talk to a dummy. Nothing can be read, prepared or sent, whatever a
client asks for. That is also why the second mode may drop the scope: the only thing a token
reaches is `ping`. Remove the setting and the server is back to normal.
"""

from __future__ import annotations

from typing import Optional

from mcp.server.caching import CacheHint
from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from . import __version__

MODE_PING = "ping"
MODE_PING_ROOT = "ping-root"
MODE_MATRIX = "matrix"
_MODES = {MODE_PING, MODE_PING_ROOT, MODE_MATRIX}

PROBE_OPTIONS = {
    "title": "Probe",
    "version": __version__,
    "subscriptions": False,
    "cache_hints": {
        "server/discover": CacheHint(ttl_ms=300_000, scope="private"),
        "tools/list": CacheHint(ttl_ms=300_000, scope="private"),
    },
    "instructions": "Answers a ping. Nothing else.",
}


def mode_from_env(value: Optional[str]) -> Optional[str]:
    """The probe mode named by OUTBOX_PROBE, or None when it is off or not recognised."""
    text = (value or "").strip().lower()
    return text if text in _MODES else None


def register_probe_tools(mcp: MCPServer) -> None:
    @mcp.tool(annotations=ToolAnnotations(title="Ping", readOnlyHint=True, openWorldHint=False))
    async def ping() -> dict:
        """Answer with pong. Takes no arguments."""
        return {"pong": True}
