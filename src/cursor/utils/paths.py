"""
Cursor-specific path helpers.

Keeps Cursor's own log files physically separate from Claude Code's, without
touching src/common/paths.py's defaults (which stay pointed at Claude's
existing ~/.cloudbyte/logs/ files, unchanged).

The database is deliberately NOT split the same way - PROJECT/SESSION and
friends are one shared, cross-IDE store at ~/.cloudbyte/data/cloudbyte.db,
with SESSION.client distinguishing which plugin wrote each row.
"""

from pathlib import Path

from src.common.paths import get_cloudbyte_dir


def get_cursor_logs_dir() -> Path:
    """
    Get the log directory for the Cursor adapter.
    Typically: ~/.cloudbyte/logs/cursor/

    Returns:
        Path: The Cursor logs directory (not guaranteed to exist yet -
        setup_logging()'s log_dir handling creates it on first use).
    """
    return get_cloudbyte_dir() / "logs" / "cursor"
