"""
Per-turn completion status, read from the Claude Code transcript.

USER_PROMPT.status records how a turn ended: NULL for a normal turn,
'tool_use' when the user denied a tool call, 'request' when the user
interrupted the whole request.

This is the one prompt-level field that is deliberately NOT sourced from hooks.
A real capture of a denied tool call showed the hook stream carries only
PreToolUse and PermissionRequest and then goes silent - there is no
"user denied" event, no PostToolUse, no PostToolUseFailure and no Stop. The
denial is therefore observable only as an *absence*, which cannot be seen at
the time it happens and would have to be inferred later at the next prompt or
at SessionEnd, where the 1.5s budget makes the work unreliable. The transcript
states the same fact explicitly and distinguishes the two cases, so it stays
the source of truth here. See PostToolUseFailure for genuine tool errors and
StopFailure for API errors - both are different signals, not this one.

The functions here are pure: they take already-parsed transcript events and
return data. Nothing touches the database, so callers decide when to persist
and the detection can be unit-tested on fixtures alone.
"""

from typing import Any, Dict, List

from src.common.logging import get_logger


logger = get_logger(__name__)


# Synthetic marker text Claude Code writes into the transcript, mapped to the
# status value stored in USER_PROMPT.
INTERRUPT_TEXT_TO_REASON = {
    "[Request interrupted by user for tool use]": "tool_use",
    "[Request interrupted by user]": "request",
}

INTERRUPT_TEXTS = frozenset(INTERRUPT_TEXT_TO_REASON)

# Set as the top-level toolUseResult on the tool_result record when a tool call
# is rejected. Verified on a real denial: the marker record above and this
# record are two separate events that share one promptId.
TOOL_REJECTED_RESULT = "User rejected tool use"


def detect_interrupt_status(events: List[Dict[str, Any]]) -> Dict[str, str]:
    """
    Return {prompt_id: status} for every interrupted turn in these events.

    status values:
      'tool_use' - the user denied a tool call
      'request'  - the user interrupted the whole request

    Turns that completed normally are simply absent from the result, so a
    caller can treat "not in this dict" as "no status to record" rather than
    having to distinguish a normal turn from an undetected one.

    Both signals are checked because they appear on different records:
      - the marker text arrives as a user record whose message.content is a
        list containing a text block
      - toolUseResult arrives at the top level of the tool_result record

    A denial produces both; a plain interrupt produces only the marker. When
    both are seen for one prompt the result is the same value anyway.

    Measured against every transcript on disk at the time of writing, this
    matched 8 of 8 interrupted turns that had a USER_PROMPT row, with no false
    negatives.
    """
    result: Dict[str, str] = {}

    for event in events:
        if event.get("type") != "user":
            continue

        prompt_id = event.get("promptId")
        if not prompt_id:
            continue

        # Signal 1 - synthetic marker text in the message content.
        content = (event.get("message", {}) or {}).get("content")
        if isinstance(content, list):
            for item in content:
                if not isinstance(item, dict) or item.get("type") != "text":
                    continue
                reason = INTERRUPT_TEXT_TO_REASON.get(item.get("text", "").strip())
                if reason:
                    result[prompt_id] = reason
        elif isinstance(content, str):
            # Not observed in practice - every marker on disk is list-shaped -
            # but cheap to accept in case the transcript format shifts.
            reason = INTERRUPT_TEXT_TO_REASON.get(content.strip())
            if reason:
                result[prompt_id] = reason

        # Signal 2 - explicit rejection on the tool_result record.
        if event.get("toolUseResult") == TOOL_REJECTED_RESULT:
            result[prompt_id] = "tool_use"

    if result:
        logger.debug(f"Detected interrupt status for {len(result)} turn(s): {result}")

    return result
