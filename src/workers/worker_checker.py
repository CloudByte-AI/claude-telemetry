"""
Worker health check and auto-start utility.

Quickly checks if worker is running and starts it if needed.
"""
import socket
import sys
from pathlib import Path

# Add src to path
src_path = Path(__file__).parent.parent
sys.path.insert(0, str(src_path))

from src.common.logging import get_logger

logger = get_logger(__name__)


def is_port_open(host: str = "localhost", port: int = 8765, timeout: float = 0.5) -> bool:
    """
    Quick check if a port is open.

    Args:
        host: Host to check
        port: Port to check
        timeout: Connection timeout in seconds

    Returns:
        bool: True if port is open, False otherwise
    """
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((host, port))
        sock.close()
        return result == 0
    except Exception:
        return False


def ensure_worker_quick() -> bool:
    """
    Quick check and start worker if needed.

    Uses port check first (fast), then HTTP check if port is open.
    Starts worker in background if not running.

    Returns:
        bool: True if worker is running, False otherwise
    """
    # Quick port check
    if not is_port_open():
        logger.info("Port 8765 not open, starting worker...")
        try:
            from src.workers.llm_client import ensure_worker_running
            return ensure_worker_running()
        except Exception as e:
            logger.warning(f"Failed to start worker: {e}")
            return False

    # Port is open, verify worker is actually responding
    try:
        from src.workers.llm_client import is_worker_running
        if is_worker_running():
            logger.debug("Worker is running and healthy")
            return True
        else:
            logger.warning("Port 8765 is open but worker not responding, trying to start...")
            from src.workers.llm_client import ensure_worker_running
            return ensure_worker_running()
    except Exception as e:
        logger.debug(f"Worker check failed (might be starting up): {e}")
        return True  # Assume it's starting up


def ensure_worker_quick_sync() -> bool:
    """
    Synchronous version of ensure_worker_quick for use in hooks.

    This version doesn't wait for worker to fully start, just initiates
    the startup if needed and returns immediately.

    Returns:
        bool: True if worker was already running, False if it needs to start
    """
    if is_port_open():
        return True

    # Start worker in background without waiting
    logger.info("Worker not running, starting in background...")
    try:
        import subprocess
        import os
        project_dir = Path(__file__).parent.parent.parent

        if sys.platform == "win32":
            # Windows: Use start /B to run in background without new window
            cmd = f'start /B "" uv run --directory "{project_dir}" uvicorn src.app.app:app --host 0.0.0.0 --port 8765 >nul 2>&1'
            subprocess.Popen(
                cmd,
                shell=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        else:
            subprocess.Popen(
                ["uv", "run", "--directory", str(project_dir),
                 "uvicorn", "src.app.app:app", "--host", "0.0.0.0", "--port", "8765"],
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        return False
    except Exception as e:
        logger.warning(f"Failed to start worker: {e}")
        return False


if __name__ == "__main__":
    # Test the checker
    import time
    print("Checking worker status...")
    if ensure_worker_quick():
        print("✓ Worker is running")
    else:
        print("✗ Worker failed to start")
