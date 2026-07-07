"""
Cursor SessionStart Handler

Called when a new Cursor composer conversation is created (hook: sessionStart).
Unlike the Claude Code adapter, this does not read or parse any transcript/JSONL
file — Cursor sessions are captured entirely from the hook's stdin payload.

Payload shape, per context/cursor-plugin-docs/hooks.md:
  Common schema (present on every hook): conversation_id, generation_id, model,
    model_id, model_params, hook_event_name, cursor_version, workspace_roots,
    user_email, transcript_path.
  sessionStart-specific fields: session_id (same value as conversation_id),
    is_background_agent, composer_mode.
  Notably absent: prompt text (sessionStart has none to give) and any explicit
  timestamp field — created_at is stamped locally instead.

sessionStart is fire-and-forget: Cursor does not wait for or enforce a
response, so failures here are logged and swallowed rather than surfaced.
"""

import hashlib
import json
import re
import sys
from pathlib import Path

from src.common.logging import get_logger, setup_logging
from src.common.time_utils import get_now_ist_iso
from src.cursor.paths import get_cursor_logs_dir
from src.db.writers import DatabaseWriter


logger = get_logger(__name__)


def _read_stdin_data() -> dict:
    """Read the JSON hook payload Cursor sends via stdin."""
    try:
        raw = sys.stdin.buffer.read()
    except Exception as e:
        logger.error(f"Error reading stdin: {e}")
        return {}

    if not raw:
        logger.debug("stdin was empty")
        return {}

    # Decode with utf-8-sig, not plain utf-8: strips a leading BOM if present
    # and is otherwise identical to utf-8. A bare utf-8 decode leaves the BOM
    # in place, which json.loads() then rejects with a misleading
    # "Expecting value: line 1 column 1" error - this codebase has hit the
    # same BOM class of bug before, in settings.json handling.
    text = raw.decode("utf-8-sig", errors="replace").strip()
    if not text:
        return {}

    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        logger.error(f"Error parsing stdin JSON: {e}")
        logger.error(f"Raw stdin received (first 500 chars): {text[:500]!r}")
        return {}


_LEADING_SLASH_BEFORE_DRIVE = re.compile(r"^/([A-Za-z]:)")


def _normalize_cwd(cwd: str) -> str:
    """
    Strip a leading '/' before a Windows drive letter.

    Cursor's workspace_roots reports Windows paths as '/c:/Users/...'
    (confirmed directly from a real Cursor CLI session) instead of the plain
    'C:/Users/...' or 'C:\\Users\\...' form Claude Code uses for the same
    folder. Left alone, this breaks cross-IDE project_id matching (see
    _generate_project_id below) - the same physical folder would hash to two
    different project_id values depending on which IDE reported it.

    Genuine POSIX paths (starting with '/' but with no drive letter right
    after it, e.g. '/home/user/project') don't match the pattern and are
    returned unchanged.
    """
    return _LEADING_SLASH_BEFORE_DRIVE.sub(r"\1", cwd)


def _generate_project_id(project_path: str) -> str:
    """
    Generate a project ID using the same algorithm as the Claude adapter
    (src.integrations.claude.extractor.generate_project_id), so the same
    project folder maps to the same PROJECT row regardless of which IDE
    was used to open it.

    Deliberately duplicated rather than imported: the Cursor and Claude
    adapters are being kept isolated until enough shared logic accumulates
    to justify extracting a common module. This is a known candidate for
    that extraction — keep it byte-identical to the Claude version if
    either changes.
    """
    normalized = project_path.strip().lower().replace("\\", "/").rstrip("/")
    return hashlib.md5(normalized.encode()).hexdigest()


def _debug(message: str) -> None:
    """
    Print a visible status line to stderr - safe to leave on permanently.

    stdout is reserved strictly for the hook's final JSON response (Cursor's
    command-hook loader parses stdout as that response), so all human-visible
    "is this thing running" output goes to stderr instead. Also shows up in
    Cursor's Hooks output channel when run as a real hook, not just here.
    """
    print(f"[cursor-telemetry] {message}", file=sys.stderr, flush=True)


def handle_session_start() -> None:
    """
    Handle Cursor's sessionStart hook: persist PROJECT + SESSION rows.
    """
    _debug("sessionStart handler triggered")
    setup_logging(log_to_file=True, log_to_console=False, log_dir=get_cursor_logs_dir())
    logger.info("=== Cursor SessionStart Handler ===")

    try:
        hook_data = _read_stdin_data()
        logger.debug(f"sessionStart payload keys: {list(hook_data.keys())}")

        session_id = hook_data.get("session_id") or hook_data.get("conversation_id")
        workspace_roots = hook_data.get("workspace_roots") or []
        cwd = _normalize_cwd(workspace_roots[0]) if workspace_roots else None

        if len(workspace_roots) > 1:
            logger.warning(
                f"Multiple workspace_roots reported ({len(workspace_roots)}); "
                f"using the first one only: {cwd}"
            )

        if not session_id or not cwd:
            _debug(f"skipped - incomplete payload (session_id={session_id!r}, cwd={cwd!r})")
            logger.warning(
                f"Incomplete sessionStart payload - session_id={session_id!r}, "
                f"cwd={cwd!r}. Skipping session creation."
            )
            print(json.dumps({}))
            return

        project_id = _generate_project_id(cwd)
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
            # Stored for reference only - null if the user has transcripts
            # disabled, and never read/parsed by this adapter.
            "jsonl_file": hook_data.get("transcript_path"),
            "created_at": now,
            "client": "cursor",
        })

        _debug(f"session created - session_id={session_id}, project={project_name}")
        logger.info(f"Cursor session created: session_id={session_id}, project={project_name}")
        print(json.dumps({}))

    except Exception as e:
        _debug(f"ERROR - {e}")
        logger.error(f"Error in Cursor SessionStart handler: {e}", exc_info=True)
        print(json.dumps({}))


def main() -> None:
    handle_session_start()


if __name__ == "__main__":
    main()
    