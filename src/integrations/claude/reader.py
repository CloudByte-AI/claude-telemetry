"""
Claude Data Reader Module

Reads data from the .claude folder structure:
- projects/<project-name>/<session-id>.jsonl
- sessions/<pid>.json
- session-data/<date>-<name>-session.tmp
"""

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Generator

from src.common.logging import get_logger


logger = get_logger(__name__)


# Claude directory constants
DEFAULT_CLAUDE_DIR = Path.home() / ".claude"
PROJECTS_DIR_NAME = "projects"
SESSIONS_DIR_NAME = "sessions"
SESSION_DATA_DIR_NAME = "session-data"
HISTORY_FILE_NAME = "history.jsonl"


def get_claude_dir(claude_dir: Optional[Path] = None) -> Path:
    """
    Get the .claude directory path.

    Args:
        claude_dir: Optional custom path to .claude directory

    Returns:
        Path: The .claude directory path
    """
    if claude_dir is None:
        claude_dir = DEFAULT_CLAUDE_DIR
    return claude_dir


def get_projects_dir(claude_dir: Optional[Path] = None) -> Path:
    """
    Get the projects directory path.

    Args:
        claude_dir: Optional custom path to .claude directory

    Returns:
        Path: The projects directory path
    """
    return get_claude_dir(claude_dir) / PROJECTS_DIR_NAME


def get_sessions_dir(claude_dir: Optional[Path] = None) -> Path:
    """
    Get the sessions directory path.

    Args:
        claude_dir: Optional custom path to .claude directory

    Returns:
        Path: The sessions directory path
    """
    return get_claude_dir(claude_dir) / SESSIONS_DIR_NAME


def get_session_data_dir(claude_dir: Optional[Path] = None) -> Path:
    """
    Get the session-data directory path.

    Args:
        claude_dir: Optional custom path to .claude directory

    Returns:
        Path: The session-data directory path
    """
    return get_claude_dir(claude_dir) / SESSION_DATA_DIR_NAME


def get_history_file_path(claude_dir: Optional[Path] = None) -> Path:
    """
    Get the history.jsonl file path.

    Args:
        claude_dir: Optional custom path to .claude directory

    Returns:
        Path: The history.jsonl file path
    """
    return get_claude_dir(claude_dir) / HISTORY_FILE_NAME


def normalize_project_name(cwd: str) -> str:
    """
    Normalize a cwd path to match Claude's project folder naming convention.

    Claude replaces special characters (':', '\\', '_', ' ', '&', etc.) with '-' in project names.

    Args:
        cwd: Current working directory path

    Returns:
        str: Normalized project name

    Examples:
        >>> normalize_project_name("D:\\CloudByte_plugin\\CloudByte")
        "D--CloudByte-plugin-CloudByte"
        >>> normalize_project_name("/home/user/projects/my-app")
        "-home-user-projects-my-app"
    """
    # Replace backslashes with forward slashes
    normalized = cwd.replace("\\", "/")

    # Replace colons with dashes
    normalized = normalized.replace(":", "-")

    # Replace underscores with dashes (Claude does this too)
    normalized = normalized.replace("_", "-")

    # Replace spaces with dashes (Claude does this too)
    normalized = normalized.replace(" ", "-")

    # Replace ampersands with dashes (Claude does this too)
    normalized = normalized.replace("&", "-")

    # Replace all other special characters with dashes
    for char in ['!', '@', '#', '$', '%', '^', '*', '(', ')', '+', '=', '[', ']', '{', '}', '|', ';', "'", '"', '<', '>', ',', '?', '~', '`']:
        normalized = normalized.replace(char, "-")

    # Replace forward slashes with dashes
    normalized = normalized.replace("/", "-")

    # Handle edge case where path starts with dash (absolute Unix paths)
    if normalized.startswith("-") and not normalized.startswith("--"):
        normalized = "-" + normalized

    return normalized


def get_project_dir(project_name: str, claude_dir: Optional[Path] = None) -> Path:
    """
    Get the project directory path for a specific project.

    Args:
        project_name: Name of the project (normalized)
        claude_dir: Optional custom path to .claude directory

    Returns:
        Path: The project directory path
    """
    return get_projects_dir(claude_dir) / project_name


def get_project_jsonl_path(project_name: str, session_id: str, claude_dir: Optional[Path] = None) -> Path:
    """
    Get the JSONL file path for a specific project session.

    Args:
        project_name: Name of the project (normalized)
        session_id: Session UUID
        claude_dir: Optional custom path to .claude directory

    Returns:
        Path: The JSONL file path
    """
    jsonl_path = get_project_dir(project_name, claude_dir) / f"{session_id}.jsonl"
    logger.debug(f"Constructed JSONL path: {jsonl_path}")
    logger.debug(f"Project name: {project_name}")
    logger.debug(f"Session ID: {session_id}")
    logger.debug(f"Path exists: {jsonl_path.exists()}")
    return jsonl_path


def list_projects(claude_dir: Optional[Path] = None) -> List[str]:
    """
    List all project names in the .claude/projects directory.

    Args:
        claude_dir: Optional custom path to .claude directory

    Returns:
        List[str]: List of project names
    """
    projects_dir = get_projects_dir(claude_dir)

    if not projects_dir.exists():
        logger.warning(f"Projects directory does not exist: {projects_dir}")
        return []

    return [d.name for d in projects_dir.iterdir() if d.is_dir()]


def list_session_files(project_name: str, claude_dir: Optional[Path] = None) -> List[Path]:
    """
    List all JSONL session files for a project.

    Args:
        project_name: Name of the project (normalized)
        claude_dir: Optional custom path to .claude directory

    Returns:
        List[Path]: List of JSONL file paths
    """
    project_dir = get_project_dir(project_name, claude_dir)

    if not project_dir.exists():
        logger.warning(f"Project directory does not exist: {project_dir}")
        return []

    return list(project_dir.glob("*.jsonl"))


def read_session_json(session_id: str, claude_dir: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """
    Read session metadata from sessions/<pid>.json.

    Note: This requires knowing the PID, not the session ID.
    You may need to search for the right file.

    Args:
        session_id: Session UUID
        claude_dir: Optional custom path to .claude directory

    Returns:
        Optional[Dict]: Session data or None if not found
    """
    sessions_dir = get_sessions_dir(claude_dir)

    if not sessions_dir.exists():
        return None

    # Search for a session file containing the session_id
    for session_file in sessions_dir.glob("*.json"):
        try:
            with open(session_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                if data.get("sessionId") == session_id:
                    return data
        except Exception as e:
            logger.error(f"Error reading session file {session_file}: {e}")
            continue

    return None


def find_session_by_pid(pid: int, claude_dir: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """
    Find session metadata by PID.

    Args:
        pid: Process ID
        claude_dir: Optional custom path to .claude directory

    Returns:
        Optional[Dict]: Session data or None if not found
    """
    session_file = get_sessions_dir(claude_dir) / f"{pid}.json"

    if not session_file.exists():
        return None

    try:
        with open(session_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Error reading session file {session_file}: {e}")
        return None


def read_jsonl_file(file_path: Path) -> Generator[Dict[str, Any], None, None]:
    """
    Read a JSONL file and yield each line as a dictionary.

    Args:
        file_path: Path to the JSONL file

    Yields:
        Dict: Each line parsed as JSON

    Raises:
        FileNotFoundError: If the file doesn't exist
        json.JSONDecodeError: If a line is not valid JSON
    """
    if not file_path.exists():
        raise FileNotFoundError(f"JSONL file not found: {file_path}")

    with open(file_path, "r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:  # Skip empty lines
                continue

            try:
                yield json.loads(line)
            except json.JSONDecodeError as e:
                logger.error(f"Error parsing line {line_num} in {file_path}: {e}")
                # Skip invalid lines
                continue


def read_jsonl_as_list(file_path: Path, max_lines: Optional[int] = None) -> List[Dict[str, Any]]:
    """
    Read a JSONL file and return all lines as a list of dictionaries.

    Args:
        file_path: Path to the JSONL file
        max_lines: Maximum number of lines to read (None = all)

    Returns:
        List[Dict]: List of parsed JSON objects
    """
    events = []
    for i, event in enumerate(read_jsonl_file(file_path)):
        if max_lines is not None and i >= max_lines:
            break
        events.append(event)
    return events


def get_session_events(project_name: str, session_id: str, claude_dir: Optional[Path] = None) -> List[Dict[str, Any]]:
    """
    Get all events for a specific session.

    Args:
        project_name: Name of the project (normalized)
        session_id: Session UUID
        claude_dir: Optional custom path to .claude directory

    Returns:
        List[Dict]: List of event dictionaries
    """
    jsonl_path = get_project_jsonl_path(project_name, session_id, claude_dir)

    if not jsonl_path.exists():
        logger.warning(f"Session JSONL not found: {jsonl_path}")
        return []

    return read_jsonl_as_list(jsonl_path)


def get_event_by_type(
    project_name: str,
    session_id: str,
    event_type: str,
    claude_dir: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """
    Get all events of a specific type from a session.

    Args:
        project_name: Name of the project (normalized)
        session_id: Session UUID
        event_type: Type of event to filter (e.g., "user", "tool", "text")
        claude_dir: Optional custom path to .claude directory

    Returns:
        List[Dict]: List of matching event dictionaries
    """
    events = get_session_events(project_name, session_id, claude_dir)
    return [e for e in events if e.get("type") == event_type]


def get_session_data_files(claude_dir: Optional[Path] = None) -> List[Path]:
    """
    Get all session data files from session-data directory.

    Args:
        claude_dir: Optional custom path to .claude directory

    Returns:
        List[Path]: List of session data file paths
    """
    session_data_dir = get_session_data_dir(claude_dir)

    if not session_data_dir.exists():
        return []

    return list(session_data_dir.glob("*.tmp"))


def read_session_data_file(file_path: Path) -> str:
    """
    Read a session data file (usually markdown formatted).

    Args:
        file_path: Path to the session data file

    Returns:
        str: File contents
    """
    if not file_path.exists():
        raise FileNotFoundError(f"Session data file not found: {file_path}")

    with open(file_path, "r", encoding="utf-8") as f:
        return f.read()


def parse_session_summary(content: str) -> Dict[str, Any]:
    """
    Parse session summary from session data file content.

    Args:
        content: Session data file content (markdown format)

    Returns:
        Dict: Parsed summary data with keys: date, tasks, tools_used, stats, notes, context
    """
    summary = {
        "date": None,
        "tasks": [],
        "tools_used": [],
        "stats": {},
        "notes": "",
        "context": "",
    }

    lines = content.split("\n")
    current_section = None

    for line in lines:
        line = line.strip()

        # Extract date
        if line.startswith("**Date:**"):
            summary["date"] = line.split("**Date:**")[1].strip()

        # Extract tasks
        elif line.startswith("- ") and current_section == "Tasks":
            summary["tasks"].append(line[2:].strip())

        # Extract tools
        elif line.startswith("- ") and current_section == "Tools Used":
            summary["tools_used"].append(line[2:].strip())

        # Extract stats
        elif line.startswith("- ") and current_section == "Stats":
            stat_parts = line[2:].split(":")
            if len(stat_parts) == 2:
                key = stat_parts[0].strip().replace(" ", "_")
                value = stat_parts[1].strip()
                summary["stats"][key] = value

        # Section headers
        elif line.startswith("### Tasks"):
            current_section = "Tasks"
        elif line.startswith("### Tools Used"):
            current_section = "Tools Used"
        elif line.startswith("### Stats"):
            current_section = "Stats"
        elif line.startswith("### Notes for Next Session"):
            current_section = "Notes"
        elif line.startswith("### Context to Load"):
            current_section = "Context"

    return summary


def get_current_session_id() -> Optional[str]:
    """
    Try to get the current session ID from environment or context.

    This is a placeholder - in actual hook context, the session ID
    would be provided by Claude Code.

    Returns:
        Optional[str]: Session ID if available
    """
    # Claude Code may provide this via environment variable or stdin
    # This is a placeholder for actual implementation
    return os.environ.get("CLAUDE_SESSION_ID")


def get_current_pid() -> Optional[int]:
    """
    Try to get the current process ID.

    Returns:
        Optional[int]: PID if available
    """
    # Claude Code may provide this via environment variable or stdin
    return os.environ.get("CLAUDE_PID", "").strip() or None
