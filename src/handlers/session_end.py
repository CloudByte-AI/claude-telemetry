"""
SessionEnd Handler

Called when a Claude Code session ends.
Queues summary generation task for background worker.
"""

import json
import os
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.common.logging import get_logger, setup_logging
from src.common.paths import get_config_file
from src.common.file_io import read_json
from src.integrations.llm.db_helpers import get_all_observations


logger = get_logger(__name__)


def read_stdin_data() -> dict:
    """
    Read hook data from stdin.

    Claude Code passes hook data via stdin.
    Expected format: JSON with session_id, etc.

    Returns:
        dict: Parsed hook data
    """
    try:
        data = sys.stdin.read().strip()
        if data:
            return json.loads(data)
    except json.JSONDecodeError as e:
        logger.error(f"Error parsing stdin JSON: {e}")

    return {}


def handle_session_end():
    """
    Handle the SessionEnd hook with summary generation.

    Expected stdin data (JSON):
    {
        "session_id": "uuid"
    }

    Process:
    1. Read session_id from stdin
    2. Queue summary task for background processing
    3. Kill worker process
    """
    setup_logging(log_to_file=True, log_to_console=False)
    logger.info("=== SessionEnd Handler ===")

    try:
        # Read hook data from stdin
        hook_data = read_stdin_data()
        logger.info(f"Hook data received: {hook_data}")

        # Extract session data
        session_id = hook_data.get("session_id") or os.environ.get("CLAUDE_SESSION_ID")

        logger.info(f"Session end: session_id={session_id}")

        if not session_id:
            logger.warning("No session_id provided")
            print(json.dumps({"status": "error", "message": "No session_id"}))
            return

        # Check if summaries are enabled
        config_file = get_config_file()
        if config_file.exists():
            config = read_json(config_file)
            settings = config.get("settings", {})

            if not settings.get("enable_summaries", True):
                logger.info("Summaries disabled in config")
                print(json.dumps({
                    "status": "success",
                    "session_id": session_id,
                    "message": "Session end logged. Summaries disabled.",
                }))
                return
        else:
            logger.warning("Config file not found, using default settings")

        # Fetch all observations for this session (for logging only)
        observations = get_all_observations(session_id)
        logger.info(f"Found {len(observations)} observations for session {session_id}")

        # Check task queue status for pending/failed tasks
        try:
            from src.db.manager import get_db_manager
            db = get_db_manager()
            task_status = db.execute("""
                SELECT
                    COUNT(CASE WHEN status = 'pending' THEN 1 END) AS pending,
                    COUNT(CASE WHEN status = 'running' THEN 1 END) AS running,
                    COUNT(CASE WHEN status = 'failed' THEN 1 END) AS failed
                FROM TASK_QUEUE
                WHERE session_id = ?
            """, (session_id,)).fetchone()

            if task_status:
                pending = task_status[0] or 0
                running = task_status[1] or 0
                failed = task_status[2] or 0

                logger.info(f"Task queue status: pending={pending}, running={running}, failed={failed}")

                if pending > 0 or running > 0 or failed > 0:
                    logger.warning(f"Session {session_id} has incomplete tasks: pending={pending}, running={running}, failed={failed}")
        except Exception as e:
            logger.warning(f"Could not check task queue status: {e}")

        # Kill worker process - DIRECT execution only
        logger.info("🚀 Starting worker shutdown...")

        try:
            # Import kill_worker functions directly
            from src.workers.kill_worker import (
                kill_worker_by_pid,
                kill_worker_by_port,
                kill_all_claude_telemetry_processes,
                kill_uv_process
            )

            # Step 1: Try killing by PID first
            logger.info("Step 1: Killing worker by PID...")
            killed = kill_worker_by_pid()
            logger.info(f"✓ Kill by PID result: {killed}")

            # Step 2: Fallback to killing by port
            if not killed:
                logger.info("Step 2: Trying to kill by port...")
                killed = kill_worker_by_port()
                logger.info(f"✓ Kill by port result: {killed}")

            # Step 3: Comprehensive cleanup of all claude-telemetry processes
            logger.info("Step 3: Performing comprehensive cleanup of all processes...")
            all_killed = kill_all_claude_telemetry_processes()
            logger.info(f"✓ Comprehensive cleanup result: {all_killed}")

            # Step 4: Kill uv process
            logger.info("Step 4: Killing uv process...")
            uv_killed = kill_uv_process()
            logger.info(f"✓ Kill uv result: {uv_killed}")

            # Overall result
            if killed or all_killed or uv_killed:
                logger.info("✅ Worker cleanup completed successfully")
            else:
                logger.warning("⚠️ No processes were killed")

        except Exception as e:
            logger.error(f"❌ Failed to run kill_worker functions: {e}", exc_info=True)

        logger.info("Session end handler completing...")

        # Return response
        print(json.dumps({
            "status": "success",
            "session_id": session_id,
            "message": "Session end logged. Worker killed.",
        }))

        logger.info("Session end handler completed successfully")

    except Exception as e:
        logger.error(f"Error in SessionEnd handler: {e}", exc_info=True)
        print(json.dumps({"status": "error", "message": str(e)}))
        sys.exit(1)


def main():
    """Main entry point for the handler."""
    handle_session_end()


if __name__ == "__main__":
    main()
