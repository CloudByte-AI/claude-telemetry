"""
Dashboard Process Launcher (blocking variant)

Starts the FastAPI dashboard (src.app.app) on localhost:4723 and waits for it
to answer, plus resolves which port an already-running instance is on.

Named `llm_client` for historical reasons - it used to be the HTTP client that
submitted observation/summary tasks to a background LLM worker. That worker and
its task queue are gone; what's left is purely dashboard process lifecycle.

The module path is kept as-is because both plugins import from it directly:
    src/handlers/session_start.py          -> ensure_worker_running()
    src/cursor/handlers/session_start.py   -> ensure_worker_running()
Renaming this file means editing the Cursor adapter, which is deliberately out
of scope. See context/claude-plugin-enhancement/00-refactor-overview.md §3.

Relationship to worker_checker:
    worker_checker.ensure_worker_quick_sync()  spawns and returns immediately.
    ensure_worker_running() (here)             spawns via that same function,
                                               then blocks until the port
                                               answers (or times out).
Both plugins call quick_sync first and this second, so in practice this one
usually short-circuits on the port check. Sharing one spawn implementation
means there is a single place where the detach semantics have to be right.

Teardown lives in kill_worker.shutdown_worker_if_no_active_sessions().
"""

import json
import sys
import time
from pathlib import Path

# Add src directory to path for imports
# This script is in src/workers/, so we need to add src/ to path
src_dir = Path(__file__).parent.parent
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

from src.common.logging import get_logger
from src.common.paths import get_cloudbyte_dir


logger = get_logger(__name__)


# Default dashboard port. Hardcoded rather than read from config.json's
# worker.port - that setting has never been wired to anything.
DEFAULT_WORKER_PORT = 4723

# How long ensure_worker_running() waits for the port to come up.
STARTUP_TIMEOUT_SECONDS = 30


def get_worker_port() -> int:
    """
    Get the dashboard port from the PID file, or the default.

    Returns:
        int: Port number
    """
    pid_file = get_cloudbyte_dir() / "worker.pid"

    if pid_file.exists():
        try:
            pid_data = json.loads(pid_file.read_text())
            return pid_data.get("port", DEFAULT_WORKER_PORT)
        except (json.JSONDecodeError, IOError):
            pass

    return DEFAULT_WORKER_PORT


def ensure_worker_running() -> bool:
    """
    Start the FastAPI dashboard if it isn't already listening, then wait for it.

    Delegates the actual spawn to worker_checker.ensure_worker_quick_sync() so
    both entry points share one detach implementation - on Windows that means
    `start /B`, which is what reliably outlives the one-shot hook process that
    launched it.

    Returns:
        bool: True if the dashboard is listening (already was, or came up
        within STARTUP_TIMEOUT_SECONDS). False if it never answered.
    """
    from src.workers.worker_checker import ensure_worker_quick_sync, is_port_open

    if is_port_open():
        logger.debug("Dashboard already listening")
        return True

    logger.info("Starting FastAPI dashboard...")
    ensure_worker_quick_sync()

    deadline = time.time() + STARTUP_TIMEOUT_SECONDS
    while time.time() < deadline:
        if is_port_open():
            logger.info("FastAPI dashboard started successfully")
            return True
        time.sleep(1)

    logger.error(
        f"FastAPI dashboard did not answer on port {DEFAULT_WORKER_PORT} "
        f"within {STARTUP_TIMEOUT_SECONDS}s"
    )
    return False
