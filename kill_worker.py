"""
Kill the CloudByte worker process by PID.

This script reads the PID from .cloudbyte/worker.pid and kills the process.
Can be run manually or called from session_end handler.
Enhanced with retry logic and verification.
"""
import sys
import json
import os
import time
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

            # Kill the process with retry
            for attempt in range(3):
                logger.info(f"Killing worker process {worker_pid} (attempt {attempt + 1})...")
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

                    # Verify it's actually dead
                    time.sleep(0.5)
                    verify = subprocess.run(
                        ["tasklist", "/FI", f"PID eq {worker_pid}"],
                        capture_output=True,
                        text=True,
                        creationflags=subprocess.CREATE_NO_WINDOW
                    )
                    if str(worker_pid) not in verify.stdout:
                        return True
                    else:
                        logger.warning(f"Process {worker_pid} still running, retrying...")
                else:
                    logger.error(f"Failed to kill process: {result.stderr}")
                    if attempt < 2:
                        time.sleep(1)
                        continue

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

            # Kill the process with retry
            for attempt in range(3):
                logger.info(f"Killing worker process {worker_pid} (attempt {attempt + 1})...")
                try:
                    os.kill(worker_pid, signal.SIGTERM)
                    time.sleep(0.5)

                    # Verify it's dead
                    try:
                        os.kill(worker_pid, 0)
                        # Still alive, try SIGKILL
                        if attempt < 2:
                            logger.info("Process still alive, trying SIGKILL...")
                            os.kill(worker_pid, signal.SIGKILL)
                            time.sleep(0.5)
                            continue
                        else:
                            logger.warning("Process survived SIGKILL, may be zombie")
                    except OSError:
                        # Process is dead
                        try:
                            pid_file.unlink()
                            logger.info("Removed worker.pid file")
                        except:
                            pass
                        return True
                except OSError as e:
                    logger.error(f"Failed to kill process: {e}")
                    if attempt < 2:
                        time.sleep(1)
                        continue

            return False

    except Exception as e:
        logger.error(f"Error killing worker: {e}", exc_info=True)
        return False


def kill_worker_by_port():
    """Kill any process listening on port 8765 with retry logic."""
    logger.info("Checking for processes on port 8765...")

    if sys.platform == "win32":
        import subprocess

        killed = False
        for attempt in range(3):  # Retry up to 3 times
            result = subprocess.run(
                ["netstat", "-ano"],
                capture_output=True,
                text=True,
                creationflags=subprocess.CREATE_NO_WINDOW
            )

            pids_found = []
            for line in result.stdout.splitlines():
                if ":8765" in line and "LISTENING" in line:
                    parts = line.split()
                    if len(parts) >= 5:
                        pid = parts[-1]
                        if pid.isdigit() and pid not in pids_found:
                            pids_found.append(pid)

            if not pids_found:
                logger.info("No processes found on port 8765")
                killed = True
                break

            logger.info(f"Attempt {attempt + 1}: Found {len(pids_found)} process(es) on port 8765")

            for pid in pids_found:
                logger.info(f"Killing process {pid} on port 8765...")
                result = subprocess.run(
                    ["taskkill", "/F", "/PID", pid],
                    capture_output=True,
                    text=True,
                    creationflags=subprocess.CREATE_NO_WINDOW
                )
                if result.returncode == 0:
                    logger.info(f"Killed process {pid}")
                    killed = True
                else:
                    logger.warning(f"Failed to kill process {pid}: {result.stderr}")

            if killed:
                # Wait and verify
                time.sleep(0.5)
                verify = subprocess.run(
                    ["netstat", "-ano"],
                    capture_output=True,
                    text=True,
                    creationflags=subprocess.CREATE_NO_WINDOW
                )
                if ":8765" not in verify.stdout:
                    logger.info("Port 8765 is now free")
                    break
                else:
                    logger.warning(f"Port 8765 still in use, retrying...")
                    killed = False
            else:
                break

        return killed
    else:
        # Unix-like systems
        import subprocess

        killed = False
        for attempt in range(3):  # Retry up to 3 times
            result = subprocess.run(
                ["lsof", "-ti", ":8765"],
                capture_output=True,
                text=True
            )

            pids = result.stdout.strip().split('\n') if result.stdout.strip() else []
            pids = [p for p in pids if p]  # Remove empty strings

            if not pids:
                logger.info("No processes found on port 8765")
                killed = True
                break

            logger.info(f"Attempt {attempt + 1}: Found {len(pids)} process(es) on port 8765")

            for pid in pids:
                logger.info(f"Killing process {pid} on port 8765")
                subprocess.run(["kill", "-9", pid], capture_output=True)
                killed = True

            if killed:
                # Wait and verify
                time.sleep(0.5)
                verify = subprocess.run(
                    ["lsof", "-ti", ":8765"],
                    capture_output=True,
                    text=True
                )
                if not verify.stdout.strip():
                    logger.info("Port 8765 is now free")
                    break
                else:
                    logger.warning(f"Port 8765 still in use, retrying...")
                    killed = False
            else:
                break

        return killed


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
