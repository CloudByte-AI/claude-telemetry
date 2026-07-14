"""
Reads a Cursor composer's title from Cursor's own local storage.

No hook exposes a title field, so this is a deliberate exception to the
hooks-only design: it reads state.vscdb, Cursor's own SQLite database,
directly. That file is live (Cursor's own process writes to it continuously
via a WAL) but is opened read-only here - SQLite's own WAL-reading logic
gives any reader a consistent snapshot including committed WAL frames, so
no manual WAL merge is needed as long as the sqlite3 module itself opens
the connection (not a custom byte-level parser).
"""

import json
import sqlite3
import sys
from pathlib import Path

from src.cursor.utils.hook_io import debug


def _state_db_path() -> Path:
    home = Path.home()
    if sys.platform == "win32":
        return home / "AppData" / "Roaming" / "Cursor" / "User" / "globalStorage" / "state.vscdb"
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "Cursor" / "User" / "globalStorage" / "state.vscdb"
    return home / ".config" / "Cursor" / "User" / "globalStorage" / "state.vscdb"


def get_composer_title(session_id: str) -> str | None:
    """
    Best-effort lookup of a Cursor composer's title.

    Returns None if the composer has no title yet (a session with no real
    conversation content has no "name" key at all - not an error, just not
    titled), or on any failure (file missing, locked, malformed, etc). Never
    raises.
    """
    db_path = _state_db_path()
    if not db_path.exists():
        return None

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=3)
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT value FROM cursorDiskKV WHERE key = ?",
                (f"composerData:{session_id}",),
            )
            row = cursor.fetchone()
            if not row:
                return None
            title = json.loads(row[0]).get("name")
            return title if title else None
        finally:
            conn.close()
    except Exception as e:
        debug(f"composer title lookup failed for {session_id}: {e}")
        return None
