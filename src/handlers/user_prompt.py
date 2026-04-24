"""
UserPromptSubmit Handler

Called when the user submits a prompt.
Stores the prompt in the USER_PROMPT and RAW_LOG tables.
Also reminds Claude to emit obs blocks in its response.
"""

import json
import os
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.common.logging import get_logger, setup_logging
from src.core.event_processor import process_user_prompt


logger = get_logger(__name__)


# Track sessions that have been initialized (in-memory for this handler process)
_initialized_sessions = set()


def ensure_session_initialized(session_id: str, cwd: str) -> bool:
    """
    Ensure the session and project exist in the database.
    Only does the check once per session (tracked in-memory).

    This handles the case where the plugin is started in an already-running session
    (so SessionStart hook was never called).

    Args:
        session_id: Session UUID
        cwd: Current working directory

    Returns:
        bool: True if session exists or was created successfully
    """
    global _initialized_sessions

    # Skip if already checked for this session
    if session_id in _initialized_sessions:
        return True

    from src.db.manager import get_db_connection
    from src.integrations.claude.extractor import extract_project_info

    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Check if session exists
        cursor.execute("SELECT 1 FROM SESSION WHERE session_id = ? LIMIT 1", (session_id,))
        if cursor.fetchone() is not None:
            # Session exists, mark as initialized
            _initialized_sessions.add(session_id)
            logger.debug(f"Session {session_id} already initialized")
            return True

        # Session doesn't exist - create project and session
        logger.info(f"Session {session_id} not found, initializing project and session records")

        # Extract project info from cwd
        project_info = extract_project_info(cwd if cwd else "")
        project_id = project_info["project_id"]

        # Ensure project exists
        cursor.execute("SELECT 1 FROM PROJECT WHERE project_id = ? LIMIT 1", (project_id,))
        if cursor.fetchone() is None:
            cursor.execute("""
                INSERT INTO PROJECT (project_id, name, path, created_at)
                VALUES (?, ?, ?, ?)
            """, (project_id, project_info["name"], project_info["path"], project_info["created_at"]))
            logger.info(f"Created project: {project_id}")

        # Create session
        from src.integrations.claude.reader import normalize_project_name
        import uuid
        from datetime import datetime

        cursor.execute("""
            INSERT INTO SESSION
            (session_id, project_id, cwd, jsonl_file, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (
            session_id,
            project_id,
            cwd,
            f"{project_info['name']}/{session_id}.jsonl",
            datetime.now().isoformat(),
        ))

        conn.commit()
        logger.info(f"Created session: {session_id}")

        # Mark as initialized
        _initialized_sessions.add(session_id)
        return True

    except Exception as e:
        logger.error(f"Error ensuring session initialized: {e}")
        return False


def retry_pending_tasks(session_id: str):
    """
    Retry pending and failed tasks for a session.

    Called on session start to process any tasks that didn't complete
    in the previous session.
    """
    from src.db.manager import get_db_manager

    try:
        db = get_db_manager()

        # Get pending and failed tasks
        tasks = db.execute("""
            SELECT id, task_type, session_id, prompt_id, priority, payload
            FROM TASK_QUEUE
            WHERE session_id = ? AND status IN ('pending', 'failed')
            ORDER BY priority DESC, created_at ASC
        """, (session_id,)).fetchall()

        if not tasks:
            return

        logger.info(f"Found {len(tasks)} pending/failed tasks for session {session_id}, retrying...")

        # Update status to pending and reset error
        for task in tasks:
            db.execute("""
                UPDATE TASK_QUEUE
                SET status = 'pending',
                    error_message = NULL,
                    retry_count = retry_count + 1,
                    created_at = datetime('now')
                WHERE id = ?
            """, (task[0],))

        # Trigger worker processing
        try:
            from src.workers.llm_client import reset_worker
            reset_worker()
            logger.info("Worker reset to process retried tasks")
        except Exception as e:
            logger.warning(f"Could not reset worker: {e}")

    except Exception as e:
        # Don't log as error - tables might not exist yet
        logger.debug(f"Could not retry pending tasks (DB might not be initialized yet): {e}")


# Reminder to inject before each prompt
OBS_REMINDER = (
    "OBS RULE ACTIVE. After this response, if you used tools or made meaningful changes, "
    "append at the very end:\n"
    "<obs>{\"type\":\"bugfix|feature|refactor|change|discovery|decision\","
    "\"title\":\"...\",\"subtitle\":\"...\",\"narrative\":\"...\","
    "\"facts\":[],\"concepts\":[],\"files_read\":[],\"files_modified\":[]}</obs>\n"
    "QUALITY: Facts=concise technical statements. Concepts=abstract terms. "
    "Narrative=6-12 sentences covering: what was attempted → what broke/blocked → "
    "what insight changed direction → what was built/fixed → what risk remains. "
    "Skip for greetings, yes/no, simple reads."
)


def read_stdin_data() -> dict:
    """
    Read hook data from stdin.

    Claude Code passes hook data via stdin.
    Expected format: JSON with prompt, session_id, etc.

    Returns:
        dict: Parsed hook data
    """
    try:
        data = sys.stdin.read().strip()
        if data:
            return json.loads(data)
    except json.JSONDecodeError as e:
        logger.error(f"Error parsing stdin JSON: {e}")

    return {}


def handle_user_prompt():
    """
    Handle the UserPromptSubmit hook.

    Expected stdin data (JSON):
    {
        "prompt": "user's prompt text",
        "session_id": "uuid",
        "prompt_id": "uuid",
        "parent_uuid": "uuid"
    }
    """
    setup_logging(log_to_file=True, log_to_console=False)
    logger.info("=== UserPromptSubmit Handler ===")

    # Quick check: ensure worker is running (fast port check)
    try:
        from src.workers.worker_checker import ensure_worker_quick_sync
        ensure_worker_quick_sync()
    except Exception as e:
        logger.debug(f"Worker check failed: {e}")

    try:
        # Read hook data from stdin
        hook_data = read_stdin_data()

        # Extract session_id early to check for pending tasks
        session_id = hook_data.get("session_id") or os.environ.get("CLAUDE_SESSION_ID")

        # Retry any pending/failed tasks from previous session
        if session_id:
            retry_pending_tasks(session_id)

        # Extract prompt data
        prompt = hook_data.get("prompt") or hook_data.get("content", "")
        prompt_id = hook_data.get("prompt_id")
        parent_uuid = hook_data.get("parent_uuid")
        cwd = hook_data.get("cwd") or os.environ.get("PWD") or os.environ.get("cwd")

        logger.info(f"User prompt: session_id={session_id}, prompt_length={len(prompt)}")

        # Ensure session is initialized (only checks once per session)
        # This handles the case where plugin started in an already-running session
        if session_id and cwd:
            ensure_session_initialized(session_id, cwd)

        if not prompt:
            logger.warning("No prompt content provided")
            print(json.dumps({"status": "error", "message": "No prompt content"}))
            return

        # Process and store user prompt in database
        result = process_user_prompt(
            prompt=prompt,
            session_id=session_id,
            prompt_id=prompt_id,
            parent_uuid=parent_uuid,
            cwd=cwd,
        )

        logger.info(f"Prompt stored: {result.get('prompt_id')}")
        # Output with OBS reminder injected into context
        logger.info("=" * 60)
        logger.info("INJECTING OBS REMINDER INTO USERPROMPTSUBMIT CONTEXT")
        logger.info("=" * 60)
        logger.info(f"Prompt ID: {result.get('prompt_id')}")
        logger.info(f"OBS reminder length: {len(OBS_REMINDER)} characters")
        logger.info(f"Reminder: {OBS_REMINDER}")

        output_data = {
            "status": "success",
            "prompt_id": result.get("prompt_id"),
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": OBS_REMINDER
            }
        }
        print(json.dumps(output_data))
        logger.info("✓ OBS reminder successfully output to Claude Code")
        logger.info("=" * 60)

    except Exception as e:
        logger.error(f"Error in UserPromptSubmit handler: {e}", exc_info=True)
        print(json.dumps({"status": "error", "message": str(e)}))
        sys.exit(1)


def main():
    """Main entry point for the handler."""
    handle_user_prompt()


if __name__ == "__main__":
    main()
