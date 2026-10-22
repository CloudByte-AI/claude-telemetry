"""
Claude Code PostToolUse adapter - proof that an asked-about call ran.

Claude Code fires PostToolUse only after a tool has run, so for a call our
PreToolUse hook asked about it is the proof the user approved it. This hook
marks that ask as having run, for its audit row's user_decision
(src/guardrails/asks.py), and turns the note PreToolUse left for a remembered
approval into an approval window (src/guardrails/approvals.py). Every other
call - nearly all of them - is a couple of failed file lookups.

Registered for the same tools as PreToolUse. Prints nothing (Claude Code reads
no output from it here) and always exits 0: nothing here may affect the tool
call or the turn.
"""

from __future__ import annotations

import json
import sys

HOOK_EVENT = "PostToolUse"


def handle_post_tool_use() -> int:
    """The command-hook entry point. Returns the process exit code."""
    try:
        raw = sys.stdin.buffer.read()
        payload = json.loads(raw.decode("utf-8-sig") or "{}") if raw else {}
    except Exception:
        return 0
    if not isinstance(payload, dict):
        return 0
    try:
        from src.guardrails.asks import note_ran
        note_ran(payload.get("tool_use_id"))
    except Exception:
        pass
    try:
        from src.guardrails.approvals import grant_from_pending
        grant_from_pending(payload.get("tool_use_id"), payload.get("session_id"))
    except Exception:
        pass
    return 0
