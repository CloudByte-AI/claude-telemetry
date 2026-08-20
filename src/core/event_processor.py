"""
Event Processor Module

Creates the PROJECT/SESSION records on session start and the USER_PROMPT/RAW_LOG
records on prompt submit, for the Claude Code hooks.

Everything downstream of a prompt (responses, tools, thinking, tokens) is written
by src/main.py's stop hook and src/core/recovery.py, which read the JSONL
directly - this module deliberately does not duplicate that.
"""

from pathlib import Path
from typing import Any, Dict, Optional

from src.common.logging import get_logger
from src.common.time_utils import get_now_ist_iso, to_ist
from src.integrations.claude.reader import (
    get_claude_dir,
    read_session_json,
    find_session_by_pid,
)
from src.integrations.claude.extractor import (
    extract_session_data,
    extract_project_info,
)
from src.db.writers import DatabaseWriter


logger = get_logger(__name__)


class EventProcessor:
    """
    Processes Claude Code session events and writes them to the database.
    """

    def __init__(self, claude_dir: Optional[Path] = None, db_writer: Optional[DatabaseWriter] = None):
        """
        Initialize the event processor.

        Args:
            claude_dir: Optional custom path to .claude directory
            db_writer: Optional DatabaseWriter instance
        """
        self.claude_dir = claude_dir or get_claude_dir()
        self.db_writer = db_writer or DatabaseWriter()

    def process_session_start(
        self,
        session_id: Optional[str] = None,
        pid: Optional[int] = None,
        cwd: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Process a session start event.

        Args:
            session_id: Session UUID (if known)
            pid: Process ID (if known)
            cwd: Current working directory (if known)

        Returns:
            Dict with processed data
        """
        logger.info(f"Processing session start: session_id={session_id}, pid={pid}")

        # Try to find session data
        session_data = None

        if pid:
            session_data = find_session_by_pid(pid, self.claude_dir)
        elif session_id:
            session_data = read_session_json(session_id, self.claude_dir)

        if session_data:
            # Extract session data
            extracted = extract_session_data(session_data)

            # Write project
            project_info = extract_project_info(session_data.get("cwd", cwd or ""))
            self.db_writer.write_project(project_info)

            # Write session
            self.db_writer.write_session(extracted)

            return {
                "session_id": extracted["session_id"],
                "project_id": extracted["project_id"],
                "cwd": extracted["cwd"],
                "status": "success",
            }
        elif cwd:
            # Create session from cwd only
            project_info = extract_project_info(cwd)
            self.db_writer.write_project(project_info)

            # Create a basic session record
            import uuid
            from datetime import datetime

            session_id = session_id or str(uuid.uuid4())
            extracted = {
                "session_id": session_id,
                "project_id": project_info["project_id"],
                "cwd": cwd,
                "transcript_path": f"{project_info['name']}/{session_id}.jsonl",
                "created_at": get_now_ist_iso(),
                "kind": "interactive",
                "entrypoint": "cli",
                "client": "claude_code",
            }

            self.db_writer.write_session(extracted)

            return {
                "session_id": extracted["session_id"],
                "project_id": extracted["project_id"],
                "cwd": extracted["cwd"],
                "status": "created",
            }
        else:
            logger.error("Cannot process session start: no session data or cwd provided")
            return {"status": "error", "message": "No session data available"}

    def process_user_prompt(
        self,
        prompt: str,
        session_id: str,
        prompt_id: str,
        mode: Optional[str] = None,
        entrypoint: Optional[str] = None,
        client_version: Optional[str] = None,
        git_branch: Optional[str] = None,
        event_timestamp: Optional[str] = None,
        cwd: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Process a user prompt event.

        prompt_id is required and must be the hook's own `prompt_id` - the same
        UUID Claude Code puts on PreToolUse/PostToolUse/Stop for this turn. It is
        never generated here: a locally minted id is precisely what forced stop()
        to fuzzy-match prompt text against the transcript to recover the real one.

        Args:
            prompt: The user's prompt text (already filtered)
            session_id: Session UUID
            prompt_id: The hook's prompt_id for this turn (required)
            mode: Permission mode from the hook (default/plan/acceptEdits/...)
            entrypoint: Launch mode from $CLAUDE_CODE_ENTRYPOINT (cli, claude-vscode)
            client_version: Claude Code version parsed from $AI_AGENT
            git_branch: Current branch derived from .git/HEAD under cwd
            event_timestamp: Original event timestamp (if available)
            cwd: Optional current working directory

        Returns:
            Dict with processed data
        """
        import uuid

        logger.debug(f"Processing user prompt for session: {session_id}")

        if not prompt_id:
            logger.error("process_user_prompt called without a prompt_id - refusing to write")
            return {"prompt_id": None, "status": "error"}

        prompt_data = {
            "prompt_id": prompt_id,
            "session_id": session_id,
            "prompt": prompt,
            "mode": mode,
            "entrypoint": entrypoint,
            "client_version": client_version,
            "git_branch": git_branch,
            "cwd": cwd,  # Pass cwd for project/session creation if needed
            "timestamp": to_ist(event_timestamp),  # Falls back to IST now if None
        }

        success = self.db_writer.write_user_prompt(prompt_data)

        # Also store as raw log.
        # uuid/parent_uuid are the transcript record's identifiers, which the
        # hook payload does not carry - left NULL rather than inventing a value
        # that links to nothing.
        raw_log = {
            "id": str(uuid.uuid4()),
            "session_id": session_id,
            "uuid": None,
            "parent_uuid": None,
            "type": "user",
            "raw_json": f'{{"type": "user", "prompt": "{prompt[:100]}..."}}',
            "timestamp": prompt_data["timestamp"],
            "cwd": cwd,  # Include cwd for session creation
        }
        self.db_writer.write_raw_log(raw_log)

        return {
            "prompt_id": prompt_data["prompt_id"],
            "status": "success" if success else "error",
        }

# Convenience functions

def process_session_start(
    session_id: Optional[str] = None,
    pid: Optional[int] = None,
    cwd: Optional[str] = None,
) -> Dict[str, Any]:
    """Process session start event."""
    processor = EventProcessor()
    return processor.process_session_start(session_id, pid, cwd)


def process_user_prompt(
    prompt: str,
    session_id: str,
    prompt_id: str,
    mode: Optional[str] = None,
    entrypoint: Optional[str] = None,
    client_version: Optional[str] = None,
    git_branch: Optional[str] = None,
    event_timestamp: Optional[str] = None,
    cwd: Optional[str] = None,
) -> Dict[str, Any]:
    """Process user prompt event."""
    processor = EventProcessor()
    return processor.process_user_prompt(
        prompt, session_id, prompt_id, mode,
        entrypoint, client_version, git_branch,
        event_timestamp, cwd,
    )
