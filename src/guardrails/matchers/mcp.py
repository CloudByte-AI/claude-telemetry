"""
MCP matchers.

An MCP tool is opaque, so these match on the verb in the tool name, which is a
convention rather than a guarantee - `create_issue` writes, `get_file` reads.
Reported as a fact (`verb`) so a rule author can see why it fired.

`mcp.ungoverned` ("not on the allowlist") is deliberately NOT a matcher: a
matcher sees only the ToolCall, never the profile. An allowlist is written as
rules, narrower allow first:

    - { mcp_tool: {equals: read_file},  decision: allow }
    - { kind: mcp,                      decision: ask, reason: "MCP tool not allow-listed" }
"""

from __future__ import annotations

from src.guardrails.matchers.base import BaseMatcher, MatchResult
from src.guardrails.registry import register_matcher
from src.guardrails.toolcall import KIND_MCP, ToolCall

_WRITE_VERBS = (
    "create", "update", "write", "set", "add", "post", "put", "patch",
    "insert", "upload", "send", "publish", "merge", "push", "edit", "modify",
)
_DELETE_VERBS = ("delete", "remove", "destroy", "drop", "purge", "truncate", "revoke")


def _verb_in(tool: str | None, verbs: tuple[str, ...]) -> str | None:
    """
    The verb found in a tool name, matched on whole name segments so that
    `undelete_file` does not read as a delete.
    """
    if not tool:
        return None
    segments = tool.replace("-", "_").lower().split("_")
    for verb in verbs:
        if verb in segments:
            return verb
    return None


@register_matcher
class McpWriteMatcher(BaseMatcher):
    OPERATION = "mcp.write"
    DOMAIN = "mcp"
    DESCRIPTION = "MCP tool whose name indicates it writes or sends data"
    APPLIES_TO = (KIND_MCP,)
    DEFAULT_ACTION = "allow"
    DEFAULT_ALERT = "info"
    FACTS = ("verb", "mcp_server")

    def match(self, call: ToolCall) -> MatchResult | None:
        verb = _verb_in(call.mcp_tool, _WRITE_VERBS)
        if not verb:
            return None
        return self.hit(
            target=call.tool_name,
            evidence=call.tool_name,
            verb=verb,
            mcp_server=call.mcp_server,
        )


@register_matcher
class McpDeleteMatcher(BaseMatcher):
    OPERATION = "mcp.delete"
    DOMAIN = "mcp"
    DESCRIPTION = "MCP tool whose name indicates it deletes data"
    APPLIES_TO = (KIND_MCP,)
    DEFAULT_ACTION = "ask"
    DEFAULT_ALERT = "warn"
    FACTS = ("verb", "mcp_server")

    def match(self, call: ToolCall) -> MatchResult | None:
        verb = _verb_in(call.mcp_tool, _DELETE_VERBS)
        if not verb:
            return None
        return self.hit(
            target=call.tool_name,
            evidence=call.tool_name,
            verb=verb,
            mcp_server=call.mcp_server,
        )
