"""
Observation Database Writer

Saves extracted observations to the database.
"""

import json
import uuid
from src.common.time_utils import get_now_ist_iso
from typing import Any, Dict, List, Optional

from src.db.manager import get_db_connection
from src.common.logging import get_logger


logger = get_logger(__name__)


def _to_list(value: Any) -> list:
    """Normalize a value to a list — handles both native lists and JSON-encoded strings."""
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

        # Normalize to list first — Claude sometimes passes arrays as JSON strings
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

        # Dedup guard — skip if an observation with the same content already
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
