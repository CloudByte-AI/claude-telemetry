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


def extract_prompt_id_from_transcript(transcript_path: str) -> str:
    """
    Extract the promptId from the JSONL transcript file.

    The UserPromptSubmit hook doesn't receive promptId in hook_data, but Claude
    writes it to the JSONL. We need to read the latest user message to get it.

    Args:
        transcript_path: Path to the JSONL transcript file
        prompt_text: The prompt text to match against (for verification)

    Returns:
        str: The promptId from the transcript, or empty string if not found
    """
    import json

    if not transcript_path or not Path(transcript_path).exists():
        logger.debug(f"Transcript not found or path missing: {transcript_path}")
        return ""

    try:
        with open(transcript_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()

        # Read in reverse to find the most recent user message
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue

            try:
                event = json.loads(line)

                # Look for user message type with promptId field
                if event.get("type") == "user":
                    prompt_id = event.get("promptId") or event.get("prompt_id")
                    if prompt_id:
                        logger.debug(f"Found promptId in transcript: {prompt_id}")
                        return prompt_id

            except json.JSONDecodeError:
                continue

        logger.debug("No user message with promptId found in transcript")
        return ""

    except Exception as e:
        logger.warning(f"Error reading transcript for promptId: {e}")
        return ""


def extract_user_text_from_hook_data(hook_data: dict) -> str:
    """
    Extract the user's actual text from hook data.

    The hook data can have different structures:
    1. Simple "prompt" or "content" field (string)
    2. "message" object with "content" array (JSONL format)

    For the array format, we filter out system messages like <ide_opened_file>.

    Args:
        hook_data: Raw hook data from Claude Code

    Returns:
        str: Cleaned user text only
    """
    # First, check if there's a simple prompt/content field
    if "prompt" in hook_data:
        return filter_system_messages(hook_data["prompt"])
    if "content" in hook_data and isinstance(hook_data["content"], str):
        return filter_system_messages(hook_data["content"])

    # Check for message.content array format (JSONL structure)
    message = hook_data.get("message", {})
    if isinstance(message, dict):
        content_array = message.get("content", [])
        if isinstance(content_array, list):
            # Extract text from each content item
            user_texts = []
            for item in content_array:
                if isinstance(item, dict) and item.get("type") == "text":
                    text = item.get("text", "")
                    # Filter out system messages
                    filtered = filter_system_messages(text)
                    if filtered:  # Only add non-empty text
                        user_texts.append(filtered)

            # Join all user texts (usually just one after filtering)
            return " ".join(user_texts).strip()

    # Fallback: return empty string
    return ""


def filter_system_messages(content: str) -> str:
    """
    Filter out system/context messages from prompt content.

    Removes messages like:
    - <ide_opened_file>...</ide_opened_file>
    - <system-reminder>...</system-reminder>
    - <ide_selection>...</ide_selection>
    - <user-prompt-submit-hook additional context>...</user-prompt-submit-hook>

    Args:
        content: Raw prompt content (may include system messages)

    Returns:
        str: Cleaned prompt text with only user messages
    """
    if not content:
        return ""

    import re

    # Remove ANY <ide*> blocks with content (handles multiline, no-newline, and unclosed tags)
    # First, try to match properly closed tags
    content = re.sub(r'<ide[^>]*>[\s\S]*?</ide[^>]*>', '', content)
    # Then, match any unclosed ide tags (from <ide to end of string or next tag)
    content = re.sub(r'<ide[^>]*>[\s\S]*?(?=<\w|$)', '', content)

    # Remove <system-reminder> blocks
    content = re.sub(r'<system-reminder>.*?</system-reminder>\s*', '', content, flags=re.DOTALL)

    # Remove <user-prompt-submit-hook additional context> blocks
    content = re.sub(r'<user-prompt-submit-hook.*?</user-prompt-submit-hook>\s*', '', content, flags=re.DOTALL)

    # Remove other common system tags
    content = re.sub(r'<sessionstart-hook.*?</sessionstart-hook>\s*', '', content, flags=re.DOTALL)
    content = re.sub(r'<sessionstart-hook-additional-context.*?</sessionstart-hook-additional-context>\s*', '', content, flags=re.DOTALL)

    # Clean up extra whitespace
    content = content.strip()

    return content


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

        # DEBUG: Print all keys and values in hook_data to understand the structure
        logger.info(f"DEBUG - hook_data keys: {list(hook_data.keys())}")
        for key, value in hook_data.items():
            if key != "prompt":  # Skip the full prompt text to avoid spam
                logger.info(f"DEBUG - {key}: {value}")
            else:
                logger.info(f"DEBUG - {key}: (length={len(value)}, preview={value[:100]})")

        # Extract session_id early to check for pending tasks
        session_id = hook_data.get("session_id") or os.environ.get("CLAUDE_SESSION_ID")

        # Retry any pending/failed tasks from previous session
        if session_id:
            retry_pending_tasks(session_id)

        # Get transcript path for promptId extraction
        transcript_path = hook_data.get("transcript_path") or hook_data.get("transcriptPath")

        # Extract ALL available prompt data from JSONL event
        # Try both camelCase (JSONL style) and snake_case (our style) field names
        # NOTE: promptId is NOT in hook_data - we must read it from the transcript
        prompt_id = hook_data.get("prompt_id") or hook_data.get("promptId")  # Will be None from hook!
        parent_uuid = hook_data.get("parent_uuid") or hook_data.get("parentUuid")  # Try both!
        event_uuid = hook_data.get("uuid") or hook_data.get("id")  # Try both!
        event_timestamp = hook_data.get("timestamp") or hook_data.get("time")  # Try both!
        cwd = hook_data.get("cwd") or hook_data.get("directory") or os.environ.get("PWD") or os.environ.get("cwd")

        # Extract user text from hook data (handles both simple string and message.content array)
        prompt_text = extract_user_text_from_hook_data(hook_data)

        # IMPORTANT: Read promptId from transcript since hook doesn't provide it
        # The hook doesn't include promptId, but Claude writes it to the JSONL
        if not prompt_id and transcript_path:
            prompt_id = extract_prompt_id_from_transcript(transcript_path)
            if prompt_id:
                logger.info(f"Retrieved promptId from transcript: {prompt_id}")

        logger.info(f"User prompt: session_id={session_id}, prompt_length={len(prompt_text)}")

        # Ensure session is initialized (only checks once per session)
        # This handles the case where plugin started in an already-running session
        if session_id and cwd:
            ensure_session_initialized(session_id, cwd)

        if not prompt_text:
            logger.warning("No prompt content provided (after filtering system messages)")
            print(json.dumps({"status": "error", "message": "No prompt content"}))
            return

        # Process and store user prompt in database with ALL original fields
        result = process_user_prompt(
            prompt=prompt_text,  # Use filtered text
            session_id=session_id,
            prompt_id=prompt_id,
            parent_uuid=parent_uuid,
            event_uuid=event_uuid,  # Pass original uuid
            event_timestamp=event_timestamp,  # Pass original timestamp
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
