"""
Finding the USER_PROMPT row of a transcript prompt, and storing the tool calls of turns that
are not prompts (a background agent or task reporting back).

Both the Stop hook (main.stop) and the missed-pair recovery (core.recovery) turn a transcript
prompt into its USER_PROMPT row before writing the turn's response, tools and tokens. They use
find_db_prompt():

  1. the session's row already bound to this transcript prompt (jsonl_prompt_id). The
     UserPromptSubmit hook stores Claude Code's prompt id there when the hook receives it
     (2.1.196+), and an earlier Stop stores it after a text match;
  2. else a normalised exact text match, then a substring match (both sides longer than 20
     characters, so "ok" or "yes" never match a longer prompt), among the session's rows with no
     response yet AND no transcript prompt id yet. A row bound to another transcript prompt is
     never taken: that was how one prompt got two rows, or a turn landed on another prompt's row.

When neither finds a row the caller creates one, as before.

Some turns are not prompts the user typed: Claude Code itself adds them, with their own
promptId, and Claude may call tools while handling them. Seen so far:
  - a task notification: a "user" record starting with <task-notification> (a background agent
    or task finishing);
  - a meta record (isMeta): newer Claude Code versions deliver a background agent's result as
    "Another Claude session sent a message: <agent-message ...>", sometimes after a
    system-reminder in the same turn.
The tool calls of such a turn belong to the prompt whose work it continues, taken as the
session's latest stored prompt at or before the turn. write_non_prompt_tools() stores those calls
(tool, tool tokens, thinking) under that prompt, only the ones not stored yet, so a re-run
changes nothing.
"""

import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.common.time_utils import to_ist
from src.common.logging import get_logger

logger = get_logger(__name__)

FUZZY_MIN_CHARS = 20


def norm_text(text: Optional[str]) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def find_db_prompt(cursor, session_id: str, transcript_prompt_id: Optional[str],
                   prompt_text: Optional[str]) -> Optional[str]:
    """The USER_PROMPT row (prompt_id) of one transcript prompt in a session, or None."""
    if transcript_prompt_id:
        cursor.execute(
            "SELECT prompt_id FROM USER_PROMPT WHERE session_id = ? AND jsonl_prompt_id = ? "
            "ORDER BY timestamp, rowid LIMIT 1",
            (session_id, transcript_prompt_id),
        )
        row = cursor.fetchone()
        if row:
            return row[0]

    cursor.execute(
        """
        SELECT p.prompt_id, p.prompt FROM USER_PROMPT p
        LEFT JOIN RESPONSE r ON r.prompt_id = p.prompt_id
        WHERE p.session_id = ? AND r.message_id IS NULL AND p.jsonl_prompt_id IS NULL
        ORDER BY p.timestamp DESC LIMIT 30
        """,
        (session_id,),
    )
    candidates = cursor.fetchall()
    want = norm_text(prompt_text)
    for cand_id, cand_text in candidates:
        if norm_text(cand_text) == want:
            return cand_id
    if len(want) > FUZZY_MIN_CHARS:
        for cand_id, cand_text in candidates:
            have = norm_text(cand_text)
            if len(have) > FUZZY_MIN_CHARS and (want in have or have in want):
                return cand_id
    return None


def _parse(ts: Any) -> Optional[datetime]:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None


def prompt_at(cursor, session_id: str, at: Any) -> Optional[tuple]:
    """(prompt_id, timestamp) of the session's latest stored prompt at or before `at` (a transcript
    timestamp), or None."""
    when = _parse(to_ist(at))
    if when is None:
        return None
    cursor.execute("SELECT prompt_id, timestamp FROM USER_PROMPT WHERE session_id = ?", (session_id,))
    best = None
    for pid, ts in cursor.fetchall():
        t = _parse(ts)
        if t is None or t > when:
            continue
        if best is None or t >= best[1]:
            best = (pid, t, ts)
    return (best[0], best[2]) if best else None


def write_non_prompt_tools(cursor, writer, session_id: str, groups: List[Dict[str, Any]],
                             skip_tool_names: Optional[set] = None) -> int:
    """Store the tool calls of non-prompt turns under the session's prompt at that time.
    `groups`: non_prompt_tool_groups(). Only calls not in TOOL yet are written. Returns how many."""
    written = 0
    for group in groups:
        tools = group.get("tools") or []
        if not tools:
            continue
        ids = [t["tool_id"] for t in tools]
        placeholders = ",".join("?" * len(ids))
        cursor.execute(f"SELECT tool_id FROM TOOL WHERE tool_id IN ({placeholders})", ids)
        stored = {r[0] for r in cursor.fetchall()}
        new_tools = [t for t in tools if t["tool_id"] not in stored]
        if not new_tools:
            continue
        found = prompt_at(cursor, session_id, group.get("timestamp"))
        if not found:
            logger.info(f"non-prompt turn {group.get('prompt_id')}: no earlier prompt in the session, "
                        f"{len(new_tools)} tool call(s) not stored")
            continue
        prompt_id, prompt_ts = found
        by_tool_tokens = {tt["tool_id"]: tt for tt in group.get("tool_tokens") or []}
        by_thinking = {th["thinking_id"]: th for th in group.get("thinking") or []}
        for tool in new_tools:
            # A tool row carries its prompt's timestamp, like every other tool row.
            if writer.write_tool({**tool, "prompt_id": prompt_id, "timestamp": prompt_ts}):
                if not skip_tool_names or tool.get("tool_name") not in skip_tool_names:
                    written += 1
                tt = by_tool_tokens.get(tool["tool_id"])
                if tt:
                    writer.write_tool_tokens(tt)
                th = by_thinking.get(f"{tool['tool_id']}_thinking")
                if th:
                    writer.write_thinking({**th, "prompt_id": prompt_id, "timestamp": prompt_ts})
        logger.info(f"non-prompt turn {group.get('prompt_id')}: {len(new_tools)} tool call(s) stored "
                    f"under prompt {prompt_id}")
    return written
