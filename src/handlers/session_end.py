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

        # Kill worker process
        logger.info("Killing worker process...")
        import subprocess

        # Run kill_worker.py script (it's in the project root, not src/)
        try:
            project_root = Path(__file__).parent.parent.parent
            kill_script = project_root / "kill_worker.py"
            logger.info(f"Running kill_worker.py from: {kill_script}")
            result = subprocess.run(
                [sys.executable, str(kill_script)],
                capture_output=True,
                text=True,
                cwd=project_root
            )
            logger.info(f"kill_worker.py output: {result.stdout}")
            if result.returncode != 0:
                logger.warning(f"kill_worker.py failed: {result.stderr}")
        except Exception as e:
            logger.warning(f"Failed to run kill_worker.py: {e}", exc_info=True)

        logger.info("Session end handler completing...")

        # Central sync — full session flush (picks up anything Stop missed)
        try:
            from src.sync.runner import run_sync
            run_sync(session_id=session_id, mode="session_end")
        except Exception as sync_err:
            logger.warning(f"Central sync skipped in session_end: {sync_err}")

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
