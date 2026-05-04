"""
Worker API Routes

FastAPI routes that provide the same functionality as the standalone LLM worker.
These endpoints handle task queueing, status checks, and worker lifecycle.
"""

import sys
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

# Add src to path for imports
src_dir = Path(__file__).parent.parent.parent
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

from src.db.manager import get_db_manager
from src.workers.task_queue import Task, TaskQueue
from src.integrations.llm.generators import (
    generate_observation_for_tools,
    generate_summary_from_observations,
    save_observation_to_db,
    save_summary_to_db,
)
from src.integrations.llm.db_helpers import get_tool_calls, get_all_observations


router = APIRouter(prefix="/worker", tags=["worker"])

# Global state for worker threads
_worker_task_queue: Optional[TaskQueue] = None
_worker_running = False
_worker_shutdown_requested = False


class WorkerState:
    """Manages worker state across the FastAPI application."""

    def __init__(self):
        from src.common.logging import get_logger
        self.logger = get_logger(__name__)
        self.task_queue: Optional[TaskQueue] = None
        self.running = False
        self.shutdown_requested = False
        self.session_end_shutdown = False
        self.worker_exited = False  # Flag to indicate worker thread has exited
        self.worker_thread = None  # Reference to the worker thread

    def initialize(self):
        """Initialize the task queue."""
        if self.task_queue is None:
            db_manager = get_db_manager()
            self.task_queue = TaskQueue(db_manager)
            loaded = self.task_queue.load_pending_tasks()
            self.logger.info(f"Worker task queue initialized with {loaded} pending tasks")

    def start(self):
        """Start the worker (mark as running)."""
        self.running = True
        self.shutdown_requested = False
        self.worker_exited = False
        self.logger.info("Worker started")

    def stop(self):
        """Stop the worker (mark as not running)."""
        self.running = False
        self.shutdown_requested = True
        self.logger.info("Worker stopped")

    def mark_worker_exited(self):
        """Mark that the worker processing thread has exited."""
        self.worker_exited = True
        self.running = False
        self.logger.info("Worker processing thread exited")

    def is_thread_alive(self) -> bool:
        """Check if the worker thread is alive."""
        return self.worker_thread is not None and self.worker_thread.is_alive()

    def get_status(self) -> dict:
        """Get current worker status."""
        if self.task_queue is None:
            return {
                "running": False,
                "pending_tasks": 0,
                "running_tasks": 0,
                "shutdown_requested": False,
                "thread_alive": self.is_thread_alive(),
                "worker_exited": self.worker_exited,
            }
        return {
            "running": self.running,
            "pending_tasks": self.task_queue.get_pending_count(),
            "running_tasks": self.task_queue.get_running_count(),
            "shutdown_requested": self.shutdown_requested,
            "thread_alive": self.is_thread_alive(),
            "worker_exited": self.worker_exited,
        }


# Global worker state
_worker_state = WorkerState()


# Pydantic models for requests/responses
class HealthResponse(BaseModel):
    status: str
    running: bool
    port: int


class StatusResponse(BaseModel):
    running: bool
    pending_tasks: int
    running_tasks: int
    shutdown_requested: bool
    thread_alive: bool
    worker_exited: bool


class QueueTaskRequest(BaseModel):
    task_type: str = Field(..., description="Type of task: 'observation' or 'summary'")
    session_id: str = Field(..., description="Session ID")
    prompt_id: Optional[str] = Field(None, description="Prompt ID (for observation tasks)")
    priority: int = Field(0, description="Task priority (higher = more important)")
    payload: dict = Field(default_factory=dict, description="Additional task payload")


class QueueTaskResponse(BaseModel):
    status: str
    task_id: Optional[str] = None
    queue_position: Optional[int] = None
    message: Optional[str] = None


class ShutdownResponse(BaseModel):
    status: str
    pending_tasks: int
    running_tasks: int


@router.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint."""
    return HealthResponse(
        status="healthy",
        running=_worker_state.running,
        port=8765,
    )


@router.get("/status", response_model=StatusResponse)
async def get_status():
    """Get worker status."""
    return StatusResponse(**_worker_state.get_status())


@router.post("/reset", response_model=StatusResponse)
async def reset_worker():
    """Reset worker state to clear shutdown flags and prepare for new tasks."""
    from src.common.logging import get_logger
    logger = get_logger(__name__)

    logger.info("Resetting worker state...")

    # Clear shutdown flags
    _worker_state.shutdown_requested = False
    _worker_state.session_end_shutdown = False
    _worker_state.worker_exited = False

    # Restart worker if it was stopped
    if not _worker_state.running:
        _worker_state.start()
        logger.info("Worker restarted")

    logger.info("Worker state reset complete")
    return StatusResponse(**_worker_state.get_status())


@router.post("/restart", response_model=StatusResponse)
async def restart_worker():
    """Restart the worker processing thread if it has exited."""
    from src.common.logging import get_logger
    import threading
    import time
    logger = get_logger(__name__)

    # Check if thread is alive
    if _worker_state.is_thread_alive():
        logger.info("Worker thread is still running, no restart needed")
        return StatusResponse(**_worker_state.get_status())

    logger.info("Worker thread has exited, restarting...")

    # Clear all shutdown flags
    _worker_state.session_end_shutdown = False
    _worker_state.shutdown_requested = False
    _worker_state.worker_exited = False
    _worker_state.running = True

    # Reload pending tasks
    if _worker_state.task_queue:
        loaded = _worker_state.task_queue.load_pending_tasks()
        logger.info(f"Reloaded {loaded} pending tasks")

    # Start new processing thread
    def process_tasks():
        """Background thread to process tasks."""
        logger.info(f"Worker processing thread restarted, running={_worker_state.running}")

        while _worker_state.running:
            try:
                if _worker_state.task_queue is None:
                    time.sleep(1)
                    continue

                task = _worker_state.task_queue.dequeue()

                if task:
                    logger.info(f"Processing task {task.id} (type={task.task_type})")

                    try:
                        if task.task_type == "observation":
                            _process_observation(task)
                        elif task.task_type == "summary":
                            _process_summary(task)
                        else:
                            logger.error(f"Unknown task type: {task.task_type}")
                            _worker_state.task_queue.update_status(task.id, "failed", "Unknown task type")

                    except Exception as e:
                        logger.error(f"Error processing task {task.id}: {e}", exc_info=True)
                        if _worker_state.task_queue:
                            _worker_state.task_queue.update_status(task.id, "failed", str(e))
                else:
                    # No tasks, check if we should shut down
                    pending = _worker_state.task_queue.get_pending_count() if _worker_state.task_queue else 0
                    running = _worker_state.task_queue.get_running_count() if _worker_state.task_queue else 0

                    # Check for session_end shutdown (highest priority)
                    if _worker_state.session_end_shutdown:
                        if pending == 0 and running == 0:
                            logger.info("Session end shutdown: queue empty, stopping worker...")
                            break
                        else:
                            logger.debug(f"Session end shutdown: waiting for tasks (pending={pending}, running={running})")

                    # Check if shutdown requested and queue is empty
                    if _worker_state.shutdown_requested:
                        if pending == 0 and running == 0:
                            logger.info("Shutdown requested and queue empty, stopping...")
                            break

                    time.sleep(0.5)

            except Exception as e:
                logger.error(f"Error in task processing loop: {e}", exc_info=True)
                time.sleep(1)

        logger.info("Worker processing thread exiting")
        _worker_state.mark_worker_exited()

    thread = threading.Thread(target=process_tasks, daemon=True, name="WorkerTaskProcessor")
    thread.start()
    _worker_state.worker_thread = thread

    logger.info(f"Worker processing thread restarted (alive={thread.is_alive()})")
    return StatusResponse(**_worker_state.get_status())


@router.post("/queue", response_model=QueueTaskResponse)
async def queue_task(request: QueueTaskRequest):
    """Queue a new task for processing."""
    from src.common.logging import get_logger
    logger = get_logger(__name__)

    # Check if worker is shutting down (but allow queueing during session_end_shutdown)
    if _worker_state.shutdown_requested and not _worker_state.session_end_shutdown:
        raise HTTPException(status_code=503, detail="Worker is shutting down")

    # Validate task type
    if request.task_type not in ("observation", "summary"):
        raise HTTPException(
            status_code=400,
            detail="Invalid task_type. Must be 'observation' or 'summary'"
        )

    # Ensure task queue is initialized
    if _worker_state.task_queue is None:
        logger.info("Initializing task queue...")
        _worker_state.initialize()

    # Create task
    task = Task(
        task_type=request.task_type,
        session_id=request.session_id,
        prompt_id=request.prompt_id,
        priority=request.priority,
        payload=request.payload,
    )

    # Enqueue task with retry logic for database locks
    logger.info(f"Enqueueing task {task.id} (type={task.task_type}, session={task.session_id})")

    import time
    max_retries = 5
    retry_delay = 0.5

    for attempt in range(max_retries):
        if _worker_state.task_queue.enqueue(task):
            queue_position = _worker_state.task_queue.get_pending_count()
            logger.info(f"Task queued successfully at position {queue_position}")
            return QueueTaskResponse(
                status="queued",
                task_id=task.id,
                queue_position=queue_position,
            )

        # If enqueue failed, wait and retry
        if attempt < max_retries - 1:
            logger.warning(f"Failed to enqueue task {task.id} (attempt {attempt + 1}/{max_retries}), retrying...")
            time.sleep(retry_delay * (2 ** attempt))  # Exponential backoff

    logger.error(f"Failed to enqueue task {task.id} after {max_retries} attempts")
    raise HTTPException(status_code=500, detail="Failed to enqueue task after retries")


@router.post("/shutdown", response_model=ShutdownResponse)
async def shutdown_worker():
    """Request worker shutdown."""
    if _worker_state.shutdown_requested:
        return ShutdownResponse(
            status="already_shutting_down",
            pending_tasks=_worker_state.task_queue.get_pending_count() if _worker_state.task_queue else 0,
            running_tasks=_worker_state.task_queue.get_running_count() if _worker_state.task_queue else 0,
        )

    _worker_state.shutdown_requested = True

    return ShutdownResponse(
        status="shutting_down",
        pending_tasks=_worker_state.task_queue.get_pending_count() if _worker_state.task_queue else 0,
        running_tasks=_worker_state.task_queue.get_running_count() if _worker_state.task_queue else 0,
    )


@router.post("/shutdown-after-session", response_model=ShutdownResponse)
async def shutdown_worker_after_session():
    """Request worker shutdown after queue drains (called on session end)."""
    from src.common.logging import get_logger
    import subprocess
    import threading
    from pathlib import Path

    logger = get_logger(__name__)

    if _worker_state.session_end_shutdown:
        return ShutdownResponse(
            status="already_shutting_down",
            pending_tasks=_worker_state.task_queue.get_pending_count() if _worker_state.task_queue else 0,
            running_tasks=_worker_state.task_queue.get_running_count() if _worker_state.task_queue else 0,
        )

    # Set both flags for compatibility
    _worker_state.session_end_shutdown = True
    _worker_state.shutdown_requested = True

    # Execute kill_worker.py in background thread for comprehensive cleanup
    def execute_kill_worker():
        """Execute kill_worker.py for comprehensive process cleanup."""
        try:
            project_root = Path(__file__).parent.parent.parent.parent
            kill_script = project_root / "kill_worker.py"

            logger.info(f"Executing kill_worker.py from: {kill_script}")
            result = subprocess.run(
                [sys.executable, str(kill_script)],
                capture_output=True,
                text=True,
                cwd=project_root,
                timeout=30  # 30 second timeout
            )
            logger.info(f"kill_worker.py output: {result.stdout}")
            if result.returncode != 0:
                logger.warning(f"kill_worker.py failed: {result.stderr}")
            else:
                logger.info("kill_worker.py completed successfully")
        except Exception as e:
            logger.error(f"Error executing kill_worker.py: {e}", exc_info=True)

    # Start kill_worker.py in background thread
    kill_thread = threading.Thread(target=execute_kill_worker, daemon=True, name="KillWorker")
    kill_thread.start()
    logger.info("Started kill_worker.py execution in background thread")

    return ShutdownResponse(
        status="shutting_down_after_session",
        pending_tasks=_worker_state.task_queue.get_pending_count() if _worker_state.task_queue else 0,
        running_tasks=_worker_state.task_queue.get_running_count() if _worker_state.task_queue else 0,
    )


# Functions for startup/shutdown handlers
async def start_worker_processing():
    """Start worker processing threads (called on FastAPI startup)."""
    import os
    import threading
    import time
    import json
    from pathlib import Path
    from src.common.logging import get_logger
    from src.common.paths import get_cloudbyte_dir

    logger = get_logger(__name__)
    logger.info("=== Worker Startup Starting ===")

    try:
        _worker_state.initialize()
        logger.info("Task queue initialized")

        # Clear all shutdown flags and restart worker
        _worker_state.session_end_shutdown = False
        _worker_state.shutdown_requested = False
        _worker_state.worker_exited = False
        _worker_state.start()
        logger.info(f"Worker state reset and started: running={_worker_state.running}, shutdown_requested={_worker_state.shutdown_requested}")
    except Exception as e:
        logger.error(f"Error during worker initialization: {e}", exc_info=True)
        raise

    # Write PID file for compatibility
    try:
        pid_file = get_cloudbyte_dir() / "worker.pid"
        pid_data = {
            "pid": os.getpid(),
            "port": 8765,
            "start_time": time.time(),
            "type": "fastapi-integrated"
        }
        pid_file.write_text(json.dumps(pid_data, indent=2))
        logger.info(f"Wrote PID file: {pid_file}")
    except Exception as e:
        logger.warning(f"Failed to write PID file: {e}")

    def process_tasks():
        """Background thread to process tasks."""
        logger.info(f"Worker processing thread started, running={_worker_state.running}, shutdown_requested={_worker_state.shutdown_requested}")

        while _worker_state.running:
            try:
                if _worker_state.task_queue is None:
                    time.sleep(1)
                    continue

                task = _worker_state.task_queue.dequeue()

                if task:
                    logger.info(f"Processing task {task.id} (type={task.task_type})")

                    try:
                        if task.task_type == "observation":
                            _process_observation(task)
                        elif task.task_type == "summary":
                            _process_summary(task)
                        else:
                            logger.error(f"Unknown task type: {task.task_type}")
                            _worker_state.task_queue.update_status(task.id, "failed", "Unknown task type")

                    except Exception as e:
                        logger.error(f"Error processing task {task.id}: {e}", exc_info=True)
                        if _worker_state.task_queue:
                            _worker_state.task_queue.update_status(task.id, "failed", str(e))
                else:
                    # No tasks, check if we should shut down
                    pending = _worker_state.task_queue.get_pending_count() if _worker_state.task_queue else 0
                    running = _worker_state.task_queue.get_running_count() if _worker_state.task_queue else 0

                    # Check for session_end shutdown (highest priority)
                    if _worker_state.session_end_shutdown:
                        if pending == 0 and running == 0:
                            logger.info("Session end shutdown: queue empty, stopping worker...")
                            break
                        else:
                            logger.debug(f"Session end shutdown: waiting for tasks (pending={pending}, running={running})")

                    # Check if shutdown requested and queue is empty
                    if _worker_state.shutdown_requested:
                        if pending == 0 and running == 0:
                            logger.info("Shutdown requested and queue empty, stopping...")
                            break

                    time.sleep(0.5)

            except Exception as e:
                logger.error(f"Error in task processing loop: {e}", exc_info=True)
                time.sleep(1)

        logger.info("Worker processing thread exiting")
        _worker_state.mark_worker_exited()

    # Start processing thread
    thread = threading.Thread(target=process_tasks, daemon=True, name="WorkerTaskProcessor")
    thread.start()
    _worker_state.worker_thread = thread

    logger.info(f"Worker processing thread started (alive={thread.is_alive()})")
    logger.info("=== Worker Startup Complete ===")


def _process_observation(task: Task):
    """Process observation task."""
    from src.common.logging import get_logger
    logger = get_logger(__name__)

    try:
        tool_calls = get_tool_calls(task.session_id, task.prompt_id)

        if not tool_calls:
            logger.info(f"No tool calls for prompt {task.prompt_id}, skipping observation")
            _worker_state.task_queue.update_status(task.id, "completed")
            return

        observation = generate_observation_for_tools(
            session_id=task.session_id,
            prompt_id=task.prompt_id,
            tool_calls=tool_calls,
            endpoint_name=None,
        )

        if observation and not observation.get("skipped"):
            if save_observation_to_db(observation):
                logger.info(f"Observation generated and saved: {observation.get('id')}")
                _worker_state.task_queue.update_status(task.id, "completed")
            else:
                _worker_state.task_queue.update_status(task.id, "failed", "Failed to save observation")
        elif observation and observation.get("skipped"):
            logger.info(f"Observation skipped: {observation.get('reason', 'routine operations')}")
            _worker_state.task_queue.update_status(task.id, "completed")
        else:
            _worker_state.task_queue.update_status(task.id, "failed", "Observation generation returned None")

    except Exception as e:
        logger.error(f"Error processing observation task: {e}", exc_info=True)
        raise


def _process_summary(task: Task):
    """Process summary task."""
    from src.common.logging import get_logger
    logger = get_logger(__name__)

    try:
        observations = get_all_observations(task.session_id)

        if not observations:
            logger.info(f"No observations for session {task.session_id}, skipping summary")
            _worker_state.task_queue.update_status(task.id, "completed")
            return

        summary = generate_summary_from_observations(
            session_id=task.session_id,
            observations=observations,
            endpoint_name=None,
        )

        if summary:
            if save_summary_to_db(summary):
                logger.info(f"Summary generated and saved: {summary.get('id')}")
                _worker_state.task_queue.update_status(task.id, "completed")
            else:
                _worker_state.task_queue.update_status(task.id, "failed", "Failed to save summary")
        else:
            _worker_state.task_queue.update_status(task.id, "failed", "Summary generation returned None")

    except Exception as e:
        logger.error(f"Error processing summary task: {e}", exc_info=True)
        raise


async def stop_worker_processing():
    """Stop worker processing (called on FastAPI shutdown)."""
    from src.common.logging import get_logger
    from src.common.paths import get_cloudbyte_dir

    logger = get_logger(__name__)

    _worker_state.stop()

    # Remove PID file
    try:
        pid_file = get_cloudbyte_dir() / "worker.pid"
        if pid_file.exists():
            pid_file.unlink()
            logger.info(f"Removed PID file: {pid_file}")
    except Exception as e:
        logger.warning(f"Failed to remove PID file: {e}")

    logger.info("Worker processing stopped")


def get_worker_state() -> WorkerState:
    """Get the global worker state."""
    return _worker_state
