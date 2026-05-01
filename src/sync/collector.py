"""
Collects unsent data from the local CloudByte SQLite DB.

Uses a rowid cursor per table stored in ~/.cloudbyte/sync-checkpoint.json
so each sync picks up exactly where the last one left off — no duplicates,
no missed rows.
"""

import json
from pathlib import Path
from typing import Any

from src.common.logging import get_logger
from src.common.paths import get_sync_checkpoint_file
from src.db.manager import get_db_connection

logger = get_logger(__name__)

_CHECKPOINT_DEFAULTS = {
    "last_session_rowid":      0,
    "last_prompt_rowid":       0,
    "last_response_rowid":     0,
    "last_tool_rowid":         0,
    "last_io_tokens_rowid":    0,
    "last_tool_tokens_rowid":  0,
    "last_thinking_rowid":     0,
    "last_observation_rowid":  0,
    "last_summary_rowid":      0,
}


def load_checkpoint() -> dict:
    path = get_sync_checkpoint_file()
    if not path.exists():
        return dict(_CHECKPOINT_DEFAULTS)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        # merge so new keys always have defaults
        return {**_CHECKPOINT_DEFAULTS, **data}
    except Exception:
        return dict(_CHECKPOINT_DEFAULTS)


def save_checkpoint(cp: dict) -> None:
    path = get_sync_checkpoint_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cp, indent=2), encoding="utf-8")


def _rows_as_dicts(cursor) -> list[dict]:
    cols = [d[0] for d in cursor.description]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]


def collect(session_id: str | None = None) -> dict[str, Any]:
    """
    Read all new rows from local DB since the last checkpoint.

    Args:
        session_id: If provided, restrict to rows for this session only
                    (used during Stop hook for instant per-prompt sync).
                    If None, collects across all sessions (used at SessionEnd).

    Returns dict with keys matching the /ingest/telemetry payload schema.
    """
    cp = load_checkpoint()
    conn = get_db_connection()
    c = conn.cursor()

    session_filter = "AND s.session_id = ?" if session_id else ""
    session_args   = (session_id,) if session_id else ()

    # ── Sessions ──────────────────────────────────────────────────────────────
    c.execute(f"""
        SELECT s.rowid AS _rowid, s.session_id, s.project_id, s.cwd, s.created_at,
               p.name AS project_name, p.path AS project_path
        FROM SESSION s
        LEFT JOIN PROJECT p ON p.project_id = s.project_id
        WHERE s.rowid > ? {session_filter}
        ORDER BY s.rowid ASC
    """, (cp["last_session_rowid"], *session_args))
    sessions = _rows_as_dicts(c)

    # ── User prompts ──────────────────────────────────────────────────────────
    prompt_session_filter = "AND session_id = ?" if session_id else ""
    c.execute(f"""
        SELECT rowid AS _rowid, prompt_id, session_id, uuid, parent_uuid, prompt, timestamp
        FROM USER_PROMPT
        WHERE rowid > ? {prompt_session_filter}
        ORDER BY rowid ASC
    """, (cp["last_prompt_rowid"], *session_args))
    prompts = _rows_as_dicts(c)

    # ── Responses ─────────────────────────────────────────────────────────────
    c.execute(f"""
        SELECT r.rowid AS _rowid, r.message_id, r.prompt_id, r.uuid, r.parent_uuid,
               r.response_text, r.model, r.timestamp
        FROM RESPONSE r
        {'JOIN USER_PROMPT up ON up.prompt_id = r.prompt_id AND up.session_id = ?' if session_id else ''}
        WHERE r.rowid > ?
        ORDER BY r.rowid ASC
    """, (*session_args, cp["last_response_rowid"]))
    responses = _rows_as_dicts(c)

    # ── IO tokens (per-message: input, cache_creation, cache_read, output) ────
    c.execute(f"""
        SELECT t.rowid AS _rowid, t.id, t.prompt_id, t.message_id, t.token_type,
               t.input_tokens, t.cache_creation_tokens, t.cache_read_tokens, t.output_tokens
        FROM IO_TOKENS t
        {'JOIN USER_PROMPT up ON up.prompt_id = t.prompt_id AND up.session_id = ?' if session_id else ''}
        WHERE t.rowid > ?
        ORDER BY t.rowid ASC
    """, (*session_args, cp["last_io_tokens_rowid"]))
    io_tokens = _rows_as_dicts(c)

    # ── Tool calls ────────────────────────────────────────────────────────────
    c.execute(f"""
        SELECT t.rowid AS _rowid, t.tool_id, t.prompt_id, t.uuid, t.parent_uuid,
               t.tool_name, t.model, t.input_json, t.output_json, t.timestamp
        FROM TOOL t
        {'JOIN USER_PROMPT up ON up.prompt_id = t.prompt_id AND up.session_id = ?' if session_id else ''}
        WHERE t.rowid > ?
        ORDER BY t.rowid ASC
    """, (*session_args, cp["last_tool_rowid"]))
    tools = _rows_as_dicts(c)

    # ── Tool tokens ───────────────────────────────────────────────────────────
    c.execute(f"""
        SELECT tt.rowid AS _rowid, tt.id, tt.tool_id,
               tt.input_tokens, tt.cache_creation_tokens, tt.cache_read_tokens, tt.output_tokens
        FROM TOOL_TOKENS tt
        {'JOIN TOOL t ON t.tool_id = tt.tool_id JOIN USER_PROMPT up ON up.prompt_id = t.prompt_id AND up.session_id = ?' if session_id else ''}
        WHERE tt.rowid > ?
        ORDER BY tt.rowid ASC
    """, (*session_args, cp["last_tool_tokens_rowid"]))
    tool_tokens = _rows_as_dicts(c)

    # ── Thinking ──────────────────────────────────────────────────────────────
    c.execute(f"""
        SELECT th.rowid AS _rowid, th.thinking_id, th.prompt_id, th.uuid, th.parent_uuid,
               th.content, th.signature, th.timestamp
        FROM THINKING th
        {'JOIN USER_PROMPT up ON up.prompt_id = th.prompt_id AND up.session_id = ?' if session_id else ''}
        WHERE th.rowid > ?
        ORDER BY th.rowid ASC
    """, (*session_args, cp["last_thinking_rowid"]))
    thinking = _rows_as_dicts(c)

    # ── Observations (from MCP hook tool) ─────────────────────────────────────
    obs_session_filter = "AND session_id = ?" if session_id else ""
    c.execute(f"""
        SELECT rowid AS _rowid, id, session_id, prompt_id, title, subtitle,
               narrative, text, facts, concepts, type, files_read, files_modified, created_at
        FROM HOOK_OBSERVATION
        WHERE rowid > ? {obs_session_filter}
        ORDER BY rowid ASC
    """, (cp["last_observation_rowid"], *session_args))
    observations = _rows_as_dicts(c)

    # ── Session summaries ─────────────────────────────────────────────────────
    sum_session_filter = "AND session_id = ?" if session_id else ""
    c.execute(f"""
        SELECT rowid AS _rowid, id, session_id, project, request,
               investigated, learned, completed, next_steps, notes, created_at
        FROM SESSION_SUMMARY
        WHERE rowid > ? {sum_session_filter}
        ORDER BY rowid ASC
    """, (cp["last_summary_rowid"], *session_args))
    summaries = _rows_as_dicts(c)

    return {
        "sessions":    sessions,
        "prompts":     prompts,
        "responses":   responses,
        "io_tokens":   io_tokens,
        "tool_calls":  tools,
        "tool_tokens": tool_tokens,
        "thinking":    thinking,
        "observations": observations,
        "summaries":   summaries,
        "_checkpoint": cp,
    }


def advance_checkpoint(data: dict) -> None:
    """Update checkpoint rowids to the max rowid seen in each collection."""
    cp = data["_checkpoint"]

    def max_rowid(rows: list[dict]) -> int:
        return max((r["_rowid"] for r in rows), default=0)

    if data["sessions"]:
        cp["last_session_rowid"]     = max_rowid(data["sessions"])
    if data["prompts"]:
        cp["last_prompt_rowid"]      = max_rowid(data["prompts"])
    if data["responses"]:
        cp["last_response_rowid"]    = max_rowid(data["responses"])
    if data["io_tokens"]:
        cp["last_io_tokens_rowid"]   = max_rowid(data["io_tokens"])
    if data["tool_calls"]:
        cp["last_tool_rowid"]        = max_rowid(data["tool_calls"])
    if data["tool_tokens"]:
        cp["last_tool_tokens_rowid"] = max_rowid(data["tool_tokens"])
    if data["thinking"]:
        cp["last_thinking_rowid"]    = max_rowid(data["thinking"])
    if data["observations"]:
        cp["last_observation_rowid"] = max_rowid(data["observations"])
    if data["summaries"]:
        cp["last_summary_rowid"]     = max_rowid(data["summaries"])

    save_checkpoint(cp)


def has_data(data: dict) -> bool:
    """Return True if there is anything to sync."""
    return any(
        data[k]
        for k in ("sessions", "prompts", "responses", "io_tokens",
                  "tool_calls", "tool_tokens", "thinking", "observations", "summaries")
    )
