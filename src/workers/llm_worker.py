"""
LLM Worker Process for CloudByte

Background worker process that handles LLM-based observation and summary
generation tasks via an HTTP API.
"""

import json
import os
import signal
import socket
import sys
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Any, Dict, Optional

# Add src directory to path for imports
# This script is in src/workers/, so we need to add src/ to path
src_dir = Path(__file__).parent.parent
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

from src.db.manager import DatabaseManager, get_db_manager
from src.common.logging import get_logger, setup_logging
from src.common.paths import get_cloudbyte_dir
from src.workers.task_queue import Task, TaskQueue
from src.integrations.llm.generators import generate_observation_for_tools, generate_summary_from_observations
from src.integrations.llm.db_helpers import get_tool_calls, get_all_observations


logger = get_logger(__name__)


class WorkerHTTPRequestHandler(BaseHTTPRequestHandler):
    """HTTP request handler for worker API."""

    def log_message(self, format, *args):
        """Override to use our logger."""
        logger.debug(f"HTTP {self.address_string()} - {format % args}")

    def _send_json_response(self, status_code: int, data: Dict[str, Any]):
        """Send JSON response."""
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def _send_json_error(self, status_code: int, message: str):
        """Send JSON error response."""
        self._send_json_response(status_code, {"status": "error", "message": message})

    def do_GET(self):
        """Handle GET requests."""
        if self.path == "/health":
            self._handle_health()
        elif self.path == "/status":
            self._handle_status()
        else:
            self._send_json_error(404, "Not found")

    def do_POST(self):
        """Handle POST requests."""
        if self.path == "/queue":
            self._handle_queue()
        elif self.path == "/shutdown":
            self._handle_shutdown()
        elif self.path == "/shutdown-after-session":
            self._handle_shutdown_after_session()
        else:
            self._send_json_error(404, "Not found")

    def _handle_health(self):
        """Handle health check."""
        worker = self.server.worker  # type: LLMWorker
        self._send_json_response(200, {
            "status": "healthy",
            "running": worker.running,
            "port": worker.port,
        })

    def _handle_status(self):
        """Handle status check."""
        worker = self.server.worker  # type: LLMWorker
        self._send_json_response(200, {
            "running": worker.running,
            "port": worker.port,
            "pending_tasks": worker.task_queue.get_pending_count(),
            "running_tasks": worker.task_queue.get_running_count(),
            "shutdown_requested": worker.shutdown_requested,
        })

    def _handle_queue(self):
        """Handle task queue submission."""
        worker = self.server.worker  # type: LLMWorker

        # Check if worker is shutting down
        if worker.shutdown_requested:
            self._send_json_error(503, "Worker is shutting down")
            return

        try:
            # Read request body
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode()
            data = json.loads(body) if body else {}

            # Validate request
            task_type = data.get("task_type")
            session_id = data.get("session_id")

            if not task_type or not session_id:
                self._send_json_error(400, "Missing required fields: task_type, session_id")
                return

            if task_type not in ("observation", "summary"):
                self._send_json_error(400, "Invalid task_type. Must be 'observation' or 'summary'")
                return

            # Create task
            task = Task(
                task_type=task_type,
                session_id=session_id,
                prompt_id=data.get("prompt_id"),
                priority=data.get("priority", 0),
                payload=data.get("payload", {}),
            )

            # Enqueue task
            if worker.task_queue.enqueue(task):
                queue_position = worker.task_queue.get_pending_count()
                self._send_json_response(200, {
                    "status": "queued",
                    "task_id": task.id,
                    "queue_position": queue_position,
                })
                logger.info(f"Task queued: {task.id} (type={task_type}, session={session_id})")
            else:
                self._send_json_error(500, "Failed to enqueue task")

        except json.JSONDecodeError:
            self._send_json_error(400, "Invalid JSON in request body")
        except Exception as e:
            logger.error(f"Error handling queue request: {e}", exc_info=True)
            self._send_json_error(500, str(e))

    def _handle_shutdown(self):
        """Handle shutdown request."""
        worker = self.server.worker  # type: LLMWorker

        if worker.shutdown_requested:
            self._send_json_response(200, {
                "status": "already_shutting_down",
                "pending_tasks": worker.task_queue.get_pending_count(),
                "running_tasks": worker.task_queue.get_running_count(),
            })
            return

        worker.request_shutdown()

        self._send_json_response(200, {
            "status": "shutting_down",
            "pending_tasks": worker.task_queue.get_pending_count(),
            "running_tasks": worker.task_queue.get_running_count(),
        })

    def _handle_shutdown_after_session(self):
        """Handle shutdown request after session ends."""
        worker = self.server.worker  # type: LLMWorker

        if worker.session_end_shutdown:
            self._send_json_response(200, {
                "status": "already_shutting_down",
                "pending_tasks": worker.task_queue.get_pending_count(),
                "running_tasks": worker.task_queue.get_running_count(),
            })
            return

        # Set both flags for compatibility
        worker.session_end_shutdown = True
        worker.request_shutdown()

        self._send_json_response(200, {
            "status": "shutting_down_after_session",
            "pending_tasks": worker.task_queue.get_pending_count(),
            "running_tasks": worker.task_queue.get_running_count(),
        })


class LLMWorker:
    """
    Background worker process for LLM-based task processing.

    Runs an HTTP server for receiving tasks and processes them
    in background threads.
    """

    def __init__(
        self,
        port: int = 8765,
        max_workers: int = 2,
        shutdown_idle_seconds: int = 60,
        max_shutdown_wait_seconds: int = 300,
        auto_shutdown_idle_minutes: int = 5,
    ):
        """
        Initialize LLM worker.

        Args:
            port: HTTP server port
            max_workers: Maximum number of concurrent task processing threads
            shutdown_idle_seconds: Seconds to wait for queue drain on shutdown
            max_shutdown_wait_seconds: Maximum seconds to wait for graceful shutdown
            auto_shutdown_idle_minutes: Minutes of idle time before auto-shutdown (0 to disable)
        """
        self.port = port
        self.max_workers = max_workers
        self.shutdown_idle_seconds = shutdown_idle_seconds
        self.max_shutdown_wait_seconds = max_shutdown_wait_seconds
        self.auto_shutdown_idle_minutes = auto_shutdown_idle_minutes

        self.db_manager = get_db_manager()
        self.task_queue = TaskQueue(self.db_manager)
        self.running = False
        self.shutdown_requested = False
        self.session_end_shutdown = False
        self.last_activity_time = None
        self.http_server: Optional[HTTPServer] = None
        self.processing_threads: list[threading.Thread] = []

    def start(self):
        """Start the worker."""
        logger.info("=== LLM Worker Starting ===")
        logger.info(f"Port: {self.port}")
        logger.info(f"Max workers: {self.max_workers}")

        # Setup signal handlers
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

        # Set running flag BEFORE starting threads
        self.running = True

        # Load pending tasks from database
        self.task_queue.load_pending_tasks()

        # Start HTTP server
        self._start_http_server()

        # Start task processing threads
        self._start_processing_threads()

        logger.info("=== LLM Worker Started ===")

        # Keep running until shutdown
        self._run_main_loop()

    def stop(self):
        """Stop the worker gracefully."""
        logger.info("=== LLM Worker Stopping ===")

        self.shutdown_requested = True
        self.running = False

        # Stop HTTP server
        if self.http_server:
            logger.info("Stopping HTTP server...")
            self.http_server.shutdown()
            self.http_server = None

        # Wait for processing threads to finish
        logger.info(f"Waiting for {len(self.processing_threads)} processing threads...")
        for thread in self.processing_threads:
            thread.join(timeout=30)

        # Wait for queue to drain
        pending_count = self.task_queue.get_pending_count()
        running_count = self.task_queue.get_running_count()

        if pending_count > 0 or running_count > 0:
            logger.info(f"Waiting for queue to drain (pending={pending_count}, running={running_count})...")

            start_time = time.time()
            while (pending_count > 0 or running_count > 0) and \
                  (time.time() - start_time) < self.shutdown_idle_seconds:
                time.sleep(1)
                pending_count = self.task_queue.get_pending_count()
                running_count = self.task_queue.get_running_count()

            if pending_count > 0 or running_count > 0:
                logger.warning(f"Shutdown timeout with {pending_count} pending and {running_count} running tasks")

        # Close database connections
        logger.info("Closing database connections...")
        self.db_manager.close()

        # Remove PID file
        pid_file = get_cloudbyte_dir() / "worker.pid"
        if pid_file.exists():
            pid_file.unlink()

        logger.info("=== LLM Worker Stopped ===")

    def request_shutdown(self):
        """Request shutdown (called via HTTP)."""
        logger.info("Shutdown requested via HTTP")
        self.shutdown_requested = True

    def _start_http_server(self):
        """Start HTTP server in a separate thread."""
        # Find available port
        port = self._find_available_port()

        # Create HTTP server
        try:
            self.http_server = HTTPServer(("localhost", port), WorkerHTTPRequestHandler)
            self.http_server.worker = self  # Attach worker to server
            self.port = port

            # Run in separate thread
            server_thread = threading.Thread(target=self.http_server.serve_forever, daemon=True)
            server_thread.start()

            # Write PID file
            self._write_pid_file(port)

            logger.info(f"HTTP server started on port {port}")
        except OSError as e:
            logger.error(f"Failed to start HTTP server: {e}")
            raise

    def _start_processing_threads(self):
        """Start task processing threads."""
        for i in range(self.max_workers):
            thread = threading.Thread(
                target=self._process_tasks,
                name=f"TaskProcessor-{i}",
                daemon=True,
            )
            thread.start()
            self.processing_threads.append(thread)
            logger.info(f"Started processing thread {i}")

    def _process_tasks(self):
        """Background thread to process tasks from queue."""
        logger.info(f"Processing thread started, running={self.running}, shutdown_requested={self.shutdown_requested}")
        while self.running and not self.shutdown_requested:
            try:
                # Get next task
                task = self.task_queue.dequeue()

                if task:
                    logger.info(f"Processing task {task.id} (type={task.task_type})")

                    try:
                        # Process task based on type
                        if task.task_type == "observation":
                            self._process_observation(task)
                        elif task.task_type == "summary":
                            self._process_summary(task)
                        else:
                            logger.error(f"Unknown task type: {task.task_type}")
                            self.task_queue.update_status(task.id, "failed", "Unknown task type")

                    except Exception as e:
                        logger.error(f"Error processing task {task.id}: {e}", exc_info=True)
                        self.task_queue.update_status(task.id, "failed", str(e))

                else:
                    # No tasks, sleep briefly
                    logger.debug("No tasks in queue, sleeping...")
                    time.sleep(0.5)

            except Exception as e:
                logger.error(f"Error in task processing loop: {e}", exc_info=True)
                time.sleep(1)

    def _process_observation(self, task: Task):
        """Process observation task."""
        try:
            # Get tool calls
            tool_calls = get_tool_calls(task.session_id, task.prompt_id)

            if not tool_calls:
                logger.info(f"No tool calls for prompt {task.prompt_id}, skipping observation")
                self.task_queue.update_status(task.id, "completed")
                return

            # Generate observation
            observation = generate_observation_for_tools(
                session_id=task.session_id,
                prompt_id=task.prompt_id,
                tool_calls=tool_calls,
                endpoint_name=None,
            )

            if observation and not observation.get("skipped"):
                # Save to database
                from src.integrations.llm.generators import save_observation_to_db
                if save_observation_to_db(observation):
                    logger.info(f"Observation generated and saved: {observation.get('id')}")
                    self.task_queue.update_status(task.id, "completed")
                else:
                    self.task_queue.update_status(task.id, "failed", "Failed to save observation")
            elif observation and observation.get("skipped"):
                logger.info(f"Observation skipped: {observation.get('reason', 'routine operations')}")
                self.task_queue.update_status(task.id, "completed")
            else:
                self.task_queue.update_status(task.id, "failed", "Observation generation returned None")

        except Exception as e:
            logger.error(f"Error processing observation task: {e}", exc_info=True)
            raise

    def _process_summary(self, task: Task):
        """Process summary task."""
        try:
            # Get all observations for session
            observations = get_all_observations(task.session_id)

            if not observations:
                logger.info(f"No observations for session {task.session_id}, skipping summary")
                self.task_queue.update_status(task.id, "completed")
                return

            # Generate summary
            summary = generate_summary_from_observations(
                session_id=task.session_id,
                observations=observations,
                endpoint_name=None,
            )

            if summary:
                # Save to database
                from src.integrations.llm.generators import save_summary_to_db
                if save_summary_to_db(summary):
                    logger.info(f"Summary generated and saved: {summary.get('id')}")
                    self.task_queue.update_status(task.id, "completed")
                else:
                    self.task_queue.update_status(task.id, "failed", "Failed to save summary")
            else:
                self.task_queue.update_status(task.id, "failed", "Summary generation returned None")

        except Exception as e:
            logger.error(f"Error processing summary task: {e}", exc_info=True)
            raise

    def _run_main_loop(self):
        """Run main loop until shutdown."""
        import time as time_module
        self.last_activity_time = time_module.time()

        try:
            while self.running:
                time_module.sleep(1)

                pending = self.task_queue.get_pending_count()
                running = self.task_queue.get_running_count()

                # Update activity time if there are tasks
                if pending > 0 or running > 0:
                    self.last_activity_time = time_module.time()

                # Check for session_end shutdown (highest priority)
                if self.session_end_shutdown:
                    if pending == 0 and running == 0:
                        logger.info("Session end shutdown: queue empty, stopping worker...")
                        break
                    else:
                        logger.debug(f"Session end shutdown: waiting for tasks (pending={pending}, running={running})")

                # Check if shutdown requested and queue is empty
                if self.shutdown_requested:
                    if pending == 0 and running == 0:
                        logger.info("Shutdown requested and queue empty, stopping...")
                        break

                # Check for auto-shutdown due to idle timeout (only if not in session_end mode)
                if not self.session_end_shutdown and self.auto_shutdown_idle_minutes > 0:
                    idle_seconds = time_module.time() - self.last_activity_time
                    if idle_seconds > (self.auto_shutdown_idle_minutes * 60):
                        if pending == 0 and running == 0:
                            logger.info(f"Worker idle for {idle_seconds:.0f}s, auto-shutting down...")
                            break
                        else:
                            logger.debug(f"Worker has tasks (pending={pending}, running={running}), not shutting down")

        except KeyboardInterrupt:
            logger.info("Interrupted by user")
            self.shutdown_requested = True

    def _signal_handler(self, signum, frame):
        """Handle signal interrupts."""
        logger.info(f"Received signal {signum}, initiating shutdown...")
        self.shutdown_requested = True

    def _find_available_port(self) -> int:
        """Find an available port starting from self.port."""
        port = self.port
        max_attempts = 10

        for i in range(max_attempts):
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.bind(("localhost", port))
                    return port
            except OSError:
                port += 1

        raise RuntimeError(f"Could not find available port after {max_attempts} attempts")

    def _write_pid_file(self, port: int):
        """Write PID file with worker information."""
        pid_file = get_cloudbyte_dir() / "worker.pid"

        # Ensure .cloudbyte directory exists
        get_cloudbyte_dir().mkdir(parents=True, exist_ok=True)

        pid_data = {
            "pid": os.getpid(),
            "port": port,
            "start_time": time.time(),
        }

        try:
            pid_file.write_text(json.dumps(pid_data, indent=2))
            logger.info(f"Wrote PID file: {pid_file}")
        except Exception as e:
            logger.error(f"Failed to write PID file: {e}")


def main():
    """Main entry point for worker process."""
    import argparse

    # DEPRECATION NOTICE
    logger.warning("=" * 60)
    logger.warning("DEPRECATION NOTICE: Standalone worker is deprecated")
    logger.warning("The worker is now integrated into the FastAPI dashboard")
    logger.warning("Please use: uv run uvicorn src.app.app:app --port 8765")
    logger.warning("=" * 60)

    parser = argparse.ArgumentParser(description="CloudByte LLM Worker")
    parser.add_argument("--port", type=int, default=8765, help="HTTP server port")
    parser.add_argument("--max-workers", type=int, default=2, help="Maximum concurrent workers")
    parser.add_argument("--shutdown-idle", type=int, default=60, help="Shutdown idle seconds")
    parser.add_argument("--max-shutdown-wait", type=int, default=300, help="Max shutdown wait seconds")

    args = parser.parse_args()

    # Setup logging
    setup_logging(log_to_file=True, log_to_console=True)

    # Create and start worker
    worker = LLMWorker(
        port=args.port,
        max_workers=args.max_workers,
        shutdown_idle_seconds=args.shutdown_idle,
        max_shutdown_wait_seconds=args.max_shutdown_wait,
    )

    try:
        worker.start()
    except Exception as e:
        logger.error(f"Worker error: {e}", exc_info=True)
        sys.exit(1)
    finally:
        worker.stop()


if __name__ == "__main__":
    main()
