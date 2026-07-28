"""
Cursor Entry Point

Dispatches Cursor hook invocations to their handlers. This mirrors the
dispatch style of src/main.py (the Claude Code entrypoint) for familiarity,
but is a fully independent file — the Cursor and Claude adapters are being
built and wired up one hook at a time, in isolation from each other.

Invoked as: uv run --directory <plugin_root> -m src.cursor.main <command>
See hooks/cursor/hooks.json for which commands are currently wired to a hook.
"""

import os
import sys
from pathlib import Path

# Add src directory to path for imports, matching src/main.py's convention.
src_path = Path(__file__).parent.parent
sys.path.insert(0, str(src_path))

from src.common.logging import get_logger
from src.cursor.utils.paths import CURSOR_TEST_DB_NAME


logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# TEMPORARY - branch-local testing only. Every command dispatched below reads
# the DB path via src.common.paths.get_db_path(), which checks CLOUDBYTE_DB_NAME
# first. Setting it here - once, for the whole Cursor adapter - means no
# individual handler needs its own DB wiring: they all just call the plain
# DatabaseWriter() default, same as Claude's handlers do, and it transparently
# lands in cloudbyte-cursor-test.db instead of the shared cloudbyte.db.
#
# setdefault (not a plain assignment) so an explicitly-set CLOUDBYTE_DB_NAME
# in the environment still wins.
#
# TO REVERT BEFORE MERGING: delete this line. Cursor sessions will then land
# in the same shared cloudbyte.db as Claude's, distinguished by SESSION.client.
os.environ.setdefault("CLOUDBYTE_DB_NAME", CURSOR_TEST_DB_NAME)
# ---------------------------------------------------------------------------


def session_start() -> None:
    """sessionStart hook - creates PROJECT + SESSION records."""
    from src.cursor.handlers.session_start import handle_session_start
    handle_session_start()


def before_submit_prompt() -> None:
    """beforeSubmitPrompt hook - writes USER_PROMPT, backfills SESSION fields."""
    from src.cursor.handlers.before_submit_prompt import handle_before_submit_prompt
    handle_before_submit_prompt()


def stop() -> None:
    """stop hook - discovery mode, logs the raw payload only."""
    from src.cursor.handlers.stop import handle_stop
    handle_stop()


def after_agent_response() -> None:
    """afterAgentResponse hook - writes RESPONSE + IO_TOKENS."""
    from src.cursor.handlers.after_agent_response import handle_after_agent_response
    handle_after_agent_response()


def post_tool_use() -> None:
    """postToolUse hook - writes TOOL."""
    from src.cursor.handlers.post_tool_use import handle_post_tool_use
    handle_post_tool_use()


def after_agent_thought() -> None:
    """afterAgentThought hook - writes THINKING."""
    from src.cursor.handlers.after_agent_thought import handle_after_agent_thought
    handle_after_agent_thought()


def session_end() -> None:
    """sessionEnd hook - writes SESSION.ended_at/end_reason/final_status."""
    from src.cursor.handlers.session_end import handle_session_end
    handle_session_end()


def after_mcp_execution() -> None:
    """afterMCPExecution hook - writes HOOK_OBSERVATION for record_observation calls."""
    from src.cursor.handlers.after_mcp_execution import handle_after_mcp_execution
    handle_after_mcp_execution()


def main() -> None:
    handlers = {
        "session_start": session_start,
        "before_submit_prompt": before_submit_prompt,
        "stop": stop,
        "after_agent_response": after_agent_response,
        "post_tool_use": post_tool_use,
        "after_agent_thought": after_agent_thought,
        "session_end": session_end,
        "after_mcp_execution": after_mcp_execution,
    }

    if len(sys.argv) < 2:
        print("Usage: python -m src.cursor.main <command>")
        print(f"Commands: {', '.join(handlers.keys())}")
        sys.exit(1)

    command = sys.argv[1]

    handler = handlers.get(command)
    if handler is None:
        print(f"Unknown command: {command}")
        print(f"Available commands: {', '.join(handlers.keys())}")
        sys.exit(1)

    try:
        handler()
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        sys.exit(130)
    except Exception as e:
        logger.error(f"Command '{command}' failed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
