"""
Cursor Stop Handler

Called when the agent loop ends (hook: stop). Writes USER_PROMPT.status
from the hook's status field ('completed' / 'aborted').

Token fields (input_tokens/output_tokens/cache_read_tokens/cache_write_tokens),
present on this hook when status == 'completed', are NOT written yet - see
context/cursor-plugin-development/PROGRESS.md for the input_tokens
convention mismatch that needs resolving first.

The stop hook's OUTPUT can include a `followup_message`: if non-empty,
Cursor auto-submits it as the next user message (a loop-style flow). This
handler must NEVER set that field - always returns {} so it can't
accidentally trigger an auto follow-up loop.
"""

import json

from src.common.logging import get_logger, setup_logging
from src.cursor.utils.hook_io import debug, read_stdin_json
from src.cursor.utils.paths import get_cursor_logs_dir
from src.db.writers import DatabaseWriter


logger = get_logger(__name__)


def handle_stop() -> None:
    """Handle Cursor's stop hook: persist USER_PROMPT.status."""
    debug("stop handler triggered")
    setup_logging(log_to_file=True, log_to_console=False, log_dir=get_cursor_logs_dir())
    logger.info("=== Cursor Stop Handler ===")

    try:
        hook_data = read_stdin_json()
        logger.info(f"stop full payload: {json.dumps(hook_data, default=str)}")
        debug(f"payload keys: {list(hook_data.keys())}")

        prompt_id = hook_data.get("generation_id")
        status = hook_data.get("status")

        if not prompt_id or not status:
            logger.warning(
                f"Incomplete stop payload - generation_id={prompt_id!r}, "
                f"status={status!r}. Skipping write."
            )
        else:
            from src.db.manager import get_db_connection
            from src.db.schema import migrate_schema
            migrate_schema(get_db_connection())

            if DatabaseWriter().update_user_prompt_status(prompt_id, status):
                debug(f"status stored - prompt_id={prompt_id}, status={status}")
                logger.info(f"Cursor prompt status stored: prompt_id={prompt_id}, status={status}")
            else:
                debug(f"status NOT stored - prompt_id={prompt_id}, status={status}")
                logger.warning(f"Cursor prompt status write failed - prompt_id={prompt_id}")

    except Exception as e:
        debug(f"ERROR - {e}")
        logger.error(f"Error in Cursor Stop handler: {e}", exc_info=True)

    # Never set followup_message - see module docstring.
    print(json.dumps({}))


def main() -> None:
    handle_stop()


if __name__ == "__main__":
    main()
