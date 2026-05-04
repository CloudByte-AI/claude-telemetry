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

# Add project root to path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

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


def kill_all_claude_telemetry_processes():
    """Kill all processes related to claude-telemetry plugin."""
    logger.info("Attempting to kill all claude-telemetry related processes...")

    if sys.platform == "win32":
        import subprocess
        killed_processes = []

        try:
            # Get all processes with claude-telemetry in their command line
            result = subprocess.run(
                ["Get-WmiObject", "Win32_Process", "|", "Where-Object", "{", "$_.CommandLine", "-like", "*claude-telemetry*", "}", "|", "Select-Object", "-ExpandProperty", "ProcessId"],
                capture_output=True,
                text=True,
                shell=True,
                creationflags=subprocess.CREATE_NO_WINDOW
            )

            # PowerShell approach
            ps_command = '''
            Get-WmiObject Win32_Process | Where-Object {
                $_.CommandLine -like "*claude-telemetry*"
            } | Select-Object -ExpandProperty ProcessId
            '''

            result = subprocess.run(
                ["powershell", "-Command", ps_command],
                capture_output=True,
                text=True,
                creationflags=subprocess.CREATE_NO_WINDOW
            )

            if result.stdout.strip():
                pids = result.stdout.strip().split('\n')
                pids = [p.strip() for p in pids if p.strip() and p.strip().isdigit()]

                logger.info(f"Found {len(pids)} claude-telemetry process(es): {pids}")

                for pid in pids:
                    try:
                        kill_result = subprocess.run(
                            ["taskkill", "/F", "/PID", pid],
                            capture_output=True,
                            text=True,
                            creationflags=subprocess.CREATE_NO_WINDOW
                        )
                        if kill_result.returncode == 0:
                            logger.info(f"Killed process {pid}")
                            killed_processes.append(pid)
                        else:
                            logger.warning(f"Failed to kill process {pid}: {kill_result.stderr}")
                    except Exception as e:
                        logger.warning(f"Error killing process {pid}: {e}")

                # Verify all processes are dead
                if killed_processes:
                    time.sleep(0.5)
                    verify_result = subprocess.run(
                        ["powershell", "-Command", ps_command],
                        capture_output=True,
                        text=True,
                        creationflags=subprocess.CREATE_NO_WINDOW
                    )

                    remaining_pids = [p.strip() for p in verify_result.stdout.strip().split('\n') if p.strip() and p.strip().isdigit()]
                    if remaining_pids:
                        logger.warning(f"Some processes still alive: {remaining_pids}")
                    else:
                        logger.info("All claude-telemetry processes killed successfully")

                return len(killed_processes) > 0
            else:
                logger.debug("No claude-telemetry processes found")
                return False

        except Exception as e:
            logger.error(f"Error killing claude-telemetry processes: {e}")
            return False
    else:
        # Unix-like systems
        import subprocess
        killed_processes = []

        try:
            # Find processes with claude-telemetry in command line
            result = subprocess.run(
                ["pgrep", "-f", "claude-telemetry"],
                capture_output=True,
                text=True
            )

            if result.stdout.strip():
                pids = result.stdout.strip().split('\n')
                pids = [p.strip() for p in pids if p.strip()]

                logger.info(f"Found {len(pids)} claude-telemetry process(es): {pids}")

                for pid in pids:
                    try:
                        kill_result = subprocess.run(
                            ["kill", "-9", pid],
                            capture_output=True,
                            text=True
                        )
                        if kill_result.returncode == 0:
                            logger.info(f"Killed process {pid}")
                            killed_processes.append(pid)
                        else:
                            logger.warning(f"Failed to kill process {pid}")
                    except Exception as e:
                        logger.warning(f"Error killing process {pid}: {e}")

                # Verify all processes are dead
                if killed_processes:
                    time.sleep(0.5)
                    verify_result = subprocess.run(
                        ["pgrep", "-f", "claude-telemetry"],
                        capture_output=True,
                        text=True
                    )

                    remaining_pids = [p.strip() for p in verify_result.stdout.strip().split('\n') if p.strip()]
                    if remaining_pids:
                        logger.warning(f"Some processes still alive: {remaining_pids}")
                    else:
                        logger.info("All claude-telemetry processes killed successfully")

                return len(killed_processes) > 0
            else:
                logger.debug("No claude-telemetry processes found")
                return False

        except Exception as e:
            logger.error(f"Error killing claude-telemetry processes: {e}")
            return False


def kill_uv_process():
    """Kill the uv package manager process."""
    logger.info("Attempting to kill uv process...")

    if sys.platform == "win32":
        import subprocess
        try:
            # Try to kill uv.exe on Windows
            result = subprocess.run(
                ["taskkill", "/F", "/IM", "uv.exe"],
                capture_output=True,
                text=True,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            if result.returncode == 0:
                logger.info("Successfully killed uv.exe process")
                return True
            else:
                # uv.exe might not be running
                logger.debug(f"uv.exe not running or could not be killed")
                return False
        except Exception as e:
            logger.warning(f"Error killing uv.exe: {e}")
            return False
    else:
        # Unix-like systems
        import subprocess
        try:
            # Try to kill uv process on Unix
            # Use pkill to match process name
            result = subprocess.run(
                ["pkill", "-f", "uv"],
                capture_output=True,
                text=True
            )
            if result.returncode == 0:
                logger.info("Successfully killed uv process")
                return True
            else:
                # uv might not be running
                logger.debug(f"uv not running or could not be killed")
                return False
        except FileNotFoundError:
            # pkill not available, try alternative method
            try:
                result = subprocess.run(
                    ["pgrep", "-f", "uv"],
                    capture_output=True,
                    text=True
                )
                pids = result.stdout.strip().split('\n') if result.stdout.strip() else []
                pids = [p for p in pids if p]

                if pids:
                    for pid in pids:
                        subprocess.run(["kill", "-9", pid], capture_output=True)
                    logger.info(f"Killed {len(pids)} uv process(es)")
                    return True
                else:
                    logger.debug("No uv process found")
                    return False
            except Exception as e:
                logger.warning(f"Error killing uv process: {e}")
                return False
        except Exception as e:
            logger.warning(f"Error killing uv process: {e}")
            return False


if __name__ == "__main__":
    logger.info("=== Kill Worker Script ===")

    # Step 1: Try killing by PID first
    killed = kill_worker_by_pid()

    # Step 2: Fallback to killing by port
    if not killed:
        logger.info("Trying to kill by port...")
        killed = kill_worker_by_port()

    # Step 3: Kill all claude-telemetry related processes (comprehensive cleanup)
    logger.info("Step 3: Performing comprehensive cleanup of all claude-telemetry processes...")
    all_killed = kill_all_claude_telemetry_processes()
    if all_killed:
        logger.info("All claude-telemetry processes killed successfully")
        killed = True  # Update status if comprehensive cleanup succeeded

    # Step 4: Kill any remaining uv.exe/uv processes
    logger.info("Step 4: Killing any remaining uv processes...")
    uv_killed = kill_uv_process()
    if uv_killed:
        logger.info("uv process killed successfully")

    if killed or all_killed or uv_killed:
        logger.info("Cleanup completed successfully")
        sys.exit(0)
    else:
        logger.warning("No processes were killed")
        sys.exit(1)
