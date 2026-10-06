"""
How a failed Cursor tool call is stored in TOOL.output_json.

Cursor's postToolUseFailure reports a call that failed, timed out or was denied
(`failure_type`: error | timeout | permission_denied). The TOOL row keeps that
type, so a reader can tell a call that ran and failed from one that was blocked
and never ran. Stdlib only: the guardrails drain imports it too.
"""

from __future__ import annotations

import json

FAILURE_TYPE = "failure_type"
# Blocked by a hook or a permission rule before it ran.
PERMISSION_DENIED = "permission_denied"


def failure_output(payload: dict) -> str:
    """The TOOL.output_json value for a postToolUseFailure payload."""
    return json.dumps({
        FAILURE_TYPE: payload.get("failure_type"),
        "error": payload.get("error_message"),
        "is_interrupt": payload.get("is_interrupt"),
    })


def is_blocked_output(output_json) -> bool:
    """
    Whether a stored TOOL.output_json records a call that was blocked before it
    ran. The writer JSON-encodes the value once more, so it is decoded until it
    is no longer text. Never raises.
    """
    value = output_json
    try:
        for _ in range(3):
            if not isinstance(value, str):
                break
            value = json.loads(value)
    except Exception:
        return False
    return isinstance(value, dict) and value.get(FAILURE_TYPE) == PERMISSION_DENIED
