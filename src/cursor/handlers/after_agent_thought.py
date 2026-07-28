"""
Cursor AfterAgentThought Handler

Handles the afterAgentThought hook, fired after the agent completes a
thinking block. Writes a THINKING row.

Real captures showed this hook can fire twice for the same thinking block:
once with the turn's plain generation_id (36 chars, a standard UUID),
once with a suffixed variant ("{plain_id}-{index}-{random}", always
longer than 36 chars) unique per thinking block. Which of the two actually
fires is inconsistent - sometimes both, sometimes only the suffixed one -
but the suffixed one is always present exactly once per real thinking
block, so that's the only one acted on.

Field mapping:
  thinking_id <- generation_id (full, suffixed) - already unique
  prompt_id   <- generation_id[:36] - the plain base UUID, same value
                 beforeSubmitPrompt used as USER_PROMPT.prompt_id
  content     <- text, repaired via repair_text()
  duration_ms <- duration_ms
  timestamp   <- stamped locally
  signature, uuid, parent_uuid: left NULL - no Cursor equivalent.

Any firing whose generation_id is 36 characters or fewer (no suffix) is
skipped entirely - it's either the plain duplicate or an incomplete payload.
"""

import json

from src.common.logging import get_logger, setup_logging
from src.common.time_utils import get_now_ist_iso
from src.cursor.utils.hook_io import debug, read_stdin_json, repair_text
from src.cursor.utils.paths import get_cursor_logs_dir
from src.db.writers import DatabaseWriter


logger = get_logger(__name__)

_PLAIN_UUID_LENGTH = 36


def handle_after_agent_thought() -> None:
    """Handle Cursor's afterAgentThought hook: persist a THINKING row."""
    debug("afterAgentThought handler triggered")
    setup_logging(log_to_file=True, log_to_console=False, log_dir=get_cursor_logs_dir())
    logger.info("=== Cursor AfterAgentThought Handler ===")

    try:
        hook_data = read_stdin_json()
        logger.info(f"afterAgentThought full payload: {json.dumps(hook_data, default=str)}")
        debug(f"payload keys: {list(hook_data.keys())}")

        thinking_id = hook_data.get("generation_id")
        content = repair_text(hook_data.get("text"))

        if not thinking_id or len(thinking_id) <= _PLAIN_UUID_LENGTH:
            debug(f"skipped - no suffix on generation_id={thinking_id!r}")
            logger.info(f"afterAgentThought skipped - no suffix on generation_id={thinking_id!r}")
        elif not content:
            logger.warning("afterAgentThought has no text. Skipping write.")
        else:
            prompt_id = thinking_id[:_PLAIN_UUID_LENGTH]

            from src.db.manager import get_db_connection
            from src.db.schema import migrate_schema
            migrate_schema(get_db_connection())

            written = DatabaseWriter().write_thinking({
                "thinking_id": thinking_id,
                "prompt_id": prompt_id,
                "content": content,
                "duration_ms": hook_data.get("duration_ms"),
                "timestamp": get_now_ist_iso(),
            })

            if written:
                debug(f"thinking stored - thinking_id={thinking_id}, prompt_id={prompt_id}")
                logger.info(f"Cursor thinking stored: thinking_id={thinking_id}, prompt_id={prompt_id}")
            else:
                logger.warning(f"Cursor thinking write failed - thinking_id={thinking_id}")

    except Exception as e:
        debug(f"ERROR - {e}")
        logger.error(f"Error in Cursor AfterAgentThought handler: {e}", exc_info=True)

    print(json.dumps({}))


def main() -> None:
    handle_after_agent_thought()


if __name__ == "__main__":
    main()
