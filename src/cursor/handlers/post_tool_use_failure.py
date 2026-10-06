"""
Cursor PostToolUseFailure Handler

Handles postToolUseFailure: a tool call that failed, timed out or was denied
before it ran. Writes a TOOL row the way postToolUse does, with the failure as
its output (src/cursor/utils/tool_failures.py), so failed calls are recorded as
Claude Code's are.

It is also the only Cursor hook that carries the tool_use_id of a call a hook
blocked: beforeShellExecution and beforeMCPExecution carry none. The guardrails
drain links a deny to this row.

Cursor can reuse one tool_use_id for two calls, so a failure never replaces a
row already stored under its id: the record of a call that ran is kept.
"""

import json

from src.common.logging import get_logger, setup_logging
from src.cursor.handlers.post_tool_use import write_tool_row
from src.cursor.utils.hook_io import debug, read_stdin_json
from src.cursor.utils.paths import get_cursor_logs_dir
from src.cursor.utils.tool_failures import failure_output

logger = get_logger(__name__)


def handle_post_tool_use_failure() -> None:
    """Handle Cursor's postToolUseFailure hook: persist a TOOL row for the failed call."""
    debug("postToolUseFailure handler triggered")
    setup_logging(log_to_file=True, log_to_console=False, log_dir=get_cursor_logs_dir())
    logger.info("=== Cursor PostToolUseFailure Handler ===")

    try:
        hook_data = read_stdin_json()
        logger.info(f"postToolUseFailure full payload: {json.dumps(hook_data, default=str)}")

        # repr: Cursor's ids can hold a line break, which would split the log line.
        tool_id = hook_data.get("tool_use_id")
        failure_type = hook_data.get("failure_type")
        if write_tool_row(hook_data, failure_output(hook_data), keep_existing=True):
            debug(f"failed tool stored - tool_id={tool_id!r}")
            logger.info(f"Cursor failed tool stored: tool_id={tool_id!r}, failure_type={failure_type}")
    except Exception as e:
        debug(f"ERROR - {e}")
        logger.error(f"Error in Cursor PostToolUseFailure handler: {e}", exc_info=True)

    print(json.dumps({}))
