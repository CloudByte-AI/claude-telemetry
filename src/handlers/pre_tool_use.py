"""
Claude Code PreToolUse adapter.

    stdin JSON -> ToolCall (normalise)   ...engine...   Decision -> stdout JSON (render)

`normalise()` and `render()` are pure, so the HTTP transport
(src/app/routes/guardrails.py) and the tests reuse them unchanged.

Claude Code lets the tool call proceed when its hook errors (exit 1, a crash,
a timeout, an HTTP hook it cannot reach). So every answer, a failed check
included, is an explicit decision on stdout with exit 0: when the engine fails,
decide_payload() answers with engine.fallback_decision() - deny for the
profile's `deny` literals, otherwise ask.
"""

from __future__ import annotations

import json
import sys

from src.guardrails.decision import ASK, DENY, MESSAGE_PREFIX, Decision
from src.guardrails.hook_input import HookInputError, read_stdin_payload
from src.guardrails.toolcall import (
    KIND_AGENT,
    KIND_EDIT,
    KIND_MCP,
    KIND_OTHER,
    KIND_READ,
    KIND_SEARCH,
    KIND_SHELL,
    KIND_WEB,
    KIND_WRITE,
    ToolCall,
    split_mcp_tool_name,
)

HOOK_EVENT = "PreToolUse"
PLATFORM = "claude_code"

# Claude Code tool name -> operation kind. The subagent tool is `Agent` (not
# `Task`). `PowerShell` is the second shell tool (on by default on Windows) and
# takes the same `tool_input.command` as Bash. A stale map is a silent coverage
# gap, so test_adapters_claude.py fails on an unknown tool name.
TOOL_KINDS: dict[str, str] = {
    "Bash": KIND_SHELL,
    "PowerShell": KIND_SHELL,
    "Read": KIND_READ,
    "Write": KIND_WRITE,
    "Edit": KIND_EDIT,
    "NotebookEdit": KIND_EDIT,
    "Glob": KIND_SEARCH,
    "Grep": KIND_SEARCH,
    "WebFetch": KIND_WEB,
    "WebSearch": KIND_WEB,
    "Agent": KIND_AGENT,
}

# Tool inputs that name a file, in the order they should be read.
_PATH_FIELDS = ("file_path", "notebook_path", "path")


def kind_for(tool_name: str) -> str:
    """Operation kind for a Claude Code tool name."""
    if not tool_name:
        return KIND_OTHER
    if tool_name.startswith("mcp__"):
        return KIND_MCP
    return TOOL_KINDS.get(tool_name, KIND_OTHER)


def normalise(payload: dict) -> ToolCall:
    """
    Claude Code's PreToolUse payload -> ToolCall. Pure; never raises.

    Bash runs a POSIX shell on every platform (Git Bash on Windows);
    `sniff_dialect` only upgrades to PowerShell for text that could not be
    POSIX. The PowerShell tool's dialect comes from its name.
    """
    from src.guardrails.parser import POSIX, POWERSHELL, sniff_dialect

    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        tool_input = {}

    tool_name = str(payload.get("tool_name") or "")
    kind = kind_for(tool_name)
    mcp_server, mcp_tool = split_mcp_tool_name(tool_name)

    command = tool_input.get("command") or ""
    if tool_name == "PowerShell":
        dialect = POWERSHELL
    else:
        dialect = sniff_dialect(command, POSIX) if command else POSIX

    paths = [tool_input[field] for field in _PATH_FIELDS if tool_input.get(field)]

    urls: list[str] = []
    if tool_input.get("url"):
        urls.append(str(tool_input["url"]))

    return ToolCall.build(
        platform=PLATFORM,
        tool_name=tool_name,
        kind=kind,
        hook_event=HOOK_EVENT,
        raw_input=tool_input,
        cwd=payload.get("cwd"),
        session_id=payload.get("session_id"),
        # Claude's own prompt id (Claude Code 2.1.196+), stored as
        # USER_PROMPT.jsonl_prompt_id.
        prompt_id=payload.get("prompt_id"),
        tool_use_id=payload.get("tool_use_id"),
        permission_mode=str(payload.get("permission_mode") or "default"),
        agent_id=payload.get("agent_id"),
        agent_type=payload.get("agent_type"),
        command=str(command),
        dialect=dialect,
        file_paths=tuple(str(p) for p in paths),
        content=tool_input.get("content"),
        mcp_server=mcp_server,
        mcp_tool=mcp_tool,
        urls=tuple(urls),
    )


def render(decision: Decision) -> dict:
    """
    Decision -> Claude Code's hookSpecificOutput. Pure.

    `{}` for anything that is not ask or deny: emitting `allow` would skip the
    user's own permission prompt. An `ask` reason is shown to the user, a `deny`
    reason to Claude, so shipped deny reasons read as instructions to the agent.
    """
    if not decision.is_opinion:
        return {}

    return {
        "hookSpecificOutput": {
            "hookEventName": HOOK_EVENT,
            "permissionDecision": decision.action,
            "permissionDecisionReason": decision.message,
        }
    }


def evaluate_payload(payload: dict) -> tuple[dict, Decision]:
    """Full path from payload to rendered output. Raises if the engine does."""
    from src.guardrails.engine import evaluate

    call = normalise(payload)
    decision = evaluate(call)
    return render(decision), decision


def decide_payload(payload: dict) -> tuple[dict, Decision]:
    """
    evaluate_payload(), failing closed. Never raises; used by both transports.

    When the check raises, the answer is engine.fallback_decision(), or
    Decision.evaluation_failed() if even the engine cannot be imported.
    """
    try:
        return evaluate_payload(payload)
    except Exception as exc:
        _log_error(f"guardrails: evaluation failed, failing closed: {exc}")
    try:
        from src.guardrails.engine import fallback_decision
        decision = fallback_decision(_failure_text(payload))
    except Exception:
        decision = Decision.evaluation_failed()
    return render(decision), decision


def _failure_text(payload: dict) -> str:
    """What the call acts on, as one string, for the fallback deny check. Never raises."""
    try:
        tool_input = payload.get("tool_input") or {}
        return " ".join(
            str(value) for value in tool_input.values() if isinstance(value, (str, int))
        )
    except Exception:
        return ""


def _log_error(message: str) -> None:
    try:
        from src.common.logging import get_logger
        get_logger(__name__).error(message, exc_info=True)
    except Exception:
        pass


def handle_pre_tool_use() -> int:
    """The command-hook entry point. Returns the process exit code."""
    from src.common.logging import get_logger

    logger = get_logger(__name__)

    try:
        payload = read_stdin_payload()
    except HookInputError as exc:
        logger.warning(f"guardrails: unreadable hook input, no opinion: {exc}")
        print(f"{MESSAGE_PREFIX}: could not read the hook input, so this call was "
              f"not checked - {exc}", file=sys.stderr)
        print("{}")
        return 0

    output, decision = decide_payload(payload)

    # stdout carries the decision and nothing else - a stray print anywhere in
    # the engine would be parsed as a hook decision.
    print(json.dumps(output))
    _record(decision, payload, logger)
    return 0


def _record(decision: Decision, payload: dict, logger) -> None:
    """
    Audit and notify, once the answer is on stdout. Never raises; the
    notification is never waited for.
    """
    if not (decision.should_audit or decision.is_opinion):
        return
    try:
        call = normalise(payload)
    except Exception as exc:
        logger.debug(f"guardrails: could not rebuild the call to record it: {exc}")
        return

    if decision.should_audit:
        try:
            from src.guardrails.spool import append_event
            append_event(decision, call)
        except Exception as exc:
            logger.debug(f"guardrails: audit spool write failed (non-fatal): {exc}")

    if decision.is_opinion:
        try:
            sys.stdout.flush()
            from src.guardrails.notify import notify_decision
            notify_decision(decision, call)
        except Exception as exc:
            logger.debug(f"guardrails: notification failed (non-fatal): {exc}")
