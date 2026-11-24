"""Optional neutral tool names for the HTTP server (OUTBOX_TOOL_NAMES=neutral), off by default.

Why: in the voice mode of claude.ai a connector disappeared without any message as soon as it
offered a tool with a mail-typical name (found with the probe matrix: a tool list with the names
tool_1..tool_8 was offered, the same tool under its real name was not). This switch lists the tools
as tool_1..tool_8 and calls them by those names; everything else stays as it is.

What changes (and nothing else): the names in the tool list, the annotation titles and generated schema titles (dropped, they only repeat names), and
every place where a tool name is written in text (tool descriptions, server instructions, texts in
answers) is replaced by the new name, so the assistant is never told to call a tool that does not
exist. The real names still work when a client calls them. Arguments, safety checks and the
approval flow are untouched.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError, UnexpectedToolError
from mcp.types import CallToolResult, TextContent
from mcp.types import Tool as MCPTool

ALIASES: Dict[str, str] = {
    "list_recipients": "tool_1",
    "prepare_email": "tool_2",
    "list_attachments": "tool_3",
    "suggest_recipient": "tool_4",
    "list_suggestions": "tool_5",
    "send_email": "tool_6",
    "draft_status": "tool_7",
    "cancel_draft": "tool_8",
}
_REVERSE = {v: k for k, v in ALIASES.items()}
_PATTERN = re.compile(r"\b(" + "|".join(sorted(ALIASES, key=len, reverse=True)) + r")\b")


def mode_from_env(value: Optional[str]) -> bool:
    return (value or "").strip().lower() == "neutral"


def rename_in_text(text: str) -> str:
    return _PATTERN.sub(lambda m: ALIASES[m.group(1)], text)


def _rename_in_value(value: Any) -> Any:
    if isinstance(value, str):
        return rename_in_text(value)
    if isinstance(value, list):
        return [_rename_in_value(v) for v in value]
    if isinstance(value, dict):
        return {k: _rename_in_value(v) for k, v in value.items()}
    return value


def neutral_options(options: dict) -> dict:
    """Server options with tool names in the instructions replaced."""
    out = dict(options)
    if isinstance(out.get("instructions"), str):
        out["instructions"] = rename_in_text(out["instructions"])
    return out


def apply_neutral_names(mcp: MCPServer) -> None:
    """Call after register_tools: list and call tools under the neutral names."""
    original_list = mcp.list_tools
    original_call = mcp.call_tool

    async def list_tools() -> List[MCPTool]:
        out = []
        for tool in await original_list():
            copy_ = tool.model_copy(deep=True)
            copy_.name = ALIASES.get(tool.name, tool.name)
            copy_.title = None
            copy_.description = rename_in_text(tool.description or "") or None
            schema = copy_.input_schema
            schema.pop("title", None)  # generated as "<tool name>Arguments"
            for prop in (schema.get("properties") or {}).values():
                prop.pop("title", None)  # generated from the argument name, carries no information
            if tool.annotations is not None:
                copy_.annotations = tool.annotations.model_copy(update={"title": None})
            out.append(copy_)
        return out

    async def call_tool(name, arguments, context=None):
        try:
            result = await original_call(_REVERSE.get(name, name), arguments, context)
        except UnexpectedToolError:
            raise
        except ToolError as exc:
            # Our own error texts are written for the assistant and may name other tools.
            raise ToolError(rename_in_text(str(exc))) from exc.__cause__
        if not isinstance(result, CallToolResult):
            return result
        content = [
            TextContent(type="text", text=rename_in_text(c.text)) if isinstance(c, TextContent) else c
            for c in result.content
        ]
        structured = result.structured_content
        if structured is not None:
            structured = json.loads(rename_in_text(json.dumps(structured)))
        return result.model_copy(update={"content": content, "structured_content": structured})

    mcp.list_tools = list_tools  # type: ignore[method-assign]
    mcp.call_tool = call_tool  # type: ignore[method-assign]
