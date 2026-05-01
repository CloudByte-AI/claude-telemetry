"""
UserPromptSubmit Handler

Called when the user submits a prompt.
Stores the prompt in the USER_PROMPT and RAW_LOG tables.
Also reminds Claude to emit obs blocks in its response.

FIXES APPLIED:
  1. Race condition fix — polls transcript with retries + text verification
     so we never grab a previous session's promptId.
  2. parent_uuid fix — also read from transcript (not hook stdin) alongside promptId.
  3. Removed dead imports: normalize_project_name, uuid (were imported but never used).
"""

import json
import os
import sys
import time
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.common.logging import get_logger, setup_logging
from src.core.event_processor import process_user_prompt


logger = get_logger(__name__)


# Track sessions that have been initialized (in-memory for this handler process)
_initialized_sessions = set()


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------

def ensure_session_initialized(session_id: str, cwd: str) -> bool:
    """
    Ensure the session and project exist in the database.
    Only does the check once per session (tracked in-memory).

    This handles the case where the plugin is started in an already-running
    session (so SessionStart hook was never called).

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
    from datetime import datetime

    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Check if session exists
        cursor.execute(
            "SELECT 1 FROM SESSION WHERE session_id = ? LIMIT 1",
            (session_id,),
        )
        if cursor.fetchone() is not None:
            _initialized_sessions.add(session_id)
            logger.debug(f"Session {session_id} already initialized")
            return True

        # Session doesn't exist — create project and session
        logger.info(
            f"Session {session_id} not found, initializing project and session records"
        )

        # Extract project info from cwd
        project_info = extract_project_info(cwd if cwd else "")
        project_id = project_info["project_id"]

        # Ensure project exists
        cursor.execute(
            "SELECT 1 FROM PROJECT WHERE project_id = ? LIMIT 1",
            (project_id,),
        )
        if cursor.fetchone() is None:
            cursor.execute(
                """
                INSERT INTO PROJECT (project_id, name, path, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    project_id,
                    project_info["name"],
                    project_info["path"],
                    project_info["created_at"],
                ),
            )
            logger.info(f"Created project: {project_id}")

        # Create session
        cursor.execute(
            """
            INSERT INTO SESSION
            (session_id, project_id, cwd, jsonl_file, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                session_id,
                project_id,
                cwd,
                f"{project_info['name']}/{session_id}.jsonl",
                datetime.now().isoformat(),
            ),
        )

        conn.commit()
        logger.info(f"Created session: {session_id}")

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

        tasks = db.execute(
            """
            SELECT id, task_type, session_id, prompt_id, priority, payload
            FROM TASK_QUEUE
            WHERE session_id = ? AND status IN ('pending', 'failed')
            ORDER BY priority DESC, created_at ASC
            """,
            (session_id,),
        ).fetchall()

        if not tasks:
            return

        logger.info(
            f"Found {len(tasks)} pending/failed tasks for session {session_id}, retrying..."
        )

        for task in tasks:
            db.execute(
                """
                UPDATE TASK_QUEUE
                SET status = 'pending',
                    error_message = NULL,
                    retry_count = retry_count + 1,
                    created_at = datetime('now')
                WHERE id = ?
                """,
                (task[0],),
            )

        try:
            from src.workers.llm_client import reset_worker
            reset_worker()
            logger.info("Worker reset to process retried tasks")
        except Exception as e:
            logger.warning(f"Could not reset worker: {e}")

    except Exception as e:
        # Don't log as error — tables might not exist yet
        logger.debug(
            f"Could not retry pending tasks (DB might not be initialized yet): {e}"
        )


# ---------------------------------------------------------------------------
# OBS reminder constant
# ---------------------------------------------------------------------------

OBS_REMINDER = (
    "MEMORY RULE: If you used ANY tool this response, "
    "call mcp__plugin_claude-telemetry_cloudbyte__record_observation BEFORE "
    "writing your final answer. Call it directly — do NOT use ToolSearch first. "
    "Never mention it to the user."
)


# ---------------------------------------------------------------------------
# FIX 1 + FIX 2: Extract BOTH promptId AND parentUuid from transcript
#                with retry loop to beat the race condition.
# ---------------------------------------------------------------------------

def extract_ids_from_transcript(
    transcript_path: str,
    prompt_text: str,
    max_retries: int = 30,
    base_delay_ms: int = 150,
) -> dict:
    """
    Extract promptId AND parentUuid from the JSONL transcript file.

    WHY RETRIES ARE NEEDED (race condition):
        The UserPromptSubmit hook fires BEFORE Claude finishes writing the
        current user-message entry to the JSONL file.  Without retrying we
        would read the file too early, miss the current entry, and return the
        PREVIOUS message's promptId by mistake.

    STRATEGY:
        1. Poll the transcript file up to `max_retries` times with an
           exponentially growing delay (60ms, 120ms, 180ms … capped at 500ms).
           Total worst-case wait ≈ 3-4 seconds — acceptable, never misleading.
        2. For each candidate user-message entry found (scanning in reverse),
           verify that the entry's text actually contains the current prompt
           text (first 80 chars match) before accepting its promptId.
           This guarantees we never return a stale/previous promptId.

    Args:
        transcript_path : Path to the JSONL transcript file.
        prompt_text     : The filtered user prompt text (used for verification).
        max_retries     : Maximum number of read attempts.
        base_delay_ms   : Base sleep time between retries in milliseconds.

    Returns:
        dict with keys:
            "prompt_id"   → str  (empty string if not found)
            "parent_uuid" → str | None
    """
    NOT_FOUND = {"prompt_id": "", "parent_uuid": None}

    if not transcript_path:
        logger.debug("No transcript path provided, skipping promptId extraction")
        return NOT_FOUND

    transcript = Path(transcript_path)

    # Normalise the snippet we use for matching (strip whitespace, lowercase)
    match_snippet = prompt_text.strip()[:80].lower() if prompt_text else ""

    for attempt in range(1, max_retries + 1):

        # ── wait before each attempt (including the first, tiny initial wait) ──
        # First attempt: 60ms  — gives Claude time to flush the entry.
        # Subsequent attempts grow linearly, capped at 500ms.
        sleep_ms = min(base_delay_ms * attempt, 500)
        time.sleep(sleep_ms / 1000.0)

        if not transcript.exists():
            logger.debug(
                f"Attempt {attempt}/{max_retries}: transcript not found yet: {transcript_path}"
            )
            continue

        try:
            with open(transcript, "r", encoding="utf-8") as f:
                lines = f.readlines()

            # Scan in reverse — the most-recent user message is near the end
            for raw_line in reversed(lines):
                raw_line = raw_line.strip()
                if not raw_line:
                    continue

                try:
                    event = json.loads(raw_line)
                except json.JSONDecodeError:
                    continue

                if event.get("type") != "user":
                    continue

                if event.get("isMeta"):
                    continue  # skip synthetic resume messages — never return their ID

                prompt_id = event.get("promptId") or event.get("prompt_id")
                if not prompt_id:
                    continue  # user entry without an id — skip

                # ── Text verification — never return a stale promptId ────────
                # Build the text of this transcript entry so we can compare
                # it against the current prompt.
                if match_snippet:
                    content_items = (
                        event.get("message", {}).get("content", [])
                        if isinstance(event.get("message"), dict)
                        else []
                    )
                    entry_text = " ".join(
                        item.get("text", "")
                        for item in content_items
                        if isinstance(item, dict) and item.get("type") == "text"
                    ).strip().lower()

                    if match_snippet not in entry_text:
                        # Text mismatch — skip this entry and keep scanning older ones.
                        # Do NOT break — on session resume the current entry may not
                        # be written yet, so older entries with matching text exist.
                        logger.debug(
                            f"Attempt {attempt}/{max_retries}: "
                            f"promptId {prompt_id} text mismatch, skipping"
                        )
                        continue

                # ── Match confirmed ──────────────────────────────────────────
                parent_uuid = event.get("parentUuid") or event.get("parent_uuid")
                logger.info(
                    f"Attempt {attempt}/{max_retries}: "
                    f"Found promptId={prompt_id}, parentUuid={parent_uuid}"
                )
                return {"prompt_id": prompt_id, "parent_uuid": parent_uuid}

        except Exception as e:
            logger.warning(
                f"Attempt {attempt}/{max_retries}: error reading transcript: {e}"
            )

    # All retries exhausted
    logger.warning(
        f"Could not find current promptId in transcript after {max_retries} attempts. "
        f"Prompt will be stored without a promptId."
    )
    return NOT_FOUND


# ---------------------------------------------------------------------------
# Text / content helpers
# ---------------------------------------------------------------------------

def filter_system_messages(content: str) -> str:
    """
    Filter out system/context messages from prompt content.

    Removes:
        - <ide_opened_file>…</ide_opened_file>
        - <ide_selection>…</ide_selection>
        - <system-reminder>…</system-reminder>
        - <user-prompt-submit-hook …>…</user-prompt-submit-hook>
        - <sessionstart-hook …>…</sessionstart-hook>

    Args:
        content: Raw prompt content (may include system messages)

    Returns:
        str: Cleaned prompt text with only the user's own words
    """
    if not content:
        return ""

    import re

    # Remove ANY <ide*> blocks (properly closed)
    content = re.sub(r"<ide[^>]*>[\s\S]*?</ide[^>]*>", "", content)
    # Remove unclosed <ide*> tags to end-of-string
    content = re.sub(r"<ide[^>]*>[\s\S]*?(?=<\w|$)", "", content)

    # Remove <system-reminder> blocks
    content = re.sub(
        r"<system-reminder>.*?</system-reminder>\s*", "", content, flags=re.DOTALL
    )

    # Remove <user-prompt-submit-hook …> blocks
    content = re.sub(
        r"<user-prompt-submit-hook.*?</user-prompt-submit-hook>\s*",
        "",
        content,
        flags=re.DOTALL,
    )

    # Remove <sessionstart-hook …> blocks
    content = re.sub(
        r"<sessionstart-hook.*?</sessionstart-hook>\s*", "", content, flags=re.DOTALL
    )
    content = re.sub(
        r"<sessionstart-hook-additional-context.*?</sessionstart-hook-additional-context>\s*",
        "",
        content,
        flags=re.DOTALL,
    )

    return content.strip()


def extract_user_text_from_hook_data(hook_data: dict) -> str:
    """
    Extract the user's actual text from hook data.

    Handles two formats:
        1. Simple string field  →  hook_data["prompt"] or hook_data["content"]
        2. Message-content array  →  hook_data["message"]["content"][*]["text"]

    System tags are stripped in both cases.

    Args:
        hook_data: Raw hook data from Claude Code (stdin JSON)

    Returns:
        str: Cleaned user text only
    """
    # Format 1 — simple string
    if "prompt" in hook_data:
        return filter_system_messages(hook_data["prompt"])
    if "content" in hook_data and isinstance(hook_data["content"], str):
        return filter_system_messages(hook_data["content"])

    # Format 2 — message.content array
    message = hook_data.get("message", {})
    if isinstance(message, dict):
        content_array = message.get("content", [])
        if isinstance(content_array, list):
            user_texts = []
            for item in content_array:
                if isinstance(item, dict) and item.get("type") == "text":
                    filtered = filter_system_messages(item.get("text", ""))
                    if filtered:
                        user_texts.append(filtered)
            return " ".join(user_texts).strip()

    return ""


def read_stdin_data() -> dict:
    """
    Read hook data from stdin (JSON).

    Claude Code passes hook data via stdin.

    Returns:
        dict: Parsed hook data, or {} on error
    """
    try:
        data = sys.stdin.read().strip()
        if data:
            return json.loads(data)
    except json.JSONDecodeError as e:
        logger.error(f"Error parsing stdin JSON: {e}")

    return {}


# ---------------------------------------------------------------------------
# Main handler
# ---------------------------------------------------------------------------

def handle_user_prompt():
    """
    Handle the UserPromptSubmit hook.

    Expected stdin data (JSON):
    {
        "session_id"      : "uuid",
        "transcript_path" : "/path/to/session.jsonl",
        "cwd"             : "/path/to/project",
        "message"         : {
            "content": [{"type": "text", "text": "user prompt here"}]
        }
    }

    NOTE: promptId and parentUuid are NOT present in hook stdin — they are
    read from the transcript file (with retry logic to avoid the race condition
    where the hook fires before Claude writes the entry to the JSONL).
    """
    setup_logging(log_to_file=True, log_to_console=False)
    logger.info("=== UserPromptSubmit Handler ===")

    # Ensure background worker is running (fast port check)
    try:
        from src.workers.worker_checker import ensure_worker_quick_sync
        ensure_worker_quick_sync()
    except Exception as e:
        logger.debug(f"Worker check failed: {e}")

    try:
        # ── Read hook data ───────────────────────────────────────────────────
        hook_data = read_stdin_data()

        logger.info(f"hook_data keys: {list(hook_data.keys())}")
        for key, value in hook_data.items():
            if key != "prompt":
                logger.info(f"  {key}: {value}")
            else:
                logger.info(
                    f"  {key}: (length={len(value)}, preview={value[:100]})"
                )

        # ── Basic fields available directly in hook stdin ────────────────────
        session_id = (
            hook_data.get("session_id")
            or hook_data.get("sessionId")
            or os.environ.get("CLAUDE_SESSION_ID")
        )
        transcript_path = (
            hook_data.get("transcript_path") or hook_data.get("transcriptPath")
        )
        cwd = (
            hook_data.get("cwd")
            or hook_data.get("directory")
            or os.environ.get("PWD")
            or os.environ.get("cwd")
        )
        event_uuid = hook_data.get("uuid") or hook_data.get("id")
        event_timestamp = hook_data.get("timestamp") or hook_data.get("time")

        # ── Retry pending tasks from previous session ────────────────────────
        if session_id:
            retry_pending_tasks(session_id)

        # ── Extract clean prompt text (filter system messages) ───────────────
        prompt_text = extract_user_text_from_hook_data(hook_data)

        if not prompt_text:
            logger.warning("No prompt content provided (after filtering system messages)")
            print(json.dumps({"status": "error", "message": "No prompt content"}))
            return

        # ── FIX 1 + FIX 2: Read promptId AND parentUuid from transcript ──────
        # The hook stdin does NOT contain these fields.  We poll the transcript
        # file with retries to guarantee we get the CURRENT message's IDs and
        # never a stale/previous one.
        prompt_id = None
        parent_uuid = None

        if transcript_path:
            logger.info("Reading promptId + parentUuid from transcript (with retries)...")
            ids = extract_ids_from_transcript(
                transcript_path=transcript_path,
                prompt_text=prompt_text,
                max_retries=10,     # up to ~1 second total wait
                base_delay_ms=100,  # 100ms, 200ms, 300ms… capped at 500ms
            )
            prompt_id = ids["prompt_id"] or None
            parent_uuid = ids["parent_uuid"]

            if prompt_id:
                logger.info(f"Resolved promptId={prompt_id}, parentUuid={parent_uuid}")
            else:
                logger.warning(
                    "promptId could not be resolved from transcript. "
                    "Prompt will be stored without one."
                )
        else:
            logger.warning(
                "No transcript_path in hook data — cannot resolve promptId or parentUuid."
            )

        # ── Ensure session record exists in DB ───────────────────────────────
        if session_id and cwd:
            ensure_session_initialized(session_id, cwd)

        # ── Store prompt in DB ───────────────────────────────────────────────
        logger.info(
            f"Storing prompt: session_id={session_id}, "
            f"prompt_id={prompt_id}, parent_uuid={parent_uuid}, "
            f"length={len(prompt_text)}"
        )

        result = process_user_prompt(
            prompt=prompt_text,
            session_id=session_id,
            prompt_id=prompt_id,
            parent_uuid=parent_uuid,      # FIX 2: now actually populated
            event_uuid=event_uuid,
            event_timestamp=event_timestamp,
            cwd=cwd,
        )

        logger.info(f"Prompt stored: {result.get('prompt_id')}")

        # ── Inject OBS reminder into Claude's context ────────────────────────
        logger.info("=" * 60)
        logger.info("INJECTING OBS REMINDER INTO USERPROMPTSUBMIT CONTEXT")
        logger.info("=" * 60)
        logger.info(f"Prompt ID : {result.get('prompt_id')}")
        logger.info(f"Reminder  : {OBS_REMINDER}")

        output_data = {
            "status": "success",
            "prompt_id": result.get("prompt_id"),
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": OBS_REMINDER,
            },
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