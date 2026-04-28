"""
Event Processor Module

Processes JSONL events from Claude Code sessions and routes them to appropriate handlers.
Uses UUIDResolver to handle parent-child event relationships.
"""

import os
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional

from src.common.logging import get_logger
from src.integrations.claude.reader import (
    get_claude_dir,
    normalize_project_name,
    read_jsonl_file,
    read_session_json,
    find_session_by_pid,
)
from src.integrations.claude.extractor import (
    extract_all_from_event,
    extract_session_data,
    extract_project_info,
    extract_files_from_event,
)
from src.integrations.claude.prompt_response import (
    extract_prompt_response_pairs,
    convert_pairs_to_db_format,
)
from src.db.writers import DatabaseWriter
from src.integrations.claude.uuid_resolver import UUIDResolver


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
                "jsonl_file": f"{project_info['name']}/{session_id}.jsonl",
                "created_at": datetime.now().isoformat(),
                "kind": "interactive",
                "entrypoint": "cli",
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
        prompt_id: Optional[str] = None,
        parent_uuid: Optional[str] = None,
        event_uuid: Optional[str] = None,
        event_timestamp: Optional[str] = None,
        cwd: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Process a user prompt event.

        Args:
            prompt: The user's prompt text (already filtered)
            session_id: Session UUID
            prompt_id: Optional prompt ID
            parent_uuid: Optional parent UUID
            event_uuid: Original event UUID from JSONL (if available)
            event_timestamp: Original event timestamp from JSONL (if available)
            cwd: Optional current working directory

        Returns:
            Dict with processed data
        """
        import uuid
        from datetime import datetime

        logger.debug(f"Processing user prompt for session: {session_id}")

        prompt_data = {
            "prompt_id": prompt_id or str(uuid.uuid4()),
            "session_id": session_id,
            "uuid": event_uuid or str(uuid.uuid4()),  # Use original if available
            "parent_uuid": parent_uuid,
            "prompt": prompt,
            "cwd": cwd,  # Pass cwd for project/session creation if needed
            "timestamp": event_timestamp or datetime.now().isoformat(),  # Use original if available
        }

        success = self.db_writer.write_user_prompt(prompt_data)

        # Also store as raw log
        raw_log = {
            "id": str(uuid.uuid4()),
            "session_id": session_id,
            "uuid": prompt_data["uuid"],
            "parent_uuid": parent_uuid,
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

    def process_jsonl_session(
        self,
        project_name: str,
        session_id: str,
        limit: Optional[int] = None,
    ) -> Dict[str, int]:
        """
        Process all events in a JSONL session file using the new prompt/response extractor.

        Args:
            project_name: Normalized project name
            session_id: Session UUID
            limit: Optional limit on number of events to process

        Returns:
            Dict with counts of processed events by type
        """
        from src.integrations.claude.reader import get_project_jsonl_path

        jsonl_path = get_project_jsonl_path(project_name, session_id, self.claude_dir)

        if not jsonl_path.exists():
            logger.warning(f"JSONL file not found: {jsonl_path}")
            return {}

        counts = {
            "processed": 0,
            "user_prompts": 0,
            "responses": 0,
            "tools": 0,
            "thinking": 0,
            "io_tokens": 0,
            "tool_tokens": 0,
            "errors": 0,
        }

        try:
            # Load all events from JSONL
            events = list(read_jsonl_file(jsonl_path))
            if limit and len(events) > limit:
                events = events[:limit]

            logger.info(f"Loaded {len(events)} events from {jsonl_path.name}")

            # Extract prompt/response pairs using the new extractor
            pairs = extract_prompt_response_pairs(events)

            # Convert to database format
            db_data = convert_pairs_to_db_format(pairs)

            # Write to database
            # SKIP: User prompts are already written by UserPromptSubmit hook
            # The hook now uses original event uuid/timestamp from JSONL
            # So we don't need to write user prompts from JSONL
            logger.debug("Skipping user prompts from JSONL (already written by UserPromptSubmit hook)")
            counts["user_prompts"] = 0

            for response in db_data.get("responses", []):
                if self.db_writer.write_response(response):
                    counts["responses"] += 1

            for tool in db_data.get("tools", []):
                if self.db_writer.write_tool(tool):
                    counts["tools"] += 1

            for thinking in db_data.get("thinking", []):
                if self.db_writer.write_thinking(thinking):
                    counts["thinking"] += 1

            for io_tokens in db_data.get("io_tokens", []):
                if self.db_writer.write_io_tokens(io_tokens):
                    counts["io_tokens"] += 1

            for tool_tokens in db_data.get("tool_tokens", []):
                if self.db_writer.write_tool_tokens(tool_tokens):
                    counts["tool_tokens"] += 1

            counts["processed"] = len(events)

            logger.info(f"Processed {counts['processed']} events from {jsonl_path.name}")
            logger.info(f"  - {counts['user_prompts']} user prompts written")
            logger.info(f"  - {counts['responses']} responses written")
            logger.info(f"  - {counts['tools']} tools written")
            logger.info(f"  - {counts['thinking']} thinking records written")
            logger.info(f"  - {counts['io_tokens']} IO token records written")
            logger.info(f"  - {counts['tool_tokens']} tool token records written")

            return counts

        except Exception as e:
            logger.error(f"Error processing JSONL file: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return counts

    def process_file_changes(
        self,
        project_name: str,
        session_id: str,
    ) -> Dict[str, List[str]]:
        """
        Extract file changes from a session.

        Args:
            project_name: Normalized project name
            session_id: Session UUID

        Returns:
            Dict with files_read and files_modified lists
        """
        from src.integrations.claude.reader import get_project_jsonl_path

        jsonl_path = get_project_jsonl_path(project_name, session_id, self.claude_dir)

        if not jsonl_path.exists():
            return {"files_read": [], "files_modified": []}

        files_read = set()
        files_modified = set()

        try:
            for event in read_jsonl_file(jsonl_path):
                extracted = extract_files_from_event(event)
                files_read.update(extracted.get("files_read", []))
                files_modified.update(extracted.get("files_modified", []))

            return {
                "files_read": list(files_read),
                "files_modified": list(files_modified),
            }

        except Exception as e:
            logger.error(f"Error extracting file changes: {e}")
            return {"files_read": [], "files_modified": []}

    def generate_observation(
        self,
        session_id: str,
        title: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Generate an observation from session data.

        NOTE: Currently disabled - not creating or writing observations.

        Args:
            session_id: Session UUID
            title: Optional custom title

        Returns:
            Dict with success status (no actual observation created)
        """
        logger.debug("Observation generation disabled - skipping")
        return {
            "observation_id": None,
            "status": "disabled",
            "files_read": 0,
            "files_modified": 0,
        }

    def process_session_end(
        self,
        session_id: str,
    ) -> Dict[str, Any]:
        """
        Process a session end event.

        NOTE: Session summary creation disabled - only processing remaining events.

        Args:
            session_id: Session UUID

        Returns:
            Dict with processed data
        """
        logger.info(f"Processing session end: {session_id}")

        # Get session info
        session_data = read_session_json(session_id, self.claude_dir)
        if not session_data:
            logger.warning(f"Session not found: {session_id}")
            return {"status": "error", "message": "Session not found"}

        cwd = session_data.get("cwd", "")
        logger.debug(f"Session cwd: {cwd}")

        project_name = normalize_project_name(cwd)
        logger.debug(f"Normalized project name: {project_name}")

        project_info = extract_project_info(cwd)
        logger.debug(f"Project info name: {project_info.get('name')}")

        # Process any remaining events in JSONL
        counts = self.process_jsonl_session(project_name, session_id)

        # Session summary creation disabled
        logger.debug("Session summary creation disabled - skipping")

        return {
            "session_id": session_id,
            "status": "success",
            "counts": counts,
            "summary_id": None,  # Disabled
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
    prompt_id: Optional[str] = None,
    parent_uuid: Optional[str] = None,
    event_uuid: Optional[str] = None,
    event_timestamp: Optional[str] = None,
    cwd: Optional[str] = None,
) -> Dict[str, Any]:
    """Process user prompt event."""
    processor = EventProcessor()
    return processor.process_user_prompt(prompt, session_id, prompt_id, parent_uuid, event_uuid, event_timestamp, cwd)


def generate_observation(
    session_id: str,
    title: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Generate observation from session.

    NOTE: Currently disabled - returns disabled status.

    Args:
        session_id: Session UUID
        title: Optional custom title

    Returns:
        Dict with disabled status
    """
    logger.debug("Observation generation disabled - skipping")
    return {
        "observation_id": None,
        "status": "disabled",
        "files_read": 0,
        "files_modified": 0,
    }


def process_session_end(session_id: str) -> Dict[str, Any]:
    """Process session end event."""
    processor = EventProcessor()
    return processor.process_session_end(session_id)
