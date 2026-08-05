"""
Observation Database Writer

Saves extracted observations to the database.
"""

import json
import os
import uuid
from src.common.time_utils import get_now_ist_iso
from typing import Any, Dict, List, Optional

from src.db.manager import get_db_connection
from src.common.logging import get_logger
from src.common.obs_salvage import salvage_obs_args


logger = get_logger(__name__)


OBS_FIELDS = (
    "type", "title", "subtitle", "narrative",
    "facts", "concepts", "files_read", "files_modified",
)

# concepts, files_read and files_modified are legitimately empty on some
# observations (a discovery that wrote nothing), so they are not counted here.
OBS_NEVER_EMPTY = ("type", "title", "subtitle", "narrative", "facts")


def audit_obs_fields(obs_data: Dict[str, Any]) -> Dict[str, Any]:
    """Describe what an observation payload is missing or has renamed.

    Runs on every write path (stop hook, recovery, Cursor afterMCPExecution) so
    a payload that arrives with invented field names is visible in the log
    instead of silently landing as a title-only row.
    """
    obs_data = obs_data if isinstance(obs_data, dict) else {}
    unknown = sorted(set(obs_data) - set(OBS_FIELDS))
    return {
        "missing": [f for f in OBS_NEVER_EMPTY if not obs_data.get(f)],
        "absent": [f for f in OBS_FIELDS if f not in obs_data],
        "unknown": unknown,
        "dropped_chars": sum(len(json.dumps(obs_data[k], default=str)) for k in unknown),
    }


def _log_obs_audit(prompt_id: str, obs_data: Dict[str, Any]) -> None:
    """Emit one OBS_INCOMPLETE warning when a payload will store badly."""
    audit = audit_obs_fields(obs_data)
    if not (audit["missing"] or audit["unknown"]):
        return
    logger.warning(
        f"OBS_INCOMPLETE save_observation: prompt_id={prompt_id} "
        f"title={str(obs_data.get('title', ''))[:60]!r} "
        f"missing={audit['missing']} absent={audit['absent']} "
        f"unknown={audit['unknown']} dropped_chars={audit['dropped_chars']}"
    )


def obs_gate_enabled() -> bool:
    """True unless CLOUDBYTE_OBS_STRICT is switched off in this process.

    Only consulted where a write path must decide something on its own (see
    recovery pass1). It deliberately does NOT gate was_rejected - see there.
    """
    return os.environ.get("CLOUDBYTE_OBS_STRICT", "1").strip().lower() not in ("0", "false", "no")


# Each client hands us the tool result under a different key, and wraps the flag
# in a different case. Claude's ingest normalises to {"result", "is_error"};
# Cursor passes the raw MCP envelope {"content": [...], "isError": bool} through
# afterMCPExecution.result_json and postToolUse.tool_output.
_RESULT_KEYS = ("output_json", "result_json", "tool_output")
_ERROR_KEYS = ("is_error", "isError")


def was_rejected(tool: Dict[str, Any]) -> bool:
    """True when the MCP server refused this record_observation call.

    The MCP server holds no database handle - it only answers the model. The row
    is written later by whichever path re-reads the transcript or hook payload,
    so a call the server rejected would still be persisted unless that path
    skips it here. Without this gate, turning on rejection stores the rejected
    draft *and* its corrected retry, because save_observation dedups on
    content_hash and the two payloads differ.

    Accepts any of the client result shapes. Returns False whenever no readable
    error flag is present, so an absent, unparseable or unrecognised tool result
    never costs an observation.

    Deliberately NOT gated on CLOUDBYTE_OBS_STRICT. The kill switch belongs to
    the MCP server, which is what decides to reject; with it off no call is ever
    refused and this gate is inert anyway. Honouring it here would instead open
    a footgun: hooks read env from the shell while the server reads it from
    mcp.json, so the two can diverge, and a strict server paired with a lenient
    writer stores the refused draft *and* its retry.
    """
    if not isinstance(tool, dict):
        return False
    for result_key in _RESULT_KEYS:
        raw = tool.get(result_key)
        if raw is None:
            continue
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                continue
        if isinstance(raw, dict):
            for error_key in _ERROR_KEYS:
                if error_key in raw:
                    return bool(raw[error_key])
    return False


def _to_list(value: Any) -> list:
    """Normalize a value to a list - handles both native lists and JSON-encoded strings."""
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass
    return []


def save_observation(
    session_id: str,
    prompt_id: str,
    obs_data: Dict[str, Any]
) -> Optional[str]:
    """
    Save an observation to the database.

    Args:
        session_id: Session identifier
        prompt_id: Prompt identifier
        obs_data: Parsed observation dict from obs block

    Returns:
        Observation ID if saved successfully, None otherwise
    """
    try:
        obs_id = str(uuid.uuid4())

        # Repair before anything else, so every write path benefits and the
        # audit reports what will actually be stored rather than what arrived.
        # The MCP server salvages too, but a row can reach here from the JSONL
        # transcript or a Cursor hook without ever passing back through it.
        obs_data, repairs = salvage_obs_args(obs_data)
        if repairs:
            logger.warning(
                f"OBS_SALVAGED save_observation: prompt_id={prompt_id} "
                f"title={str(obs_data.get('title', ''))[:60]!r} {repairs}"
            )

        _log_obs_audit(prompt_id, obs_data)

        # Normalize to list first - Claude sometimes passes arrays as JSON strings
        facts = json.dumps(_to_list(obs_data.get("facts", [])))
        concepts = json.dumps(_to_list(obs_data.get("concepts", [])))
        files_read = json.dumps(_to_list(obs_data.get("files_read", [])))
        files_modified = json.dumps(_to_list(obs_data.get("files_modified", [])))

        # Generate text field from other fields
        text_parts = [f"**{obs_data.get('title', '')}**"]
        subtitle = obs_data.get("subtitle", "")
        if subtitle:
            text_parts.append(subtitle)

        narrative = obs_data.get("narrative", "")
        if narrative:
            text_parts.append(narrative[:200] + "..." if len(narrative) > 200 else narrative)

        text = "\n\n".join(text_parts)

        # Generate content hash
        content_str = f"{obs_data.get('title', '')}{obs_data.get('narrative', '')}{files_modified}"
        import hashlib
        content_hash = hashlib.sha256(content_str.encode()).hexdigest()[:16]

        conn = get_db_connection()
        cursor = conn.cursor()

        # Dedup guard - skip if an observation with the same content already
        # exists for this prompt.  Prevents duplicate rows when process_missed_pairs
        # runs on every UserPromptSubmit for a session with a past interrupted
        # MCP observation call (Bug #1).
        cursor.execute(
            "SELECT 1 FROM HOOK_OBSERVATION WHERE prompt_id = ? AND content_hash = ? LIMIT 1",
            (prompt_id, content_hash),
        )
        if cursor.fetchone():
            cursor.close()
            logger.debug(
                f"save_observation: skipping duplicate "
                f"(prompt_id={prompt_id}, hash={content_hash})"
            )
            return None

        cursor.execute("""
            INSERT INTO HOOK_OBSERVATION (
                id, session_id, prompt_id, title, subtitle, narrative,
                text, facts, concepts, type, files_read, files_modified,
                content_hash, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            obs_id,
            session_id,
            prompt_id,
            obs_data.get("title", "")[:100],
            obs_data.get("subtitle", "")[:200],
            obs_data.get("narrative", ""),
            text,
            facts,
            concepts,
            obs_data.get("type", "change"),
            files_read,
            files_modified,
            content_hash,
            get_now_ist_iso(),
        ))

        conn.commit()
        cursor.close()

        return obs_id

    except Exception as e:
        logger.error(f"Failed to save observation: {e}", exc_info=True)
        return None


def get_session_observations(session_id: str) -> List[Dict[str, Any]]:
    """
    Get all hook-based observations for a session.

    Args:
        session_id: Session identifier

    Returns:
        List of observation dicts
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT id, prompt_id, title, subtitle, narrative, text,
                   facts, concepts, type, files_read, files_modified,
                   content_hash, created_at
            FROM HOOK_OBSERVATION
            WHERE session_id = ?
            ORDER BY created_at ASC
        """, (session_id,))

        rows = cursor.fetchall()
        cursor.close()

        observations = []
        for row in rows:
            observations.append({
                "id": row[0],
                "prompt_id": row[1],
                "title": row[2],
                "subtitle": row[3],
                "narrative": row[4],
                "text": row[5],
                "facts": json.loads(row[6]) if row[6] else [],
                "concepts": json.loads(row[7]) if row[7] else [],
                "type": row[8],
                "files_read": json.loads(row[9]) if row[9] else [],
                "files_modified": json.loads(row[10]) if row[10] else [],
                "content_hash": row[11],
                "created_at": row[12],
            })

        return observations

    except Exception as e:
        logger.error(f"Failed to get observations: {e}", exc_info=True)
        return []


def cleanup_duplicate_observations() -> int:
    """
    Remove duplicate HOOK_OBSERVATION rows, keeping the earliest insertion
    per (prompt_id, content_hash).  Uses SQLite rowid for stable ordering.

    Returns the number of rows deleted.
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            DELETE FROM HOOK_OBSERVATION
            WHERE rowid NOT IN (
                SELECT MIN(rowid)
                FROM HOOK_OBSERVATION
                GROUP BY prompt_id, content_hash
            )
        """)
        deleted = cursor.rowcount
        conn.commit()
        cursor.close()
        if deleted:
            logger.info(
                f"cleanup_duplicate_observations: removed {deleted} duplicate row(s)"
            )
        return deleted
    except Exception as exc:
        logger.error(f"cleanup_duplicate_observations failed: {exc}", exc_info=True)
        return 0
