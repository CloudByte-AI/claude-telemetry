"""
Cursor AfterAgentResponse Handler

Handles the afterAgentResponse hook, fired after the agent completes an
assistant message. Persists a RESPONSE row.

Field mapping:
  prompt_id     <- generation_id (matches the USER_PROMPT row
                    beforeSubmitPrompt already created for this turn)
  message_id    <- freshly generated UUID, not generation_id - it's
                    unconfirmed whether this hook can fire more than once
                    per generation_id, so reusing it as a primary key here
                    would risk a collision
  response_text <- text, repaired via repair_text() (see hook_io.py)
  model         <- model, falling back to model_id (model is more specific
                    in practice, e.g. "composer-2.5-fast" vs model_id's
                    "composer-2.5", and unlike model_id has no "optional"
                    caveat in hooks.md)
  timestamp     <- stamped locally
  uuid, parent_uuid: left NULL - no Cursor equivalent.

Also writes an IO_TOKENS row linked to the same prompt_id/message_id.
input_tokens needs adjusting first - see _compute_input_tokens().
"""

import json
import uuid as _uuid_mod

from src.common.logging import get_logger, setup_logging
from src.common.time_utils import get_now_ist_iso
from src.cursor.utils import obs_state
from src.cursor.utils.hook_io import debug, read_stdin_json, repair_text
from src.cursor.utils.paths import get_cursor_logs_dir
from src.db.writers import DatabaseWriter


logger = get_logger(__name__)


def _compute_input_tokens(total_input_tokens, cache_read_tokens):
    """
    IO_TOKENS.input_tokens is meant to exclude cache_read_tokens, but
    Cursor's hook reports input_tokens as a total that includes them - so
    subtract cache_read_tokens first. Falls back to the raw total if that
    goes negative (cache_read_tokens should never exceed input_tokens; if
    it does, something's off with this row, so keep the original number
    instead of a negative one).
    """
    if total_input_tokens is None or cache_read_tokens is None:
        return total_input_tokens
    delta = total_input_tokens - cache_read_tokens
    if delta < 0:
        logger.warning(
            f"input_tokens ({total_input_tokens}) < cache_read_tokens ({cache_read_tokens}) "
            f"- falling back to raw input_tokens"
        )
        return total_input_tokens
    return delta


def handle_after_agent_response() -> None:
    """Handle Cursor's afterAgentResponse hook: persist a RESPONSE row."""
    debug("afterAgentResponse handler triggered")
    setup_logging(log_to_file=True, log_to_console=False, log_dir=get_cursor_logs_dir())
    logger.info("=== Cursor AfterAgentResponse Handler ===")

    try:
        hook_data = read_stdin_json()
        logger.info(f"afterAgentResponse full payload: {json.dumps(hook_data, default=str)}")
        debug(f"payload keys: {list(hook_data.keys())}")

        prompt_id = hook_data.get("generation_id")
        session_id = hook_data.get("session_id") or hook_data.get("conversation_id")
        response_text = repair_text(hook_data.get("text"))

        if not prompt_id or not response_text:
            logger.warning(
                f"Incomplete afterAgentResponse payload - generation_id={prompt_id!r}, "
                f"text present={bool(response_text)}. Skipping write."
            )
        else:
            from src.db.manager import get_db_connection
            from src.db.schema import migrate_schema
            migrate_schema(get_db_connection())

            writer = DatabaseWriter()
            message_id = str(_uuid_mod.uuid4())
            written = writer.write_response({
                "message_id": message_id,
                "prompt_id": prompt_id,
                "response_text": response_text,
                "model": hook_data.get("model") or hook_data.get("model_id"),
                "timestamp": get_now_ist_iso(),
            })

            if written:
                debug(f"response stored - message_id={message_id}, prompt_id={prompt_id}")
                logger.info(f"Cursor response stored: message_id={message_id}, prompt_id={prompt_id}")

                cache_read_tokens = hook_data.get("cache_read_tokens")
                tokens_written = writer.write_io_tokens({
                    "id": str(_uuid_mod.uuid4()),
                    "prompt_id": prompt_id,
                    "message_id": message_id,
                    "token_type": "io",
                    "input_tokens": _compute_input_tokens(hook_data.get("input_tokens"), cache_read_tokens),
                    "cache_creation_tokens": hook_data.get("cache_write_tokens"),
                    "cache_read_tokens": cache_read_tokens,
                    "output_tokens": hook_data.get("output_tokens"),
                })
                if not tokens_written:
                    logger.warning(f"Cursor IO tokens write failed - prompt_id={prompt_id}, message_id={message_id}")
            else:
                debug(f"response NOT stored - prompt_id={prompt_id}")
                logger.warning(f"Cursor response write failed - prompt_id={prompt_id}")

        obs_state.delete(session_id, prompt_id)

    except Exception as e:
        debug(f"ERROR - {e}")
        logger.error(f"Error in Cursor AfterAgentResponse handler: {e}", exc_info=True)

    print(json.dumps({}))


def main() -> None:
    handle_after_agent_response()


if __name__ == "__main__":
    main()
