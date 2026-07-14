"""
Cursor BeforeSubmitPrompt Handler

Handles the beforeSubmitPrompt hook, fired right after the user hits send
and before the backend request. Persists a USER_PROMPT row, and backfills
SESSION.transcript_path and SESSION.ai_title if not set yet.

Field mapping:
  prompt_id      <- generation_id (used directly as the primary key)
  session_id     <- session_id, falling back to conversation_id
  prompt         <- prompt, repaired via repair_text() (see hook_io.py)
  attachments    <- attachments, JSON-encoded
  client_version <- cursor_version
  mode           <- composer_mode (agent/ask/edit - Cursor's closest
                     equivalent to Claude Code's permission_mode)
  git_branch     <- self-derived via `git rev-parse --abbrev-ref HEAD`
                     against workspace_roots[0] - no hook gives this
  entrypoint     <- hardcoded "cursor-ide"
  timestamp      <- stamped locally
  uuid, parent_uuid, jsonl_prompt_id: left NULL - no Cursor equivalent.
  status: left NULL - populated later by the stop hook.

This hook can block prompt submission, so it always returns
{"continue": true} regardless of write outcome.
"""

import json
import subprocess

from src.common.logging import get_logger, setup_logging
from src.common.time_utils import get_now_ist_iso
from src.cursor.utils import obs_state
from src.cursor.utils.composer_title import get_composer_title
from src.cursor.utils.hook_io import debug, normalize_cwd, normalize_path, read_stdin_json, repair_text
from src.cursor.utils.paths import get_cursor_logs_dir
from src.db.writers import DatabaseWriter


logger = get_logger(__name__)

CURSOR_ENTRYPOINT = "cursor-ide"


def _get_git_branch(cwd: str | None) -> str | None:
    """Best-effort current git branch for cwd. Returns None on any failure."""
    if not cwd:
        return None
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=3,
        )
        branch = result.stdout.strip()
        return branch if result.returncode == 0 and branch else None
    except Exception as e:
        debug(f"git branch lookup failed for {cwd}: {e}")
        return None


def handle_before_submit_prompt() -> None:
    """Handle Cursor's beforeSubmitPrompt hook: persist a USER_PROMPT row."""
    debug("beforeSubmitPrompt handler triggered")
    setup_logging(log_to_file=True, log_to_console=False, log_dir=get_cursor_logs_dir())
    logger.info("=== Cursor BeforeSubmitPrompt Handler ===")

    try:
        hook_data = read_stdin_json()
        logger.info(f"beforeSubmitPrompt full payload: {json.dumps(hook_data, default=str)}")
        debug(f"payload keys: {list(hook_data.keys())}")

        prompt_id = hook_data.get("generation_id")
        session_id = hook_data.get("session_id") or hook_data.get("conversation_id")
        prompt_text = repair_text(hook_data.get("prompt"))
        attachments = hook_data.get("attachments")
        workspace_roots = hook_data.get("workspace_roots") or []
        cwd = normalize_cwd(workspace_roots[0]) if workspace_roots else None

        obs_state.create(session_id, prompt_id)

        if not prompt_id or not session_id or not prompt_text:
            logger.warning(
                f"Incomplete beforeSubmitPrompt payload - generation_id={prompt_id!r}, "
                f"session_id={session_id!r}, prompt present={bool(prompt_text)}. Skipping write."
            )
        else:
            from src.db.manager import get_db_connection
            from src.db.schema import migrate_schema
            migrate_schema(get_db_connection())

            writer = DatabaseWriter()
            written = writer.write_user_prompt({
                "prompt_id": prompt_id,
                "session_id": session_id,
                "prompt": prompt_text,
                "timestamp": get_now_ist_iso(),
                "client_version": hook_data.get("cursor_version"),
                "attachments": json.dumps(attachments) if attachments is not None else None,
                "mode": hook_data.get("composer_mode"),
                "git_branch": _get_git_branch(cwd),
                "entrypoint": CURSOR_ENTRYPOINT,
            })

            transcript_path = normalize_path(hook_data.get("transcript_path"))
            if transcript_path:
                writer.update_session_transcript_path(session_id, transcript_path)

            if not writer.get_session_ai_title(session_id):
                title = get_composer_title(session_id)
                if title:
                    writer.update_session_ai_title(session_id, title)

            if written:
                debug(f"prompt stored - prompt_id={prompt_id}, session_id={session_id}")
                logger.info(f"Cursor prompt stored: prompt_id={prompt_id}, session_id={session_id}")
            else:
                debug(f"prompt NOT stored - prompt_id={prompt_id}, session_id={session_id}")
                logger.warning(
                    f"Cursor prompt write failed - prompt_id={prompt_id}, session_id={session_id} "
                    f"(see preceding error log line for the reason, e.g. missing SESSION row)"
                )

    except Exception as e:
        debug(f"ERROR - {e}")
        logger.error(f"Error in Cursor BeforeSubmitPrompt handler: {e}", exc_info=True)

    print(json.dumps({"continue": True}))


def main() -> None:
    handle_before_submit_prompt()


if __name__ == "__main__":
    main()
