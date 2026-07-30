"""
SessionEnd Handler

Called when a Claude Code session ends.
Recovers any prompt/response pairs the stop hook missed, then shuts down the
shared worker/dashboard if no other session (Claude Code or Cursor) needs it.
"""

import json
import os
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.common.logging import get_logger, setup_logging
from src.common.paths import get_claude_logs_dir


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
    2. Recover any prompt/response pairs the stop hook missed
    3. Shut down the shared worker/dashboard if no other session needs it
    """
    setup_logging(log_to_file=True, log_to_console=False, log_dir=get_claude_logs_dir())
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

        # ── Fallback: recover any missed stop-hook pairs ─────────────────────
        # Covers the case where the user denied a tool / interrupted on the
        # last turn of the session, preventing the stop hook from firing.
        try:
            from src.db.manager import get_db_connection
            _conn = get_db_connection()
            _row = _conn.cursor().execute(
                "SELECT cwd FROM SESSION WHERE session_id = ? LIMIT 1", (session_id,)
            ).fetchone()
            if _row and _row[0]:
                from src.core.recovery import process_missed_pairs
                _counts = process_missed_pairs(session_id, _row[0])
                _total = _counts.get("pass1", 0) + _counts.get("pass2", 0)
                if _total > 0:
                    logger.info(
                        f"SessionEnd recovery: pass1={_counts.get('pass1',0)} pass2={_counts.get('pass2',0)}"
                    )
        except Exception as _re:
            logger.warning(f"SessionEnd missed-pair recovery failed: {_re}")

        # Kill worker process - but only if no other session (this or another
        # Claude Code window, or a Cursor session) is still relying on the
        # shared worker/dashboard at localhost:4723. See
        # src/common/session_registry.py and shutdown_worker_if_no_active_sessions().
        logger.info("🚀 Checking whether it's safe to shut down the shared worker...")

        # Returns False when another session is still active, so report what
        # actually happened rather than always claiming the worker was killed.
        shutdown_attempted = False
        try:
            from src.workers.kill_worker import shutdown_worker_if_no_active_sessions
            shutdown_attempted = shutdown_worker_if_no_active_sessions(session_id)
        except Exception as e:
            logger.error(f"❌ Failed to run kill_worker functions: {e}", exc_info=True)

        logger.info("Session end handler completing...")

        # Return response
        print(json.dumps({
            "status": "success",
            "session_id": session_id,
            "message": (
                "Session end logged. Worker shut down."
                if shutdown_attempted
                else "Session end logged. Worker left running for other active sessions."
            ),
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
