"""
Cursor SessionEnd Handler

Handles the sessionEnd hook, fired when a composer conversation ends.
Fire-and-forget per hooks.md - the response is logged but not used.

Field mapping:
  ended_at     <- stamped locally, not from the hook's duration_ms. A real
                  capture showed duration_ms=0 for a session that ran for
                  hours, so it isn't trustworthy - real duration is
                  ended_at - SESSION.created_at instead.
  end_reason   <- reason ("completed"/"aborted"/"error"/"window_close"/"user_close")
  final_status <- final_status
"""

import json

from src.common.logging import get_logger, setup_logging
from src.common.time_utils import get_now_ist_iso
from src.cursor.utils import obs_state
from src.cursor.utils.hook_io import debug, read_stdin_json
from src.cursor.utils.paths import get_cursor_logs_dir
from src.db.writers import DatabaseWriter


logger = get_logger(__name__)


def handle_session_end() -> None:
    """Handle Cursor's sessionEnd hook: persist SESSION end fields."""
    debug("sessionEnd handler triggered")
    setup_logging(log_to_file=True, log_to_console=False, log_dir=get_cursor_logs_dir())
    logger.info("=== Cursor SessionEnd Handler ===")

    try:
        hook_data = read_stdin_json()
        logger.info(f"sessionEnd full payload: {json.dumps(hook_data, default=str)}")
        debug(f"payload keys: {list(hook_data.keys())}")

        session_id = hook_data.get("session_id") or hook_data.get("conversation_id")

        if not session_id:
            logger.warning(f"Incomplete sessionEnd payload - session_id={session_id!r}. Skipping write.")
        else:
            from src.db.manager import get_db_connection
            from src.db.schema import migrate_schema
            migrate_schema(get_db_connection())

            written = DatabaseWriter().update_session_end(
                session_id=session_id,
                ended_at=get_now_ist_iso(),
                end_reason=hook_data.get("reason"),
                final_status=hook_data.get("final_status"),
            )

            if written:
                debug(f"session end stored - session_id={session_id}")
                logger.info(f"Cursor session end stored: session_id={session_id}")
            else:
                logger.warning(f"Cursor session end write failed - session_id={session_id}")

        obs_state.delete_session(session_id)

    except Exception as e:
        debug(f"ERROR - {e}")
        logger.error(f"Error in Cursor SessionEnd handler: {e}", exc_info=True)

    print(json.dumps({}))


def main() -> None:
    handle_session_end()


if __name__ == "__main__":
    main()
