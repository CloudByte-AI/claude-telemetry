"""
LLM Worker Client for CloudByte

HTTP client for submitting tasks to the LLM worker process.
Used by hooks to queue observation and summary tasks.

Note: With FastAPI integration, the worker runs within the FastAPI app
rather than as a separate process.
"""

import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional

# Add src directory to path for imports
# This script is in src/workers/, so we need to add src/ to path
src_dir = Path(__file__).parent.parent
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

from src.common.logging import get_logger
from src.common.paths import get_cloudbyte_dir

# Try to import psutil for process detection
try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    psutil = None
    PSUTIL_AVAILABLE = False
    # Note: logger not initialized yet, will log later if needed


logger = get_logger(__name__)


# Default worker configuration
DEFAULT_WORKER_PORT = 8765
DEFAULT_WORKER_TIMEOUT = 15  # seconds for HTTP requests
_worker_start_time = None  # Track when worker was started


def get_worker_port() -> int:
    """
    Get the worker port from PID file or use default.

    Returns:
        int: Worker port number
    """
    pid_file = get_cloudbyte_dir() / "worker.pid"

    if pid_file.exists():
        try:
            pid_data = json.loads(pid_file.read_text())
            return pid_data.get("port", DEFAULT_WORKER_PORT)
        except (json.JSONDecodeError, IOError):
            pass

    return DEFAULT_WORKER_PORT


def is_worker_running() -> bool:
    """
    Check if worker is running via HTTP health check.

    With FastAPI integration, we check the HTTP endpoint instead of
    checking for a PID file or process.

    Returns:
        bool: True if worker is running and accepting tasks (not in shutdown)
    """
    status = get_worker_status()
    # Worker must be running AND not in shutdown state
    return status.get("running", False) and not status.get("shutdown_requested", False)


def is_worker_process_running() -> bool:
    """
    Check if worker process is running (regardless of shutdown state).

    This is used for shutdown requests - we need to know if the worker
    process exists even if it's in shutdown state.

    Returns:
        bool: True if worker process is running
    """
    status = get_worker_status()
    return status.get("running", False)


def is_worker_process_alive() -> bool:
    """
    Check if worker process is alive by checking PID file.

    This is a fallback method that doesn't rely on HTTP endpoint.

    Returns:
        bool: True if worker process is alive, False otherwise
    """
    pid_file = get_cloudbyte_dir() / "worker.pid"
    if not pid_file.exists():
        return False

    try:
        pid_data = json.loads(pid_file.read_text())
        pid = pid_data.get("pid")
        if pid and PSUTIL_AVAILABLE:
            return psutil.pid_exists(pid)
        elif pid:
            # Fallback without psutil - use signal 0 to check if process exists
            import signal
            import os
            try:
                os.kill(pid, 0)  # Signal 0 doesn't actually kill the process
                return True
            except OSError:
                return False
    except Exception as e:
        logger.debug(f"Failed to check worker process: {e}")
        return False

    return False


def get_worker_status() -> Dict[str, Any]:
    """
    Get worker status via HTTP.

    Returns:
        dict: Worker status with keys:
            - running: bool
            - port: int
            - pending_tasks: int
            - running_tasks: int
            - shutdown_requested: bool
    """
    global _worker_start_time
    port = get_worker_port()
    url = f"http://localhost:{port}/worker/status"

    try:
        with urllib.request.urlopen(url, timeout=DEFAULT_WORKER_TIMEOUT) as response:
            data = json.loads(response.read().decode())
            return data
    except Exception as e:
        # Suppress warnings during worker startup grace period (5 seconds)
        if _worker_start_time and (time.time() - _worker_start_time) < 5:
            logger.debug(f"Worker not ready yet: {e}")
        else:
            logger.warning(f"Failed to get worker status: {e}")
        return {
            "running": False,
            "port": port,
            "pending_tasks": 0,
            "running_tasks": 0,
            "shutdown_requested": False,
        }


def reset_worker() -> bool:
    """
    Reset worker state to clear shutdown flags.

    Returns:
        bool: True if reset successful, False otherwise
    """
    port = get_worker_port()
    url = f"http://localhost:{port}/worker/reset"

    try:
        data = _http_post(url, {})
        if data and data.get("running"):
            logger.info("Worker reset successfully")
            return True
        else:
            logger.warning(f"Worker reset returned unexpected response: {data}")
            return False
    except Exception as e:
        logger.error(f"Failed to reset worker: {e}")
        return False


def restart_worker() -> bool:
    """
    Restart the worker processing thread if it has exited.

    Returns:
        bool: True if restart successful, False otherwise
    """
    port = get_worker_port()
    url = f"http://localhost:{port}/worker/restart"

    try:
        data = _http_post(url, {})
        if data and data.get("running"):
            logger.info("Worker restarted successfully")
            return True
        else:
            logger.warning(f"Worker restart returned unexpected response: {data}")
            return False
    except Exception as e:
        logger.error(f"Failed to restart worker: {e}")
        return False


def ensure_worker_running() -> bool:
    """
    Start FastAPI app with integrated worker if not already running.

    Returns:
        bool: True if worker is running, False otherwise
    """
    if is_worker_running():
        logger.debug("Worker already running and accepting tasks")
        return True

    # Check if worker exists but is in shutdown state
    try:
        status = get_worker_status()
        if status.get("running") and status.get("shutdown_requested"):
            logger.info("Worker is in shutdown state, resetting...")
            return reset_worker()
    except Exception:
        pass  # Worker not responding, will start new one

    logger.info("Starting FastAPI app with integrated worker...")
    global _worker_start_time
    _worker_start_time = time.time()

    try:
        # Get the project path
        project_dir = Path(__file__).parent.parent.parent

        # Start FastAPI app using uv run
        if sys.platform == "win32":
            # Windows: Use pythonw.exe (no console) and CREATE_NO_WINDOW
            DETACHED_PROCESS = 0x00000008
            CREATE_NO_WINDOW = 0x08000000

            # Find pythonw.exe (GUI Python - no console window)
            python_exe = sys.executable.replace("python.exe", "pythonw.exe")
            if not Path(python_exe).exists():
                python_exe = sys.executable

            process = subprocess.Popen(
                ["uv", "run", "--directory", str(project_dir),
                 "uvicorn", "src.app.app:app", "--host", "0.0.0.0", "--port", "8765"],
                creationflags=DETACHED_PROCESS | CREATE_NO_WINDOW,
                executable=python_exe,
            )
        else:
            # Unix-like systems
            process = subprocess.Popen(
                ["uv", "run", "--directory", str(project_dir), "uvicorn", "src.app.app:app", "--host", "0.0.0.0", "--port", "8765"],
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

        logger.info(f"Started FastAPI app process (PID: {process.pid})")

        # Wait for worker to start
        max_wait = 30  # seconds
        start_time = time.time()

        while time.time() - start_time < max_wait:
            try:
                if is_worker_running():
                    logger.info("FastAPI app with worker started successfully")
                    _worker_start_time = None  # Clear startup flag
                    return True
            except Exception:
                pass  # Worker not ready yet
            time.sleep(1)

        # Check if process is still running
        if process.poll() is None:
            logger.warning("FastAPI app process started but not responding yet")
            return True
        else:
            logger.error("FastAPI app process failed to start")
            return False

    except Exception as e:
        logger.error(f"Failed to start FastAPI app: {e}", exc_info=True)
        return False


def _http_post(url: str, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Make HTTP POST request to worker.

    Args:
        url: URL to post to
        data: Data to send

    Returns:
        dict: Response data or None if failed
    """
    try:
        body = json.dumps(data).encode()
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        with urllib.request.urlopen(req, timeout=DEFAULT_WORKER_TIMEOUT) as response:
            return json.loads(response.read().decode())

    except urllib.error.HTTPError as e:
        logger.error(f"HTTP error: {e.code} - {e.reason}")
        try:
            return json.loads(e.read().decode())
        except:
            return {"status": "error", "message": str(e)}
    except Exception as e:
        logger.error(f"HTTP request failed: {e}")
        return None


def queue_observation_task(
    session_id: str,
    prompt_id: str,
    priority: int = 0
) -> Dict[str, Any]:
    """
    Queue observation task for processing.

    Args:
        session_id: Session identifier
        prompt_id: Prompt identifier
        priority: Task priority (higher = more important)

    Returns:
        dict: Response with keys:
            - status: 'queued' | 'error'
            - task_id: str (if queued)
            - queue_position: int (if queued)
            - message: str (if error)
    """
    # Ensure worker is running
    if not ensure_worker_running():
        return {"status": "error", "message": "Failed to start worker"}

    port = get_worker_port()
    url = f"http://localhost:{port}/worker/queue"

    data = {
        "task_type": "observation",
        "session_id": session_id,
        "prompt_id": prompt_id,
        "priority": priority,
    }

    response = _http_post(url, data)

    if response and response.get("status") == "queued":
        logger.info(f"Observation task queued: {response['task_id']}")
        return response
    else:
        error_msg = response.get("message", "Unknown error") if response else "No response from worker"
        logger.error(f"Failed to queue observation task: {error_msg}")
        return {"status": "error", "message": error_msg}


def queue_summary_task(
    session_id: str,
    priority: int = 10  # Summaries have higher priority by default
) -> Dict[str, Any]:
    """
    Queue summary task for processing.

    Args:
        session_id: Session identifier
        priority: Task priority (higher = more important)

    Returns:
        dict: Response with keys:
            - status: 'queued' | 'error'
            - task_id: str (if queued)
            - queue_position: int (if queued)
            - message: str (if error)
    """
    # Ensure worker is running
    if not ensure_worker_running():
        return {"status": "error", "message": "Failed to start worker"}

    port = get_worker_port()
    url = f"http://localhost:{port}/worker/queue"

    data = {
        "task_type": "summary",
        "session_id": session_id,
        "priority": priority,
    }

    response = _http_post(url, data)

    if response and response.get("status") == "queued":
        logger.info(f"Summary task queued: {response['task_id']}")
        return response
    else:
        error_msg = response.get("message", "Unknown error") if response else "No response from worker"
        logger.error(f"Failed to queue summary task: {error_msg}")
        return {"status": "error", "message": error_msg}


def request_worker_shutdown() -> bool:
    """
    Request worker to shut down gracefully.

    Returns:
        bool: True if shutdown request sent successfully
    """
    if not is_worker_running():
        logger.debug("Worker not running, no shutdown needed")
        return True

    port = get_worker_port()
    url = f"http://localhost:{port}/worker/shutdown"

    response = _http_post(url, {})

    if response:
        logger.info(f"Shutdown requested: pending={response.get('pending_tasks')}, running={response.get('running_tasks')}")
        return True
    else:
        logger.error("Failed to request worker shutdown")
        return False


def request_worker_shutdown_after_session() -> bool:
    """
    Request worker to shut down after processing current tasks.
    Called when session ends - worker will shutdown immediately after queue is empty.

    Returns:
        bool: True if shutdown request sent successfully
    """
    if not is_worker_process_running():
        logger.debug("Worker process not running, no shutdown needed")
        return True

    port = get_worker_port()
    url = f"http://localhost:{port}/worker/shutdown-after-session"

    response = _http_post(url, {})

    if response:
        logger.info(f"Session end shutdown requested: pending={response.get('pending_tasks')}, running={response.get('running_tasks')}")
        return True
    else:
        logger.error("Failed to request session end shutdown")
        return False


def force_worker_shutdown() -> bool:
    """
    Request graceful worker shutdown via HTTP.

    With FastAPI integration, we send a shutdown request rather than
    force killing a process.

    Returns:
        bool: True if shutdown request was sent successfully
    """
    return request_worker_shutdown()
