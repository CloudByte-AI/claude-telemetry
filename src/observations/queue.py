"""
Observation Task Queue Management

Handles queueing and management of observation generation tasks.
"""

import json
import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from src.db.manager import get_db_connection
from src.common.logging import get_logger


logger = get_logger(__name__)


def queue_observation_task(
    session_id: str,
    prompt_id: str,
    priority: int = 0,
) -> Dict[str, Any]:
    """
    Queue an observation generation task.

    Args:
        session_id: Session identifier
        prompt_id: Prompt identifier
        priority: Task priority (higher = more important)

    Returns:
        Dict with task_id and status
    """
    try:
        task_id = str(uuid.uuid4())
        payload = json.dumps({
            "session_id": session_id,
            "prompt_id": prompt_id,
        })

        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO TASK_QUEUE (
                id, task_type, session_id, prompt_id, status,
                priority, payload, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            task_id,
            "observation",
            session_id,
            prompt_id,
            "pending",
            priority,
            payload,
            datetime.now().isoformat(),
        ))

        conn.commit()
        cursor.close()

        logger.info(f"Queued observation task {task_id} for prompt {prompt_id}")
        return {
            "task_id": task_id,
            "status": "queued",
        }

    except Exception as e:
        logger.error(f"Failed to queue observation task: {e}", exc_info=True)
        return {
            "status": "error",
            "message": str(e),
        }


def get_pending_tasks(limit: int = 10) -> list[Dict[str, Any]]:
    """
    Get pending observation tasks from the queue.

    Args:
        limit: Maximum number of tasks to return

    Returns:
        List of task dicts
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT id, session_id, prompt_id, payload, priority, created_at
            FROM TASK_QUEUE
            WHERE status = 'pending' AND task_type = 'observation'
            ORDER BY priority DESC, created_at ASC
            LIMIT ?
        """, (limit,))

        rows = cursor.fetchall()
        cursor.close()

        tasks = []
        for row in rows:
            tasks.append({
                "id": row[0],
                "session_id": row[1],
                "prompt_id": row[2],
                "payload": row[3],
                "priority": row[4],
                "created_at": row[5],
            })

        return tasks

    except Exception as e:
        logger.error(f"Failed to get pending tasks: {e}", exc_info=True)
        return []


def mark_task_started(task_id: str) -> bool:
    """
    Mark a task as started.

    Args:
        task_id: Task identifier

    Returns:
        True if successful
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("""
            UPDATE TASK_QUEUE
            SET status = 'processing', started_at = ?
            WHERE id = ?
        """, (datetime.now().isoformat(), task_id))

        conn.commit()
        cursor.close()

        return True

    except Exception as e:
        logger.error(f"Failed to mark task as started: {e}", exc_info=True)
        return False


def mark_task_completed(task_id: str, error_message: Optional[str] = None) -> bool:
    """
    Mark a task as completed.

    Args:
        task_id: Task identifier
        error_message: Optional error message if task failed

    Returns:
        True if successful
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        status = "completed" if not error_message else "failed"

        cursor.execute("""
            UPDATE TASK_QUEUE
            SET status = ?, completed_at = ?, error_message = ?
            WHERE id = ?
        """, (status, datetime.now().isoformat(), error_message, task_id))

        conn.commit()
        cursor.close()

        return True

    except Exception as e:
        logger.error(f"Failed to mark task as completed: {e}", exc_info=True)
        return False
