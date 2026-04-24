"""
Task Queue Implementation for CloudByte Worker

Provides thread-safe task queue with database persistence for LLM-based
observation and summary generation tasks.
"""

import json
import sqlite3
import threading
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.db.manager import DatabaseManager
from src.common.logging import get_logger


logger = get_logger(__name__)


class Task:
    """
    Task data model for LLM-based observation and summary generation.

    Attributes:
        id: Unique task identifier
        task_type: Type of task ('observation' or 'summary')
        session_id: Session identifier
        prompt_id: Prompt identifier (None for summary tasks)
        status: Task status ('pending', 'running', 'completed', 'failed')
        priority: Task priority (higher = more important)
        payload: JSON payload with task data
        error_message: Error message if failed
        created_at: Task creation timestamp
        started_at: Task start timestamp
        completed_at: Task completion timestamp
        retry_count: Number of retry attempts
    """

    def __init__(
        self,
        task_type: str,
        session_id: str,
        prompt_id: Optional[str] = None,
        priority: int = 0,
        payload: Optional[Dict[str, Any]] = None,
    ):
        self.id = str(uuid.uuid4())
        self.task_type = task_type
        self.session_id = session_id
        self.prompt_id = prompt_id
        self.status = "pending"
        self.priority = priority
        self.payload = payload or {}
        self.error_message = None
        self.created_at = datetime.now()
        self.started_at = None
        self.completed_at = None
        self.retry_count = 0

    def to_dict(self) -> Dict[str, Any]:
        """Convert task to dictionary."""
        return {
            "id": self.id,
            "task_type": self.task_type,
            "session_id": self.session_id,
            "prompt_id": self.prompt_id,
            "status": self.status,
            "priority": self.priority,
            "payload": json.dumps(self.payload) if self.payload else None,
            "error_message": self.error_message,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "retry_count": self.retry_count,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Task":
        """Create task from dictionary."""
        task = cls(
            task_type=data["task_type"],
            session_id=data["session_id"],
            prompt_id=data.get("prompt_id"),
            priority=data.get("priority", 0),
        )
        task.id = data["id"]
        task.status = data["status"]
        task.error_message = data.get("error_message")
        task.retry_count = data.get("retry_count", 0)

        # Parse timestamps
        if data.get("created_at"):
            task.created_at = datetime.fromisoformat(data["created_at"])
        if data.get("started_at"):
            task.started_at = datetime.fromisoformat(data["started_at"])
        if data.get("completed_at"):
            task.completed_at = datetime.fromisoformat(data["completed_at"])

        # Parse payload
        if data.get("payload"):
            try:
                task.payload = json.loads(data["payload"])
            except (json.JSONDecodeError, TypeError):
                task.payload = {}

        return task

    @classmethod
    def from_db_row(cls, row: tuple) -> "Task":
        """Create task from database row."""
        (
            task_id,
            task_type,
            session_id,
            prompt_id,
            status,
            priority,
            payload,
            error_message,
            created_at,
            started_at,
            completed_at,
            retry_count,
        ) = row

        task = cls(
            task_type=task_type,
            session_id=session_id,
            prompt_id=prompt_id,
            priority=priority or 0,
        )
        task.id = task_id
        task.status = status
        task.error_message = error_message
        task.retry_count = retry_count or 0
        task.created_at = created_at
        task.started_at = started_at
        task.completed_at = completed_at

        # Parse payload
        if payload:
            try:
                task.payload = json.loads(payload)
            except (json.JSONDecodeError, TypeError):
                task.payload = {}
        else:
            task.payload = {}

        return task


class TaskQueue:
    """
    Thread-safe task queue with database persistence.

    Provides FIFO queue with priority support for LLM-based
    observation and summary generation tasks.
    """

    def __init__(self, db_manager: DatabaseManager):
        """
        Initialize task queue.

        Args:
            db_manager: Database manager for persistence
        """
        self.db_manager = db_manager
        self._lock = threading.Lock()
        self._pending_tasks: List[Task] = []
        self._running_tasks: Dict[str, Task] = {}

    def enqueue(self, task: Task) -> bool:
        """
        Add task to queue.

        Args:
            task: Task to enqueue

        Returns:
            bool: True if enqueued successfully
        """
        import time
        max_retries = 10
        retry_delay = 0.3

        for attempt in range(max_retries):
            try:
                with self._lock:
                    # Get fresh connection for each attempt
                    conn = self.db_manager.get_connection()
                    cursor = conn.cursor()

                    cursor.execute("""
                        INSERT INTO TASK_QUEUE (
                            id, task_type, session_id, prompt_id, status,
                            priority, payload, error_message, created_at,
                            started_at, completed_at, retry_count
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        task.id,
                        task.task_type,
                        task.session_id,
                        task.prompt_id,
                        task.status,
                        task.priority,
                        json.dumps(task.payload) if task.payload else None,
                        task.error_message,
                        task.created_at,
                        task.started_at,
                        task.completed_at,
                        task.retry_count,
                    ))

                    conn.commit()
                    # Don't close cursor - let connection pool handle it

                    # Add to in-memory queue
                    self._pending_tasks.append(task)
                    # Sort by priority (descending) and created_at (ascending)
                    self._pending_tasks.sort(key=lambda t: (-t.priority, t.created_at))

                    logger.debug(f"Enqueued task {task.id} (type={task.task_type})")
                    return True

            except sqlite3.OperationalError as e:
                if "database is locked" in str(e) and attempt < max_retries - 1:
                    if attempt == 0:
                        logger.warning(f"Database locked, retrying enqueue (up to {max_retries} attempts)")
                    time.sleep(retry_delay)
                    continue
                logger.error(f"Failed to enqueue task after {max_retries} attempts: {e}")
                return False
            except Exception as e:
                logger.error(f"Failed to enqueue task: {e}", exc_info=True)
                return False

        return False

    def dequeue(self) -> Optional[Task]:
        """
        Get next pending task.

        Returns:
            Task or None if no pending tasks
        """
        try:
            with self._lock:
                if not self._pending_tasks:
                    return None

                # Get highest priority task
                task = self._pending_tasks.pop(0)

                # Update status
                task.status = "running"
                task.started_at = datetime.now()

                # Update in database
                self._update_task_status(task)

                # Track running task
                self._running_tasks[task.id] = task

                logger.debug(f"Dequeued task {task.id} (type={task.task_type})")
                return task

        except Exception as e:
            logger.error(f"Failed to dequeue task: {e}", exc_info=True)
            return None

    def update_status(
        self,
        task_id: str,
        status: str,
        error_message: Optional[str] = None
    ) -> bool:
        """
        Update task status.

        Args:
            task_id: Task identifier
            status: New status ('completed', 'failed', etc.)
            error_message: Error message if failed

        Returns:
            bool: True if updated successfully
        """
        try:
            with self._lock:
                # Check running tasks
                task = self._running_tasks.get(task_id)
                if task:
                    task.status = status
                    task.error_message = error_message

                    if status in ("completed", "failed"):
                        task.completed_at = datetime.now()
                        # Remove from running tasks
                        del self._running_tasks[task_id]

                    return self._update_task_status(task)

                # Task not found in running tasks
                logger.warning(f"Task {task_id} not found in running tasks")
                return False

        except Exception as e:
            logger.error(f"Failed to update task status: {e}", exc_info=True)
            return False

    def _update_task_status(self, task: Task) -> bool:
        """Update task status in database."""
        try:
            conn = self.db_manager.get_connection()
            cursor = conn.cursor()

            cursor.execute("""
                UPDATE TASK_QUEUE
                SET status = ?, error_message = ?, started_at = ?, completed_at = ?, retry_count = ?
                WHERE id = ?
            """, (
                task.status,
                task.error_message,
                task.started_at,
                task.completed_at,
                task.retry_count,
                task.id,
            ))

            conn.commit()
            cursor.close()
            return True

        except Exception as e:
            logger.error(f"Failed to update task in database: {e}", exc_info=True)
            return False

    def get_pending_count(self) -> int:
        """
        Count pending tasks.

        Returns:
            int: Number of pending tasks
        """
        with self._lock:
            return len(self._pending_tasks)

    def get_running_count(self) -> int:
        """
        Count running tasks.

        Returns:
            int: Number of running tasks
        """
        with self._lock:
            return len(self._running_tasks)

    def load_pending_tasks(self) -> int:
        """
        Load pending tasks from database on startup.

        Returns:
            int: Number of tasks loaded
        """
        try:
            conn = self.db_manager.get_connection()
            cursor = conn.cursor()

            # First, reset any orphaned 'running' tasks
            cursor.execute("""
                UPDATE TASK_QUEUE
                SET status = 'pending', retry_count = retry_count + 1
                WHERE status = 'running'
            """)
            orphaned_count = cursor.rowcount
            if orphaned_count > 0:
                logger.warning(f"Reset {orphaned_count} orphaned 'running' tasks to 'pending'")
            conn.commit()

            # Load pending tasks
            cursor.execute("""
                SELECT id, task_type, session_id, prompt_id, status,
                       priority, payload, error_message, created_at,
                       started_at, completed_at, retry_count
                FROM TASK_QUEUE
                WHERE status = 'pending'
                ORDER BY priority DESC, created_at ASC
            """)

            rows = cursor.fetchall()
            cursor.close()

            with self._lock:
                self._pending_tasks = [Task.from_db_row(row) for row in rows]

            logger.info(f"Loaded {len(self._pending_tasks)} pending tasks from database")
            return len(self._pending_tasks)

        except Exception as e:
            logger.error(f"Failed to load pending tasks: {e}", exc_info=True)
            return 0

    def cleanup_old_tasks(self, days: int = 7) -> int:
        """
        Remove completed tasks older than N days.

        Args:
            days: Days to keep tasks

        Returns:
            int: Number of tasks cleaned up
        """
        try:
            conn = self.db_manager.get_connection()
            cursor = conn.cursor()

            # Calculate cutoff date
            from datetime import timedelta
            cutoff = datetime.now() - timedelta(days=days)

            cursor.execute("""
                DELETE FROM TASK_QUEUE
                WHERE status IN ('completed', 'failed')
                AND completed_at < ?
            """, (cutoff,))

            count = cursor.rowcount
            conn.commit()
            cursor.close()

            logger.info(f"Cleaned up {count} old tasks")
            return count

        except Exception as e:
            logger.error(f"Failed to cleanup old tasks: {e}", exc_info=True)
            return 0
