"""
Database Helper Functions for LLM Observations and Summaries

Provides utilities for querying tool calls, observations, and related data
for LLM-based observation and summary generation.
"""

import json
from typing import Any, Dict, List, Optional

from src.db.manager import get_db_connection
from src.common.logging import get_logger


logger = get_logger(__name__)


def has_tool_calls(session_id: str, prompt_id: str) -> bool:
    """
    Check if a prompt has any tool calls.

    Args:
        session_id: Session identifier
        prompt_id: Prompt identifier

    Returns:
        bool: True if the prompt has tool calls
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT COUNT(*) FROM TOOL WHERE prompt_id = ?
        """, (prompt_id,))

        count = cursor.fetchone()[0]
        # Don't close - using global connection that will be closed by caller
        cursor.close()

        return count > 0

    except Exception as e:
        logger.error(f"Error checking tool calls: {e}")
        return False


def get_tool_calls(session_id: str, prompt_id: str) -> List[Dict[str, Any]]:
    """
    Fetch all tool calls for a prompt.

    Args:
        session_id: Session identifier
        prompt_id: Prompt identifier

    Returns:
        list: List of tool call dicts with tool_name, input_json, output_json
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT tool_name, input_json, output_json
            FROM TOOL
            WHERE prompt_id = ?
            ORDER BY timestamp
        """, (prompt_id,))

        rows = cursor.fetchall()
        cursor.close()

        tool_calls = []
        for tool_name, input_json, output_json in rows:
            tool_calls.append({
                "tool_name": tool_name,
                "input_json": input_json,
                "output_json": output_json,
            })

        return tool_calls

    except Exception as e:
        logger.error(f"Error fetching tool calls: {e}")
        return []


def get_prompt_text(session_id: str, prompt_id: str) -> Optional[str]:
    """
    Fetch the prompt text for a given prompt_id.

    Args:
        session_id: Session identifier
        prompt_id: Prompt identifier

    Returns:
        str: Prompt text or None if not found
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT prompt FROM USER_PROMPT
            WHERE prompt_id = ? AND session_id = ?
        """, (prompt_id, session_id))

        row = cursor.fetchone()
        cursor.close()

        if row:
            return row[0]
        return None

    except Exception as e:
        logger.error(f"Error fetching prompt text: {e}")
        return None


def get_last_observation(session_id: str, count: int = 1) -> List[Dict[str, Any]]:
    """
    Get the most recent observations for a session.

    Args:
        session_id: Session identifier
        count: Number of recent observations to fetch

    Returns:
        list: List of observation dicts
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT title, subtitle, narrative, facts, concepts, type
            FROM OBSERVATION
            WHERE session_id = ?
            ORDER BY created_at DESC
            LIMIT ?
        """, (session_id, count))

        rows = cursor.fetchall()
        cursor.close()

        observations = []
        for title, subtitle, narrative, facts, concepts, obs_type in rows:
            observations.append({
                "title": title or "",
                "subtitle": subtitle or "",
                "narrative": narrative or "",
                "facts": facts or "",
                "concepts": concepts or "",
                "type": obs_type or "",
            })

        return observations

    except Exception as e:
        logger.error(f"Error fetching last observation: {e}")
        return []


def get_all_observations(session_id: str) -> List[Dict[str, Any]]:
    """
    Fetch all observations for a session (for summary generation).

    Args:
        session_id: Session identifier

    Returns:
        list: List of all observation dicts for the session
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT title, subtitle, narrative, facts, concepts, type,
                   files_read, files_modified
            FROM OBSERVATION
            WHERE session_id = ?
            ORDER BY created_at ASC
        """, (session_id,))

        rows = cursor.fetchall()
        cursor.close()

        observations = []
        for title, subtitle, narrative, facts, concepts, obs_type, files_read, files_modified in rows:
            observations.append({
                "title": title or "",
                "subtitle": subtitle or "",
                "narrative": narrative or "",
                "facts": facts or "",
                "concepts": concepts or "",
                "type": obs_type or "",
                "files_read": files_read or "",
                "files_modified": files_modified or "",
            })

        return observations

    except Exception as e:
        logger.error(f"Error fetching all observations: {e}")
        return []


def get_files_from_tools(session_id: str, prompt_id: str) -> Dict[str, List[str]]:
    """
    Extract files read/modified from tool calls.

    Args:
        session_id: Session identifier
        prompt_id: Prompt identifier

    Returns:
        dict: {"files_read": [...], "files_modified": [...]}
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT tool_name, input_json
            FROM TOOL
            WHERE prompt_id = ?
        """, (prompt_id,))

        rows = cursor.fetchall()
        cursor.close()

        files_read = set()
        files_modified = set()

        for tool_name, input_json in rows:
            try:
                input_data = json.loads(input_json) if input_json else {}

                # Extract file paths from common tools
                if tool_name == "Read":
                    file_path = input_data.get("file_path")
                    if file_path:
                        files_read.add(file_path)

                elif tool_name in ("Edit", "Write"):
                    file_path = input_data.get("file_path")
                    if file_path:
                        files_modified.add(file_path)

            except (json.JSONDecodeError, TypeError):
                pass

        return {
            "files_read": sorted(list(files_read)),
            "files_modified": sorted(list(files_modified)),
        }

    except Exception as e:
        logger.error(f"Error extracting files from tools: {e}")
        return {"files_read": [], "files_modified": []}


def observation_exists(session_id: str, prompt_id: str) -> bool:
    """
    Check if an observation already exists for a prompt.

    Args:
        session_id: Session identifier
        prompt_id: Prompt identifier (used as content_hash for lookup)

    Returns:
        bool: True if observation exists
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT COUNT(*) FROM OBSERVATION
            WHERE session_id = ? AND content_hash = ?
        """, (session_id, prompt_id))

        count = cursor.fetchone()[0]
        cursor.close()

        return count > 0

    except Exception as e:
        logger.error(f"Error checking observation existence: {e}")
        return False
