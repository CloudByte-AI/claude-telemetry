"""
Spool -> TOOL_GUARDRAIL_EVENT drain, `user_decision` resolution and tool-call linking.

Drained by hooks that have the database open and are not in front of a tool
call: Claude Code Stop, UserPromptSubmit and SessionEnd; Cursor stop and
sessionEnd. Stop alone is not enough, since Claude Code skips it for a turn a
rejected call interrupted. The drain is idempotent (INSERT OR IGNORE on
event_id) and deletes only the files it wrote, so concurrent drains are safe.

`user_decision` is read from the TOOL table afterwards:

    deny                                      -> auto_denied (nobody was asked)
    ask the platform could not show (Cursor)  -> not_prompted (it ran unreviewed)
    ask, TOOL row whose output is Claude
         Code's rejection message             -> rejected
    ask, any other TOOL row                   -> approved (the tool ran)
    ask, no TOOL row, turn over               -> rejected (it never ran)
    ask, no TOOL row, turn still running      -> NULL, re-checked on every drain

Cursor's before* hooks carry no tool_use_id, so each such event is linked to the
earliest TOOL row of the same prompt with the same tool_input_hash, recorded
after the event and not claimed by another event of its kind, oldest event
first. Two kinds never mix, because Cursor may give a call and a denied call
after it one id:

    deny                       only the record of a call that was blocked
    anything else (it ran)     only the record of a call that ran

A platform that cannot tell a blocked call's record apart (no `blocked_records`
entry) never links its denies. Known limit: two identical parallel calls that
finish in the opposite order swap output and duration.

The caller passes the TOOL-row functions (`tool_hashers`, `blocked_records`),
because this package may not import a platform adapter. A Cursor event drained
by a Claude Code hook is linked by the next Cursor drain.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

from src.common.logging import get_logger
from src.guardrails import spool

logger = get_logger(__name__)

# (TOOL.tool_name, TOOL.input_json decoded once) -> the tool_input_hash that
# call was spooled with, or None.
ToolHasher = Callable[[str, Any], str | None]

# TOOL.output_json as stored -> whether the row records a call that was blocked
# before it ran.
BlockedRecord = Callable[[str | None], bool]

APPROVED = "approved"
REJECTED = "rejected"
AUTO_DENIED = "auto_denied"
NOT_PROMPTED = "not_prompted"

# The tool_result Claude Code records when the developer declines a permission
# prompt. Matched as a PREFIX of the tool's output, never as a substring, so a
# file that merely quotes this sentence does not read as a rejection.
CLAUDE_REJECTION_PREFIX = "The user doesn't want to proceed with this tool use"

# Pending asks re-checked per drain. Newest first; an ask that can never be
# resolved (no prompt id, a session that never ended) must not grow the cost
# of every future drain.
_PENDING_LIMIT = 200

# Cursor events re-checked per drain for a TOOL row to link to (a drain can see
# an event before its tool has finished). Bounded by count and age so events
# that never link stop costing anything.
_LINK_LIMIT = 200
_LINK_MAX_AGE = timedelta(days=1)

# Evidence carries text lifted from the tool call, so it is masked before it
# reaches the database. Done here, not on the hot path, because the masker
# pulls in the whole detector library.
_MASK_EVIDENCE = True

_INSERT = """
INSERT OR IGNORE INTO TOOL_GUARDRAIL_EVENT (
    event_id, session_id, prompt_id, tool_use_id, tool_name, platform,
    operation, rule_id, rule_rank, reason, profile_hash, matcher_id, target,
    evidence, command_name, tool_input_hash, action,
    alert_level, decision_source,
    user_decision, eval_ms, timestamp,
    policy_scope, workspace_root
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


def _mask(text: str | None) -> str | None:
    if not text or not _MASK_EVIDENCE:
        return text
    try:
        from src.security.config import ScanConfig
        from src.security.masker import mask_text
        from src.security.scanner import scan_text

        result = scan_text(text, ScanConfig(categories={}))
        if not result.findings:
            return text
        return mask_text(text, result.findings)
    except Exception as exc:
        logger.debug(f"guardrails: evidence masking unavailable, storing as-is: {exc}")
        return text


def drain(conn=None, tool_hashers: Mapping[str, ToolHasher] | None = None,
          blocked_records: Mapping[str, BlockedRecord] | None = None) -> int:
    """
    Write every spooled event to TOOL_GUARDRAIL_EVENT, link events per
    `tool_hashers` (platform -> TOOL-row hasher) and `blocked_records`
    (platform -> blocked-call test), then resolve pending asks.

    Returns the number of events newly written (duplicates not counted). Never
    raises: a failed drain leaves the spool for the next hook to retry. A spool
    file is deleted only once its row is committed, and only files read at the
    start are candidates, so an event written mid-drain is never touched.
    """
    batch = spool.read_batch()
    spool.remove_unreadable()

    try:
        from src.db.manager import get_db_connection

        connection = conn or get_db_connection()
        cursor = connection.cursor()

        written = 0
        done: set = set()
        for path, row in batch:
            try:
                cursor.execute(_INSERT, _values(row, resolve_user_decision(cursor, row)))
                if cursor.rowcount:
                    written += 1
                elif not _stored(cursor, row.get("event_id")):
                    # OR IGNORE also skips a row that breaks a constraint. It
                    # can never be stored, so it is discarded, not retried.
                    logger.warning(
                        f"guardrails: audit event {row.get('event_id')!r} is malformed "
                        f"and cannot be stored; discarded"
                    )
                done.add(path)
            except Exception as exc:
                logger.warning(
                    f"guardrails: could not write audit event {row.get('event_id')!r}, "
                    f"kept for retry: {exc}"
                )

        linked = sum(
            link_tool_calls(cursor, platform, tool_hash, (blocked_records or {}).get(platform))
            for platform, tool_hash in (tool_hashers or {}).items()
        )
        resolved = resolve_pending(cursor)
        connection.commit()

        # After the commit, not before: a crash in between only makes the next
        # drain repeat an insert, which INSERT OR IGNORE absorbs.
        spool.remove(done)

        if written or resolved or linked:
            logger.info(
                f"guardrails: drained {written} audit event(s), resolved {resolved} pending, "
                f"linked {linked} to their tool call"
            )
        return written

    except Exception as exc:
        logger.warning(f"guardrails: audit drain failed, spool kept for retry: {exc}")
        return 0


def _stored(cursor, event_id) -> bool:
    """Whether this event is already in the table - an ignored duplicate, not a loss."""
    cursor.execute("SELECT 1 FROM TOOL_GUARDRAIL_EVENT WHERE event_id = ? LIMIT 1", (event_id,))
    return cursor.fetchone() is not None


def _values(row: dict, user_decision: str | None) -> tuple:
    return (
        row.get("event_id"),
        row.get("session_id"),
        row.get("prompt_id"),
        row.get("tool_use_id"),
        row.get("tool_name"),
        row.get("platform"),
        row.get("operation"),
        row.get("rule_id"),
        row.get("rule_rank"),
        row.get("reason"),
        row.get("profile_hash"),
        row.get("matcher_id"),
        row.get("target"),
        _mask(row.get("evidence")),
        row.get("command_name"),
        row.get("tool_input_hash"),
        row.get("action"),
        row.get("alert_level"),
        row.get("decision_source"),
        user_decision,
        row.get("eval_ms"),
        row.get("timestamp"),
        # Spool files written before v4 have neither key: NULL, as for old rows.
        row.get("policy_scope"),
        row.get("workspace_root"),
    )


def resolve_user_decision(cursor, row: dict) -> str | None:
    """
    approved | rejected | auto_denied | not_prompted | None. Never raises.

    None means either "not applicable" (an allow) or "not known yet" (an ask
    whose turn is still running).
    """
    action = row.get("action")
    if action == "deny":
        return AUTO_DENIED
    if action != "ask":
        return None
    # A missing key means prompted: resolve_pending() passes table rows, which
    # have no `prompted` - an unprompted ask was stored as not_prompted already.
    if row.get("prompted", True) is False:
        return NOT_PROMPTED

    tool_use_id = row.get("tool_use_id")
    if not tool_use_id:
        return None

    try:
        found, output = _tool_output(cursor, tool_use_id)
        if found:
            return REJECTED if is_rejection(output) else APPROVED
        if _turn_is_over(cursor, row):
            return REJECTED
    except Exception as exc:
        logger.debug(f"guardrails: could not resolve user_decision for {tool_use_id!r}: {exc}")
    return None


def resolve_pending(cursor) -> int:
    """
    Resolve asks written earlier with user_decision NULL, now that later hooks
    may have recorded what happened. Returns how many were resolved. Never raises.
    """
    try:
        cursor.execute(
            "SELECT event_id, action, tool_use_id, session_id, prompt_id "
            "FROM TOOL_GUARDRAIL_EVENT "
            "WHERE action = 'ask' AND user_decision IS NULL AND tool_use_id IS NOT NULL "
            "ORDER BY timestamp DESC LIMIT ?",
            (_PENDING_LIMIT,),
        )
        pending = [
            dict(zip(("event_id", "action", "tool_use_id", "session_id", "prompt_id"), found))
            for found in cursor.fetchall()
        ]
    except Exception as exc:
        logger.debug(f"guardrails: pending lookup failed: {exc}")
        return 0

    resolved = 0
    for row in pending:
        decision = resolve_user_decision(cursor, row)
        if decision is None:
            continue
        try:
            cursor.execute(
                "UPDATE TOOL_GUARDRAIL_EVENT SET user_decision = ? "
                "WHERE event_id = ? AND user_decision IS NULL",
                (decision, row["event_id"]),
            )
            resolved += cursor.rowcount or 0
        except Exception as exc:
            logger.debug(f"guardrails: could not resolve {row['event_id']!r}: {exc}")
    return resolved


def link_tool_calls(cursor, platform: str, tool_hash: ToolHasher,
                    is_blocked: BlockedRecord | None = None) -> int:
    """
    Fill tool_use_id on this platform's events from the TOOL row each matches:
    the earliest unclaimed one of its kind after it, with the same prompt and
    subject. Denies are linked only when `is_blocked` can recognise a blocked
    call's record. Returns how many were linked. Never raises.
    """
    try:
        cutoff = (datetime.now(timezone.utc) - _LINK_MAX_AGE).isoformat()
        # Without a blocked-call test denies cannot be linked, so they are left
        # out rather than taking places in the window.
        only_ran = "" if is_blocked is not None else "AND action != 'deny' "
        cursor.execute(
            "SELECT event_id, prompt_id, tool_input_hash, timestamp, action FROM TOOL_GUARDRAIL_EVENT "
            "WHERE platform = ? AND tool_use_id IS NULL " + only_ran +
            "AND prompt_id IS NOT NULL AND tool_input_hash IS NOT NULL AND timestamp >= ? "
            "ORDER BY timestamp DESC LIMIT ?",
            (platform, cutoff, _LINK_LIMIT),
        )
        # The newest events, matched oldest first so identical calls pair in order.
        unlinked = sorted(cursor.fetchall(), key=lambda row: _when(row[3]) or _EPOCH)
    except Exception as exc:
        logger.debug(f"guardrails: unlinked event lookup failed: {exc}")
        return 0

    linked = 0
    for event_id, prompt_id, subject_hash, event_time, action in unlinked:
        blocked = action == "deny"
        try:
            match = _earliest_match(cursor, prompt_id, subject_hash, _when(event_time), tool_hash,
                                    blocked, is_blocked)
            if match is None:
                continue
            cursor.execute(
                "UPDATE TOOL_GUARDRAIL_EVENT SET tool_use_id = ? "
                "WHERE event_id = ? AND tool_use_id IS NULL",
                (match, event_id),
            )
            linked += cursor.rowcount or 0
        except Exception as exc:
            logger.debug(f"guardrails: could not link {event_id!r} to its tool call: {exc}")
    return linked


_EPOCH = datetime.min.replace(tzinfo=timezone.utc)


def _earliest_match(cursor, prompt_id: str, subject_hash: str, after: datetime | None,
                    tool_hash: ToolHasher, blocked: bool = False,
                    is_blocked: BlockedRecord | None = None) -> str | None:
    """
    The earliest TOOL row of this prompt with this subject, recorded after the
    event, of the event's kind and not claimed by another event of that kind.
    A deny matches only a blocked call's record and anything else only the
    record of a call that ran, so neither kind's id claims the other's row.
    """
    claimant = "g.action = 'deny'" if blocked else "g.action != 'deny'"
    cursor.execute(
        "SELECT tool_id, tool_name, input_json, output_json, timestamp FROM TOOL t "
        "WHERE t.prompt_id = ? AND NOT EXISTS (SELECT 1 FROM TOOL_GUARDRAIL_EVENT g "
        "WHERE g.tool_use_id = t.tool_id AND " + claimant + ")",
        (prompt_id,),
    )
    candidates = []
    for tool_id, tool_name, input_json, output_json, recorded in cursor.fetchall():
        if is_blocked is not None and is_blocked(output_json) != blocked:
            continue
        if tool_hash(tool_name, _stored_input(input_json)) != subject_hash:
            continue
        when = _when(recorded)
        if after is not None and when is not None and when < after:
            continue            # an earlier call's record, not this one's
        candidates.append((when or datetime.max.replace(tzinfo=timezone.utc), tool_id))
    return min(candidates)[1] if candidates else None


def _when(value) -> datetime | None:
    """
    A stored timestamp as an aware datetime, or None. Guardrail events are
    stamped in UTC and Cursor's TOOL rows in local time with an offset
    (`+05:30`), so they are compared as instants, never as text.
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.astimezone()


def _stored_input(input_json: str | None):
    """TOOL.input_json decoded once. Cursor rows hold the input as a JSON string inside it."""
    if not input_json:
        return {}
    try:
        return json.loads(input_json)
    except Exception:
        return {}


def is_rejection(output_json: str | None) -> bool:
    """
    Whether a TOOL row's stored output is Claude Code's permission rejection.

    The output is stored JSON-encoded, either as the bare result string or as
    `{"result": "..."}`; both shapes occur.
    """
    return _output_text(output_json).lstrip().startswith(CLAUDE_REJECTION_PREFIX)


def _output_text(output_json: str | None) -> str:
    if not output_json:
        return ""
    try:
        value = json.loads(output_json)
    except Exception:
        return str(output_json)
    if isinstance(value, dict):
        value = value.get("result", value.get("content", ""))
    if isinstance(value, list):
        value = " ".join(
            str(item.get("text", "")) if isinstance(item, dict) else str(item) for item in value
        )
    return value if isinstance(value, str) else ""


def _tool_output(cursor, tool_use_id: str) -> tuple[bool, str | None]:
    """(TOOL row exists, its stored output)."""
    cursor.execute("SELECT output_json FROM TOOL WHERE tool_id = ? LIMIT 1", (tool_use_id,))
    found = cursor.fetchone()
    return (True, found[0]) if found else (False, None)


def _turn_is_over(cursor, row: dict) -> bool:
    """
    Whether the turn that raised this ask has finished, so a tool that has not
    run by now never will: the prompt has a final status, a later prompt exists
    in the same session, or the session has ended.
    """
    session_id = row.get("session_id")
    prompt_id = row.get("prompt_id")

    if prompt_id:
        prompts = _prompt_rows(cursor, prompt_id)
        if any(status for _, status in prompts):
            return True
        if prompts and session_id:
            # Latest copy: the same prompt can appear twice in USER_PROMPT.
            latest = max(rowid for rowid, _ in prompts)
            cursor.execute(
                "SELECT 1 FROM USER_PROMPT WHERE session_id = ? AND rowid > ? LIMIT 1",
                (session_id, latest),
            )
            if cursor.fetchone():
                return True

    if session_id:
        cursor.execute("SELECT ended_at FROM SESSION WHERE session_id = ? LIMIT 1", (session_id,))
        session = cursor.fetchone()
        if session and session[0]:
            return True

    return False


def _prompt_rows(cursor, prompt_id: str) -> list:
    """
    (rowid, status) of the USER_PROMPT rows for the prompt id an event carries.

    Claude Code's prompt_id is USER_PROMPT.jsonl_prompt_id, while Cursor's
    generation_id IS USER_PROMPT.prompt_id. Both columns are indexed.
    """
    cursor.execute(
        "SELECT rowid, status FROM USER_PROMPT WHERE prompt_id = ? OR jsonl_prompt_id = ?",
        (prompt_id, prompt_id),
    )
    return cursor.fetchall()
