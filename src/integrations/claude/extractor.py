"""
Data Extractors Module

Extracts specific data from Claude Code events and session data.
Uses UUIDResolver to handle parent-child relationships.
"""

import hashlib
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import uuid

from src.common.logging import get_logger
from src.integrations.claude.reader import normalize_project_name
from src.integrations.claude.uuid_resolver import UUIDResolver


logger = get_logger(__name__)


def generate_project_id(project_path: str) -> str:
    """
    Generate a unique project ID from the project path.

    Args:
        project_path: Full path to the project

    Returns:
        str: Unique project ID (hash of path)
    """
    return hashlib.md5(project_path.encode()).hexdigest()


def extract_project_info(cwd: str) -> Dict[str, Any]:
    """
    Extract project information from current working directory.

    Args:
        cwd: Current working directory path

    Returns:
        Dict with keys: project_id, name, path, created_at
    """
    # Extract project name from path (last folder name)
    path_obj = Path(cwd)
    project_name = path_obj.name

    # Normalize for Claude's folder naming
    normalized_name = normalize_project_name(cwd)

    return {
        "project_id": generate_project_id(cwd),
        "name": normalized_name,
        "path": cwd,
        "created_at": datetime.now().isoformat(),
    }


def extract_session_data(session_json: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract session data from session JSON file.

    Args:
        session_json: Session JSON from sessions/<pid>.json

    Returns:
        Dict with keys: session_id, project_id, cwd, jsonl_file, created_at, kind, entrypoint
    """
    cwd = session_json.get("cwd", "")
    project_info = extract_project_info(cwd)

    # Get JSONL file path
    project_name = normalize_project_name(cwd)
    session_id = session_json.get("sessionId", "")
    jsonl_file = f"{project_name}/{session_id}.jsonl"

    return {
        "session_id": session_json.get("sessionId"),
        "project_id": project_info["project_id"],
        "cwd": cwd,
        "jsonl_file": jsonl_file,
        "created_at": datetime.fromtimestamp(
            session_json.get("startedAt", 0) / 1000
        ).isoformat(),
        "kind": session_json.get("kind"),
        "entrypoint": session_json.get("entrypoint"),
    }


def extract_user_prompt(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract user prompt data from a user event.

    Args:
        event: Event dictionary with type="user"

    Returns:
        Dict with keys: prompt_id, session_id, uuid, parent_uuid, prompt, timestamp
    """
    message = event.get("message", {})
    content = message.get("content", "")

    # Handle both string content and list content (attachments)
    if isinstance(content, list):
        # Extract text from content array (skip tool_result items)
        text_parts = []
        for item in content:
            if isinstance(item, str):
                text_parts.append(item)
            elif isinstance(item, dict):
                # Skip tool_result items - they're not part of the user prompt text
                if item.get("type") == "tool_result":
                    continue
                text_parts.append(str(item.get("text", "")))
        content = " ".join(text_parts)

    return {
        "prompt_id": event.get("promptId") or event.get("uuid"),  # Fallback to uuid
        "session_id": event.get("sessionId"),
        "uuid": event.get("uuid"),
        "parent_uuid": event.get("parentUuid"),
        "prompt": str(content) if content else "",
        "timestamp": event.get("timestamp") or datetime.now().isoformat(),
    }


def extract_response(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract Claude response data from a text or assistant event.

    Args:
        event: Event dictionary with type="text" or type="assistant"

    Returns:
        Dict with keys: message_id, prompt_id, uuid, parent_uuid, response_text, model, timestamp

    Note:
        Only extracts final assistant responses (stop_reason == "end_turn").
        Skips intermediate responses (stop_reason == "tool_use").
    """
    message = event.get("message", {})

    # Only process final responses (stop_reason == "end_turn")
    # Skip intermediate responses (stop_reason == "tool_use")
    stop_reason = message.get("stop_reason")
    if stop_reason and stop_reason != "end_turn":
        # Return empty dict to signal "no response to extract"
        return {}

    content = message.get("content", "")

    # Handle content arrays (common in assistant events)
    if isinstance(content, list):
        text_parts = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    text_parts.append(item.get("text", ""))
                elif item.get("type") == "thinking":
                    # Skip thinking in response text
                    continue
            elif isinstance(item, str):
                text_parts.append(item)
        content = " ".join(text_parts)

    return {
        "message_id": message.get("id") or event.get("uuid"),  # Use message.id first, fallback to event.uuid
        "prompt_id": event.get("promptId") or event.get("parentUuid"),
        "uuid": event.get("uuid"),
        "parent_uuid": event.get("parentUuid"),
        "response_text": str(content) if content else "",
        "model": message.get("model") or event.get("model"),
        "timestamp": event.get("timestamp") or datetime.now().isoformat(),
    }


def extract_tool_use(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract tool usage data from a tool event.

    Args:
        event: Event dictionary with tool usage

    Returns:
        Dict with keys: tool_id, prompt_id, uuid, parent_uuid, tool_name, model, input_json, output_json, timestamp
    """
    message = event.get("message", {})
    tool_use = message.get("toolUse", {})

    return {
        "tool_id": event.get("uuid") or str(uuid.uuid4()),
        "prompt_id": event.get("promptId"),
        "uuid": event.get("uuid"),
        "parent_uuid": event.get("parentUuid"),
        "tool_name": tool_use.get("name"),
        "model": event.get("model"),
        "input_json": tool_use.get("input"),
        "output_json": event.get("output"),  # May be in a different format
        "timestamp": event.get("timestamp") or datetime.now().isoformat(),
    }


def extract_thinking(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract thinking process data from a thinking event.

    Args:
        event: Event dictionary with thinking data

    Returns:
        Dict with keys: thinking_id, prompt_id, uuid, parent_uuid, content, signature, timestamp
    """
    message = event.get("message", {})

    return {
        "thinking_id": event.get("uuid") or str(uuid.uuid4()),
        "prompt_id": event.get("promptId"),
        "uuid": event.get("uuid"),
        "parent_uuid": event.get("parentUuid"),
        "content": message.get("content"),
        "signature": message.get("signature"),
        "timestamp": event.get("timestamp") or datetime.now().isoformat(),
    }


def extract_token_usage(event: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Extract token usage data from an event.

    Args:
        event: Event dictionary that may contain token usage

    Returns:
        List of dicts with token data for IO_TOKENS and TOOL_TOKENS tables
    """
    tokens = []

    # Extract IO tokens from message
    message = event.get("message", {})
    usage = message.get("usage", {})

    if usage:
        prompt_id = event.get("promptId")
        message_id = event.get("uuid")
        timestamp = event.get("timestamp") or datetime.now().isoformat()

        # Main IO tokens
        io_token_data = {
            "id": str(uuid.uuid4()),
            "prompt_id": prompt_id,
            "message_id": message_id,
            "token_type": "io",
            "input_tokens": usage.get("inputTokens"),
            "cache_creation_tokens": usage.get("cacheCreationTokens"),
            "cache_read_tokens": usage.get("cacheReadTokens"),
            "output_tokens": usage.get("outputTokens"),
        }
        tokens.append(("io_tokens", io_token_data))

    # Extract tool-specific tokens if present
    if event.get("type") == "tool" or "toolUse" in message:
        tool_id = event.get("uuid")
        tool_usage = event.get("usage") or message.get("toolUse", {}).get("usage")

        if tool_usage:
            tool_token_data = {
                "id": str(uuid.uuid4()),
                "tool_id": tool_id,
                "input_tokens": tool_usage.get("inputTokens"),
                "cache_creation_tokens": tool_usage.get("cacheCreationTokens"),
                "cache_read_tokens": tool_usage.get("cacheReadTokens"),
                "output_tokens": tool_usage.get("outputTokens"),
            }
            tokens.append(("tool_tokens", tool_token_data))

    return tokens


def extract_raw_log(event: Dict[str, Any], session_id: str) -> Dict[str, Any]:
    """
    Extract raw log data for storing complete event JSON.

    Args:
        event: Full event dictionary
        session_id: Session ID

    Returns:
        Dict with keys: id, session_id, uuid, parent_uuid, type, raw_json, timestamp
    """
    import json

    return {
        "id": event.get("uuid") or str(uuid.uuid4()),
        "session_id": session_id,
        "uuid": event.get("uuid"),
        "parent_uuid": event.get("parentUuid"),
        "type": event.get("type"),
        "raw_json": json.dumps(event),
        "timestamp": event.get("timestamp") or datetime.now().isoformat(),
    }


def extract_observation(session_summary: Dict[str, Any], session_id: str) -> Dict[str, Any]:
    """
    Extract observation data from session summary.

    Args:
        session_summary: Parsed session summary
        session_id: Session ID

    Returns:
        Dict with keys for OBSERVATION table
    """
    return {
        "id": str(uuid.uuid4()),
        "session_id": session_id,
        "prompt_number": session_summary.get("stats", {}).get("total_user_messages"),
        "title": f"Session Observation - {session_summary.get('date', '')}",
        "subtitle": "",
        "narrative": "\n".join(session_summary.get("tasks", [])),
        "text": session_summary.get("notes", ""),
        "facts": "",
        "concepts": "",
        "type": "session_summary",
        "files_read": "",
        "files_modified": "",
        "content_hash": "",
        "created_at": datetime.now().isoformat(),
        "sync_status": "pending",
    }


def extract_session_summary(
    session_summary: Dict[str, Any],
    session_id: str,
    project_name: str,
) -> Dict[str, Any]:
    """
    Extract session summary data for SESSION_SUMMARY table.

    Args:
        session_summary: Parsed session summary
        session_id: Session ID
        project_name: Project name

    Returns:
        Dict with keys for SESSION_SUMMARY table
    """
    return {
        "id": str(uuid.uuid4()),
        "session_id": session_id,
        "project": project_name,
        "request": "\n".join(session_summary.get("tasks", [])),
        "investigated": "",
        "learned": "",
        "completed": "\n".join(session_summary.get("tools_used", [])),
        "next_steps": session_summary.get("notes", ""),
        "notes": session_summary.get("context", ""),
        "created_at": datetime.now().isoformat(),
        "sync_status": "pending",
    }


def get_event_type(event: Dict[str, Any]) -> str:
    """
    Get the event type from an event dictionary.

    Args:
        event: Event dictionary

    Returns:
        str: Event type (e.g., "user", "tool", "text", "thinking")
    """
    return event.get("type", "unknown")


def is_user_prompt(event: Dict[str, Any]) -> bool:
    """Check if event is a user prompt."""
    return get_event_type(event) == "user"


def is_tool_use(event: Dict[str, Any]) -> bool:
    """Check if event is a tool use."""
    event_type = get_event_type(event)
    return event_type == "tool_use" or event_type == "tool_result"


def is_response(event: Dict[str, Any]) -> bool:
    """Check if event is a Claude response."""
    return get_event_type(event) == "assistant"


def is_thinking(event: Dict[str, Any]) -> bool:
    """Check if event contains thinking data."""
    # Thinking is nested inside assistant message content
    if get_event_type(event) == "assistant":
        message = event.get("message", {})
        content = message.get("content", [])
        if isinstance(content, list):
            return any(item.get("type") == "thinking" for item in content)
    return False


def extract_all_from_event(
    event: Dict[str, Any],
    session_id: str,
    uuid_resolver: Optional[UUIDResolver] = None,
) -> Dict[str, List[Dict]]:
    """
    Extract all relevant data from an event.

    Args:
        event: Event dictionary
        session_id: Session ID
        uuid_resolver: Optional UUIDResolver for resolving parent relationships

    Returns:
        Dict with keys for each table that should receive data:
        {
            "raw_log": [...],
            "user_prompts": [...],
            "responses": [...],
            "tools": [...],
            "thinking": [...],
            "io_tokens": [...],
            "tool_tokens": [...],
        }
    """
    extracted = {
        "raw_log": [],
        "user_prompts": [],
        "responses": [],
        "tools": [],
        "thinking": [],
        "io_tokens": [],
        "tool_tokens": [],
    }

    # Always add raw log
    extracted["raw_log"].append(extract_raw_log(event, session_id))

    # Resolve prompt_id if not present
    event_uuid = event.get("uuid")
    if event_uuid and uuid_resolver:
        # If event doesn't have prompt_id, try to resolve it from parent chain
        if not event.get("promptId"):
            resolved_prompt_id = uuid_resolver.resolve_prompt_id(event_uuid)
            if resolved_prompt_id:
                event["promptId"] = resolved_prompt_id

    # Extract based on type
    if is_user_prompt(event):
        extracted["user_prompts"].append(extract_user_prompt(event))

    elif is_response(event):
        response_data = extract_response(event)
        # Only add response if extraction succeeded (not empty dict)
        if response_data:
            extracted["responses"].append(response_data)

    elif is_tool_use(event):
        extracted["tools"].append(extract_tool_use(event))

    elif is_thinking(event):
        extracted["thinking"].append(extract_thinking(event))

    # Extract tokens (may be present in any event type)
    token_data = extract_token_usage(event)
    for token_type, data in token_data:
        if token_type == "io_tokens":
            extracted["io_tokens"].append(data)
        elif token_type == "tool_tokens":
            extracted["tool_tokens"].append(data)

    return extracted


def extract_files_from_event(event: Dict[str, Any]) -> Dict[str, List[str]]:
    """
    Extract file paths mentioned in an event.

    Args:
        event: Event dictionary

    Returns:
        Dict with keys: files_read, files_modified
    """
    files_read = []
    files_modified = []

    # Check tool use events for file operations
    if is_tool_use(event):
        message = event.get("message", {})
        tool_use = message.get("toolUse", {})
        tool_name = tool_use.get("name", "")
        input_data = tool_use.get("input", {})

        # Read operations
        if tool_name == "Read" and "file_path" in input_data:
            files_read.append(input_data["file_path"])

        # Write/Edit operations
        elif tool_name in ["Write", "Edit"] and "file_path" in input_data:
            files_modified.append(input_data["file_path"])

        # Bash operations - might touch files
        elif tool_name == "Bash" and "command" in input_data:
            command = input_data["command"]
            # Simple heuristic - commands that read files
            if any(cmd in command.lower() for cmd in ["cat ", "less ", "head ", "tail ", "grep "]):
                # Could extract file paths from command
                pass

    return {
        "files_read": files_read,
        "files_modified": files_modified,
    }
