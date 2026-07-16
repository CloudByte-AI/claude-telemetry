"""
Cursor SessionStart Handler

Handles the sessionStart hook, fired when a new Cursor composer conversation
is created. Persists PROJECT + SESSION rows from the hook payload alone -
no transcript/JSONL file is read or parsed.

sessionStart is fire-and-forget: Cursor does not wait for or enforce a
response, so failures are logged and swallowed rather than surfaced.
"""

import json
from pathlib import Path

from src.common.logging import get_logger, setup_logging
from src.common.time_utils import get_now_ist_iso
from src.cursor.utils.hook_io import (
    debug as _debug,
    normalize_cwd,
    normalize_path,
    read_stdin_json as _read_stdin_data,
)
from src.cursor.utils.paths import get_cursor_logs_dir
from src.cursor.utils.session_bootstrap import generate_project_id as _generate_project_id
from src.db.writers import DatabaseWriter


logger = get_logger(__name__)


OBS_INSTRUCTION = (
    "MEMORY SYSTEM ACTIVE.\n\n"
    "You have a tool called record_observation.\n\n"
    "RULE: After completing work with tools (Read, Write, Bash/Shell, Grep, etc.), "
    "call record_observation BEFORE writing your final response to the user.\n\n"
    "STRICT RULE: one separate observation call per distinct type of work performed. "
    "Never combine two types into one call. Never skip a type you actually performed.\n\n"
    "EXAMPLES:\n"
    "Found a bug + fixed it + added a test:\n"
    "  Call 1 → type=discovery, title='Found null pointer in auth middleware'\n"
    "  Call 2 → type=bugfix,    title='Fixed null pointer in auth middleware'\n"
    "  Call 3 → type=feature,   title='Added regression test for auth middleware'\n\n"
    "Chose approach + implemented it:\n"
    "  Call 1 → type=decision,  title='Chose jsonl_prompt_id over temp file approach'\n"
    "  Call 2 → type=feature,   title='Implemented jsonl_prompt_id column in schema'\n\n"
    "Fixed bug + refactored same file:\n"
    "  Call 1 → type=bugfix,    title='Fixed race condition in worker stop()'\n"
    "  Call 2 → type=refactor,  title='Restructured worker stop() for clarity'\n\n"
    "Read config and updated a value (one logical action):\n"
    "  Call 1 → type=change,    title='Updated worker timeout in config.json'\n\n"
    "Read and understood code structure only (no changes):\n"
    "  Call 1 → type=discovery, title='Analysed CloudByte schema design'\n\n"
    "RULE: count types performed → call that many times, no more, no less.\n"
    "IMPORTANT: determine type from YOUR OWN ACTIONS, not from words in the user prompt.\n"
    "If user says 'decide' but you just followed obvious instructions → type=change, not decision.\n"
    "decision is only correct when YOU genuinely evaluated multiple valid alternatives.\n\n"
    "SKIP ONLY when you used zero tools (pure conversation, greetings, yes/no answers).\n\n"
    "HOW TO CALL IT:\n"
    "- Call it directly by its name\n"
    "- Call BEFORE your final text response\n"
    "- Never mention it to the user\n"
    "- Never show it in your response text\n"
    "JSON SAFETY: all field values must be plain single-line strings. "
    "Use forward slashes in paths (never backslashes). "
    "No inner quotes, no newlines inside any field value.\n"
)


def _emit_with_context(extra: dict | None = None) -> None:
    """Print the sessionStart hook output with OBS_INSTRUCTION in additional_context.

    additional_context is the flat, top-level output field Cursor's hooks.md
    documents for sessionStart (unlike Claude Code's nested
    hookSpecificOutput.additionalContext) - confirmed against the doc table
    before wiring this in.
    """
    logger.info("=" * 60)
    logger.info("INJECTING OBS INSTRUCTION INTO CURSOR SESSIONSTART CONTEXT")
    logger.info(f"OBS instruction length: {len(OBS_INSTRUCTION)} characters")
    logger.info(f"Instruction preview: {OBS_INSTRUCTION[:100]}...")
    payload = {**(extra or {}), "additional_context": OBS_INSTRUCTION}
    print(json.dumps(payload))
    logger.info("Context injected into Cursor sessionStart output via additional_context field")
    logger.info("=" * 60)


def handle_session_start() -> None:
    """Handle Cursor's sessionStart hook: persist PROJECT + SESSION rows."""
    _debug("sessionStart handler triggered")
    setup_logging(log_to_file=True, log_to_console=False, log_dir=get_cursor_logs_dir())
    logger.info("=== Cursor SessionStart Handler ===")

    try:
        hook_data = _read_stdin_data()
        logger.info(f"sessionStart full payload: {json.dumps(hook_data, default=str)}")
        _debug(f"payload keys: {list(hook_data.keys())}")

        session_id = hook_data.get("session_id") or hook_data.get("conversation_id")
        workspace_roots = hook_data.get("workspace_roots") or []
        cwd = normalize_cwd(workspace_roots[0]) if workspace_roots else None

        if len(workspace_roots) > 1:
            logger.warning(
                f"Multiple workspace_roots reported ({len(workspace_roots)}); "
                f"using the first one only: {cwd}"
            )

        if not session_id or not cwd:
            _debug(f"skipped - incomplete payload (session_id={session_id!r}, cwd={cwd!r})")
            logger.warning(
                f"Incomplete sessionStart payload - session_id={session_id!r}, "
                f"cwd={cwd!r}. Skipping session creation."
            )
            _emit_with_context()
            return

        project_id = _generate_project_id(cwd)
        project_name = Path(cwd).name
        now = get_now_ist_iso()

        writer = DatabaseWriter()
        writer.write_project({
            "project_id": project_id,
            "name": project_name,
            "path": cwd,
            "created_at": now,
        })

        writer.write_session({
            "session_id": session_id,
            "project_id": project_id,
            "cwd": cwd,
            "transcript_path": normalize_path(hook_data.get("transcript_path")),
            "created_at": now,
            "client": "cursor",
        })

        # Mark this session active so the shared worker/dashboard at :8765
        # isn't torn down by a Claude Code session ending while this one
        # is still running - see src/common/session_registry.py.
        try:
            from src.common.session_registry import register
            register(session_id, "cursor")
        except Exception as e:
            logger.debug(f"session_registry.register failed: {e}")

        # Bring up the shared worker/dashboard if it isn't already running -
        # previously only Claude's sessionStart did this, so a Cursor-only
        # user could never reach localhost:8765 at all.
        try:
            from src.workers.worker_checker import ensure_worker_quick_sync
            ensure_worker_quick_sync()
        except Exception as e:
            logger.debug(f"Worker check failed: {e}")

        try:
            from src.workers.llm_client import ensure_worker_running
            worker_started = ensure_worker_running()
            logger.info("LLM worker started successfully" if worker_started else "LLM worker failed to start")
        except ImportError:
            logger.debug("Worker module not available")
        except Exception as e:
            logger.warning(f"Failed to start worker: {e}")

        _debug(f"session created - session_id={session_id}, project={project_name}")
        logger.info(f"Cursor session created: session_id={session_id}, project={project_name}")
        _emit_with_context()

    except Exception as e:
        _debug(f"ERROR - {e}")
        logger.error(f"Error in Cursor SessionStart handler: {e}", exc_info=True)
        _emit_with_context()


def main() -> None:
    handle_session_start()


if __name__ == "__main__":
    main()
