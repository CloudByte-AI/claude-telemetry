"""
Claude Code integration module.

This module handles reading and extracting data from Claude Code's
internal data structures and session files.
"""

from src.integrations.claude.reader import (
    get_claude_dir,
    get_projects_dir,
    get_sessions_dir,
    get_session_data_dir,
    get_history_file_path,
    normalize_project_name,
    get_project_dir,
    get_project_jsonl_path,
    list_projects,
    list_session_files,
    read_jsonl_file,
    read_session_json,
    find_session_by_pid,
)

__all__ = [
    "get_claude_dir",
    "get_projects_dir",
    "get_sessions_dir",
    "get_session_data_dir",
    "get_history_file_path",
    "normalize_project_name",
    "get_project_dir",
    "get_project_jsonl_path",
    "list_projects",
    "list_session_files",
    "read_jsonl_file",
    "read_session_json",
    "find_session_by_pid",
]
