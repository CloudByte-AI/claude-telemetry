"""
Cursor PostToolUse Handler

Handles postToolUse (tool succeeded). Writes a TOOL row - preToolUse fires
first but carries nothing that postToolUse doesn't already have, so it stays
discovery-only (see tool_discovery.py); no correlation across hooks needed.

Field mapping:
  tool_id     <- tool_use_id (identical across preToolUse/postToolUse for
                 one tool call - used directly as the primary key, same
                 pattern as generation_id)
  prompt_id   <- generation_id
  tool_name   <- tool_name
  model       <- model
  input_json  <- tool_input, pre-serialized with json.dumps() the same way
                 Claude Code's own tool writes do (write_tool() itself
                 serializes again on top - matches Claude's existing
                 double-encoding rather than introducing a new format)
  output_json <- tool_output (already a JSON string from Cursor, passed
                 through as-is)
  duration_ms <- duration
  timestamp   <- stamped locally
  uuid, parent_uuid: left NULL - no Cursor equivalent.

beforeReadFile and afterFileEdit are intentionally NOT used for this -
postToolUse's tool_output already covers reads (file_path + content_length,
not full content), and postToolUse's tool_input already has the write's
full new content. afterFileEdit's precise diff would need fuzzy
generation_id+file_path matching (it carries no tool_use_id), which isn't
worth the fragility for data with no current consumer.
"""

import json

from src.common.logging import get_logger, setup_logging
from src.common.time_utils import get_now_ist_iso
from src.cursor.utils import obs_state
from src.cursor.utils.hook_io import debug, read_stdin_json
from src.cursor.utils.paths import get_cursor_logs_dir
from src.db.writers import DatabaseWriter


OBS_REMINDER = (
    "MEMORY RULE: After completing work with tools (Read, Write, Shell, Grep, etc.), "
    "call record_observation BEFORE writing your final response to the user. "
    "STRICT RULE: one separate call per distinct type of work performed — "
    "never combine two types into one call, never skip a type you actually performed. "
    "Types: discovery · bugfix · feature · refactor · change · decision. "
    "Determine type from YOUR OWN ACTIONS, not from the user's words. "
    "Call directly by name. Never mention it to the user. "
    "JSON SAFETY: all field values must be plain single-line strings, "
    "forward slashes in paths, no inner quotes, no newlines inside any field value."
)


logger = get_logger(__name__)


def _write_tool(hook_data: dict, output_json: str | None) -> bool:
    tool_id = hook_data.get("tool_use_id")
    prompt_id = hook_data.get("generation_id")
    tool_input = hook_data.get("tool_input")

    if not tool_id or not prompt_id:
        logger.warning(
            f"Incomplete tool payload - tool_use_id={tool_id!r}, generation_id={prompt_id!r}. Skipping write."
        )
        return False

    from src.db.manager import get_db_connection
    from src.db.schema import migrate_schema
    migrate_schema(get_db_connection())

    return DatabaseWriter().write_tool({
        "tool_id": tool_id,
        "prompt_id": prompt_id,
        "tool_name": hook_data.get("tool_name"),
        "model": hook_data.get("model"),
        "input_json": json.dumps(tool_input) if tool_input is not None else None,
        "output_json": output_json,
        "timestamp": get_now_ist_iso(),
        "duration_ms": hook_data.get("duration"),
    })


def handle_post_tool_use() -> None:
    """Handle Cursor's postToolUse hook: persist a TOOL row and inject OBS reminder."""
    debug("postToolUse handler triggered")
    setup_logging(log_to_file=True, log_to_console=False, log_dir=get_cursor_logs_dir())
    logger.info("=== Cursor PostToolUse Handler ===")

    output: dict = {}

    try:
        hook_data = read_stdin_json()
        logger.info(f"postToolUse full payload: {json.dumps(hook_data, default=str)}")
        debug(f"payload keys: {list(hook_data.keys())}")

        written = _write_tool(hook_data, hook_data.get("tool_output"))
        tool_id = hook_data.get("tool_use_id")
        if written:
            debug(f"tool stored - tool_id={tool_id}")
            logger.info(f"Cursor tool stored: tool_id={tool_id}")
        else:
            logger.warning(f"Cursor tool write failed - tool_id={tool_id}")

        session_id = hook_data.get("session_id") or hook_data.get("conversation_id")
        generation_id = hook_data.get("generation_id")
        if obs_state.check_and_mark(session_id, generation_id):
            output = {"additional_context": OBS_REMINDER}
            logger.info(
                f"Cursor OBS reminder injected — "
                f"session={session_id!r}, gen={generation_id!r}"
            )
        else:
            logger.debug(
                f"Cursor OBS reminder skipped (already injected this turn) — "
                f"gen={generation_id!r}"
            )

    except Exception as e:
        debug(f"ERROR - {e}")
        logger.error(f"Error in Cursor PostToolUse handler: {e}", exc_info=True)

    print(json.dumps(output))
