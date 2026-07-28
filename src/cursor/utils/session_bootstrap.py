"""
Cursor Session Bootstrap

Cursor's sessionStart hook only fires when a NEW composer conversation is
created (confirmed in context/cursor-plugin-docs/hooks.md: "Called when a
new composer conversation is created") - continuing a past conversation
never triggers it. That means PROJECT/SESSION rows normally created in
sessionStart (src/cursor/handlers/session_start.py) may simply not exist
yet when beforeSubmitPrompt fires for a resumed conversation.

Since USER_PROMPT.session_id has a foreign key to SESSION, writing a prompt
for a session that was never created fails silently (write_user_prompt()
catches the FK error and just returns False) - every prompt in that
conversation would be lost, forever, with no error ever surfacing to the
user.

ensure_session_and_project() is the fallback both session_start.py and
before_submit_prompt.py can call: cheap existence check first, so the
normal sessionStart-first flow never does extra work - it only creates
rows when they're genuinely missing.
"""

import hashlib
from pathlib import Path
from typing import Optional

from src.common.logging import get_logger
from src.common.time_utils import get_now_ist_iso
from src.db.manager import get_db_connection
from src.db.writers import DatabaseWriter


logger = get_logger(__name__)


def generate_project_id(project_path: str) -> str:
    """
    Generate a project ID using the same algorithm as the Claude adapter
    (src.integrations.claude.extractor.generate_project_id) so both IDEs
    map the same folder to the same PROJECT row. Keep byte-identical to
    the Claude version if either changes.
    """
    normalized = project_path.strip().lower().replace("\\", "/").rstrip("/")
    return hashlib.md5(normalized.encode()).hexdigest()


def ensure_session_and_project(
    session_id: Optional[str],
    cwd: Optional[str],
    transcript_path: Optional[str] = None,
) -> bool:
    """
    Ensure PROJECT + SESSION rows exist for this session, creating them if
    sessionStart never fired for it.

    Safe to call on every prompt - does a cheap SELECT first and only
    writes when the SESSION row is genuinely missing, so this never
    interferes with (or duplicates) the normal sessionStart-first flow.

    Returns True if the session exists or was just created, False if
    session_id/cwd weren't available to work with.
    """
    if not session_id or not cwd:
        return False

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM SESSION WHERE session_id = ? LIMIT 1", (session_id,))
        if cursor.fetchone() is not None:
            return True  # normal case - sessionStart already created it

        logger.info(
            f"SESSION not found for session_id={session_id!r} - sessionStart likely "
            f"never fired (resumed conversation); creating fallback PROJECT+SESSION rows"
        )

        project_id = generate_project_id(cwd)
        project_name = Path(cwd).name
        now = get_now_ist_iso()

        writer = DatabaseWriter()
        writer.write_project({
            "project_id": project_id,
            "name": project_name,
            "path": cwd,
            "created_at": now,
        })
        writer.write_session({
            "session_id": session_id,
            "project_id": project_id,
            "cwd": cwd,
            "transcript_path": transcript_path,
            "created_at": now,
            "client": "cursor",
        })
        return True

    except Exception as exc:
        logger.warning(
            f"ensure_session_and_project failed (session_id={session_id!r}): {exc}"
        )
        return False
