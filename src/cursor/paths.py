"""
Cursor-specific path helpers.

Keeps Cursor's own log files physically separate from Claude Code's, without
touching src/common/paths.py's defaults (which stay pointed at Claude's
existing ~/.cloudbyte/logs/ files, unchanged).

The database is intentionally NOT split the same way in the long run -
PROJECT/SESSION and friends are meant to be one shared, cross-IDE store (see
SESSION.client). See CURSOR_TEST_DB_NAME below for the branch-local exception
to that while this adapter is still unverified.
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


# TEMPORARY - branch-local testing only.
#
# src/cursor/main.py sets CLOUDBYTE_DB_NAME to this value before dispatching
# to any handler, so every Cursor hook (current and future - no per-handler
# wiring needed) writes to its own cloudbyte-cursor-test.db instead of the
# shared cloudbyte.db, and can't corrupt real session history while this
# adapter is still unverified.
#
# TO REVERT BEFORE MERGING: delete (or comment out) the os.environ.setdefault
# line in src/cursor/main.py that references this constant. Once removed,
# Cursor sessions land in the same shared DB as Claude's, distinguished by
# SESSION.client - that was the whole point of that column.
CURSOR_TEST_DB_NAME = "cloudbyte-cursor-test"
