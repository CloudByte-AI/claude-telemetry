"""
Cursor AfterMCPExecution Handler

Fires after every MCP tool call. When the tool is record_observation,
parses the tool_input JSON string and writes a HOOK_OBSERVATION row.
All other MCP tool calls are logged for discovery but not stored.

Field mapping:
  session_id  <- session_id (or conversation_id as fallback)
  prompt_id   <- generation_id  (same key beforeSubmitPrompt wrote for
                 USER_PROMPT.prompt_id — no extra join needed)
  obs_data    <- json.loads(tool_input)  (full record_observation input:
                 type, title, subtitle, narrative, facts, concepts,
                 files_read, files_modified)

tool_input arrives as a JSON *string* in this hook (unlike postToolUse
where it is an already-parsed dict). json.loads() is required.
"""

import json

from src.common.logging import get_logger, setup_logging
from src.cursor.utils.hook_io import debug, read_stdin_json
from src.cursor.utils.paths import get_cursor_logs_dir
from src.observations.writer import save_observation


logger = get_logger(__name__)

_OBS_TOOL = "record_observation"


def _write_observation(hook_data: dict) -> None:
    """Parse tool_input and persist a HOOK_OBSERVATION row."""
    session_id = hook_data.get("session_id") or hook_data.get("conversation_id")
    generation_id = hook_data.get("generation_id")

    if not session_id or not generation_id:
        logger.warning(
            f"Incomplete afterMCPExecution payload — "
            f"session_id={session_id!r}, generation_id={generation_id!r}. Skipping write."
        )
        return

    tool_input_raw = hook_data.get("tool_input", "")
    try:
        obs_data = json.loads(tool_input_raw)
    except (json.JSONDecodeError, TypeError) as exc:
        logger.warning(f"Failed to parse tool_input JSON: {exc!r}. Skipping write.")
        return

    from src.db.manager import get_db_connection
    from src.db.schema import migrate_schema
    migrate_schema(get_db_connection())

    obs_id = save_observation(session_id, generation_id, obs_data)
    if obs_id:
        logger.info(
            f"Observation saved — obs_id={obs_id!r}, "
            f"type={obs_data.get('type', '')!r}, title={obs_data.get('title', '')!r}, "
            f"session={session_id!r}, gen={generation_id!r}"
        )
    else:
        logger.warning(
            f"Observation save failed — session={session_id!r}, gen={generation_id!r}"
        )


def handle_after_mcp_execution() -> None:
    """Handle Cursor's afterMCPExecution hook: persist record_observation calls to HOOK_OBSERVATION."""
    debug("afterMCPExecution handler triggered")
    setup_logging(log_to_file=True, log_to_console=False, log_dir=get_cursor_logs_dir())
    logger.info("=== Cursor AfterMCPExecution Handler ===")

    try:
        hook_data = read_stdin_json()
        logger.info(f"afterMCPExecution full payload: {json.dumps(hook_data, default=str)}")
        debug(f"payload keys: {list(hook_data.keys())}")

        tool_name = hook_data.get("tool_name", "")

        if tool_name == _OBS_TOOL:
            _write_observation(hook_data)
        else:
            logger.debug(f"Non-observation MCP tool — tool_name={tool_name!r}, skipping DB write")

    except Exception as exc:
        debug(f"ERROR - {exc}")
        logger.error(f"Error in Cursor AfterMCPExecution handler: {exc}", exc_info=True)

    print(json.dumps({}))
