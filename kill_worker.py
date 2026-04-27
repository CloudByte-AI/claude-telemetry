"""
Kill the CloudByte worker process by PID.

This script reads the PID from .cloudbyte/worker.pid and kills the process.
Can be run manually or called from session_end handler.
"""
import sys
import json
import os
from pathlib import Path

# Add src to path
src_path = Path(__file__).parent / "src"
sys.path.insert(0, str(src_path))

from src.common.paths import get_cloudbyte_dir
from src.common.logging import get_logger, setup_logging

setup_logging(log_to_file=True, log_to_console=True)
logger = get_logger(__name__)


def kill_worker_by_pid():
    """Kill worker process using PID from worker.pid file."""
    pid_file = get_cloudbyte_dir() / "worker.pid"

    if not pid_file.exists():
        logger.info("No worker.pid file found - worker may not be running")
        return False

    try:
        # Read PID from file
        pid_data = json.loads(pid_file.read_text())
        worker_pid = pid_data.get("pid")

        if not worker_pid:
            logger.warning("No PID found in worker.pid file")
            return False

        logger.info(f"Found worker PID: {worker_pid}")

        # Check if process is running
        if sys.platform == "win32":
            import subprocess
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {worker_pid}"],
                capture_output=True,
                text=True,
                creationflags=subprocess.CREATE_NO_WINDOW
            )

            if str(worker_pid) not in result.stdout:
                logger.info(f"Process {worker_pid} is not running")
                # Remove stale PID file
                pid_file.unlink()
                return False

            # Kill the process
            logger.info(f"Killing worker process {worker_pid}...")
            result = subprocess.run(
                ["taskkill", "/F", "/PID", str(worker_pid)],
                capture_output=True,
                text=True,
                creationflags=subprocess.CREATE_NO_WINDOW
            )

            if result.returncode == 0:
                logger.info(f"Successfully killed worker process {worker_pid}")
                # Remove PID file
                try:
                    pid_file.unlink()
                    logger.info("Removed worker.pid file")
                except:
                    pass
                return True
            else:
                logger.error(f"Failed to kill process: {result.stderr}")
                return False

        else:
            # Unix-like systems
            import signal
            try:
                os.kill(worker_pid, 0)  # Check if process exists
            except OSError:
                logger.info(f"Process {worker_pid} is not running")
                pid_file.unlink()
                return False

            # Kill the process
            logger.info(f"Killing worker process {worker_pid}...")
            os.kill(worker_pid, signal.SIGTERM)

            try:
                pid_file.unlink()
                logger.info("Removed worker.pid file")
            except:
                pass

            return True

    except Exception as e:
        logger.error(f"Error killing worker: {e}", exc_info=True)
        return False


def kill_worker_by_port():
    """Kill any process listening on port 8765."""
    logger.info("Checking for processes on port 8765...")

    if sys.platform == "win32":
        import subprocess
        result = subprocess.run(
            ["netstat", "-ano"],
            capture_output=True,
            text=True,
            creationflags=subprocess.CREATE_NO_WINDOW
        )

        killed = False
        for line in result.stdout.splitlines():
            if ":8765" in line and "LISTENING" in line:
                parts = line.split()
                if len(parts) >= 5:
                    pid = parts[-1]
                    if pid.isdigit():
                        logger.info(f"Killing process {pid} on port 8765")
                        result = subprocess.run(
                            ["taskkill", "/F", "/PID", pid],
                            capture_output=True,
                            creationflags=subprocess.CREATE_NO_WINDOW
                        )
                        if result.returncode == 0:
                            killed = True
                            logger.info(f"Killed process {pid}")

        return killed
    else:
        # Unix-like systems
        import subprocess
        result = subprocess.run(
            ["lsof", "-ti", ":8765"],
            capture_output=True,
            text=True
        )

        if result.stdout.strip():
            for pid in result.stdout.strip().split('\n'):
                logger.info(f"Killing process {pid} on port 8765")
                subprocess.run(["kill", "-9", pid])
            return True
        return False


if __name__ == "__main__":
    logger.info("=== Kill Worker Script ===")

    # Try killing by PID first
    killed = kill_worker_by_pid()

    # Fallback to killing by port
    if not killed:
        logger.info("Trying to kill by port...")
        killed = kill_worker_by_port()

    if killed:
        logger.info("Worker killed successfully")
        sys.exit(0)
    else:
        logger.warning("No worker was killed")
        sys.exit(1)
