"""
Observation Database Writer

Saves extracted observations to the database.
"""

import json
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.db.manager import get_db_connection
from src.common.logging import get_logger


logger = get_logger(__name__)


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

        # Convert lists to JSON strings
        facts = json.dumps(obs_data.get("facts", []))
        concepts = json.dumps(obs_data.get("concepts", []))
        files_read = json.dumps(obs_data.get("files_read", []))
        files_modified = json.dumps(obs_data.get("files_modified", []))

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
            datetime.now().isoformat(),
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
