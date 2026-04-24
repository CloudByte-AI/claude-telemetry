"""
Dashboard Start Handler

Automatically starts the FastAPI dashboard when a Claude session starts.
Runs as a detached background process on port 8765.
"""

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.common.logging import get_logger, setup_logging
from src.common.paths import get_cloudbyte_dir


logger = get_logger(__name__)

DASHBOARD_PORT = 8765
PID_FILE = get_cloudbyte_dir() / "dashboard.pid"


def is_dashboard_running() -> bool:
    """Check if the dashboard is already running by checking PID file and port."""
    # Check PID file
    if PID_FILE.exists():
        try:
            pid_data = json.loads(PID_FILE.read_text())
            pid = pid_data.get("pid")
            if pid:
                # Check if process is running
                try:
                    os.kill(pid, 0)  # Signal 0 doesn't kill, just checks existence
                    logger.debug(f"Dashboard already running with PID: {pid}")
                    return True
                except OSError:
                    # Process not running, stale PID file
                    logger.debug(f"Stale PID file, process {pid} not running")
                    PID_FILE.unlink()
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(f"Invalid PID file: {e}")
            PID_FILE.unlink()

    # Check if port is in use
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1)
            result = s.connect_ex(("localhost", DASHBOARD_PORT))
            if result == 0:
                logger.warning(f"Port {DASHBOARD_PORT} is in use but no PID file found")
                return True
    except Exception as e:
        logger.debug(f"Port check error: {e}")

    return False


def start_dashboard():
    """Start the FastAPI dashboard as a background process."""
    logger.info("=== Dashboard Start Handler ===")

    try:
        # Check if already running
        if is_dashboard_running():
            logger.info("Dashboard is already running, skipping start")
            return {"status": "already_running", "port": DASHBOARD_PORT}

        # Get project directory
        project_dir = Path(__file__).parent.parent.parent
        logger.debug(f"Project directory: {project_dir}")

        # Start uvicorn as a detached subprocess
        logger.info(f"Starting FastAPI dashboard on port {DASHBOARD_PORT}...")

        # Use CREATE_NEW_PROCESS_GROUP on Windows, fork on Unix
        creation_flags = 0
        if sys.platform == "win32":
            creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP
            # On Windows, also set start to use CREATE_NEW_PROCESS_GROUP
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        else:
            startupinfo = None

        process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "src.app.app:app", "--host", "0.0.0.0", "--port", str(DASHBOARD_PORT)],
            cwd=project_dir,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creation_flags,
            start_new_session=True if sys.platform != "win32" else False,
        )

        logger.info(f"Dashboard started with PID: {process.pid}")

        # Write PID file
        pid_data = {
            "pid": process.pid,
            "port": DASHBOARD_PORT,
            "start_time": time.time(),
            "type": "dashboard",
        }
        PID_FILE.write_text(json.dumps(pid_data, indent=2))
        logger.info(f"PID file written: {PID_FILE}")

        # Give it a moment to start and check if it's still running
        time.sleep(1)
        if process.poll() is None:
            logger.info("✓ Dashboard is running")
            return {
                "status": "started",
                "pid": process.pid,
                "port": DASHBOARD_PORT,
                "url": f"http://localhost:{DASHBOARD_PORT}",
            }
        else:
            logger.error(f"Dashboard process exited immediately with code: {process.returncode}")
            PID_FILE.unlink(missing_ok=True)
            return {"status": "failed", "message": "Process exited immediately"}

    except Exception as e:
        logger.error(f"Failed to start dashboard: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}


def stop_dashboard():
    """Stop the dashboard by killing the process in the PID file."""
    logger.info("=== Dashboard Stop Handler ===")

    try:
        if not PID_FILE.exists():
            logger.info("No dashboard PID file found")
            return {"status": "not_running"}

        pid_data = json.loads(PID_FILE.read_text())
        pid = pid_data.get("pid")

        if not pid:
            logger.warning("Invalid PID file")
            PID_FILE.unlink()
            return {"status": "error", "message": "Invalid PID file"}

        # Kill the process
        try:
            os.kill(pid, 15)  # SIGTERM
            logger.info(f"Sent SIGTERM to dashboard process {pid}")
        except OSError:
            logger.debug(f"Process {pid} not running")

        PID_FILE.unlink(missing_ok=True)
        logger.info("✓ Dashboard stopped")
        return {"status": "stopped"}

    except Exception as e:
        logger.error(f"Failed to stop dashboard: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}


def main():
    """Main entry point."""
    setup_logging(log_to_file=True, log_to_console=True)

    if len(sys.argv) > 1 and sys.argv[1] == "stop":
        result = stop_dashboard()
    else:
        result = start_dashboard()

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
