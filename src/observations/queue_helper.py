"""
Task Queue Helper Module

Provides direct database insertion for tasks without HTTP overhead.
Used by hooks to queue tasks for the worker.
"""

import uuid
from datetime import datetime
from typing import Optional

from src.common.logging import get_logger


logger = get_logger(__name__)


def queue_task_direct(
    task_type: str,
    session_id: str,
    prompt_id: Optional[str] = None,
    priority: int = 0,
    payload: Optional[dict] = None,
) -> Optional[str]:
    """
    Queue a task directly to the database (no HTTP overhead).

    Args:
        task_type: Type of task ('observation')
        session_id: Session identifier
        prompt_id: Optional prompt identifier (for observation tasks)
        priority: Task priority (higher = more important)
        payload: Optional task payload (JSON)

    Returns:
        str: Task ID if queued successfully, None otherwise
    """
    try:
        from src.db.manager import get_db_connection

        conn = get_db_connection()
        cursor = conn.cursor()
        task_id = str(uuid.uuid4())
        created_at = datetime.now().isoformat()

        # Direct insert to TASK_QUEUE table
        cursor.execute(
            """INSERT INTO TASK_QUEUE (id, task_type, session_id, prompt_id, status, priority, payload, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (task_id, task_type, session_id, prompt_id, "pending", priority,
             __import__('json').dumps(payload) if payload else None, created_at)
        )

        # Commit the transaction
        conn.commit()
        cursor.close()

        logger.info(f"Task {task_type} queued directly to database: {task_id}")
        return task_id

    except Exception as e:
        logger.error(f"Failed to queue {task_type} task to database: {e}", exc_info=True)
        return None


def queue_observation_direct(
    session_id: str,
    prompt_id: str,
    priority: int = 0,
) -> Optional[str]:
    """
    Queue an observation task directly to the database.

    Args:
        session_id: Session identifier
        prompt_id: Prompt identifier
        priority: Task priority (default 0 for observations)

    Returns:
        str: Task ID if queued successfully, None otherwise
    """
    return queue_task_direct("observation", session_id, prompt_id, priority)
