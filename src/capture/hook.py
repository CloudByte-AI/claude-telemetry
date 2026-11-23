"""
The hook-side entry point for edit evidence.

capture_post_tool_use(raw, client) is called by the post-tool hook with the raw hook input,
before anything else looks at it. It never raises and never prints: capture must not affect
the tool call or the turn.

Cost on the hot path (every tool call): a few byte searches, with nothing imported but `os`
and `time` (already loaded). Only a call that changed files (an edit tool, or a shell command
whose result carries a diff) parses the JSON, reads the settings (usually two stats) and
writes one file per changed file.
"""

import os
import time

_TOOL_NAME_KEY = b'"tool_name"'
_EDIT_TOOL_VALUES = (b'"Edit"', b'"Write"', b'"MultiEdit"', b'"NotebookEdit"')
_SHELL_DIFF = b'"bashEditDiff"'


def may_change_files(raw: bytes) -> bool:
    """A cheap pre-filter on the raw hook input: True when it may be an edit tool's call or
    carry a shell diff. False means it certainly changed no file (inside a JSON string a quote
    is escaped, so `"tool_name"` unescaped is a key or a value of exactly that text)."""
    if _SHELL_DIFF in raw:
        return True
    start = raw.find(_TOOL_NAME_KEY)
    while start >= 0:
        rest = raw[start + len(_TOOL_NAME_KEY):start + len(_TOOL_NAME_KEY) + 32].lstrip()
        if rest[:1] == b":" and rest[1:].lstrip().startswith(_EDIT_TOOL_VALUES):
            return True
        start = raw.find(_TOOL_NAME_KEY, start + 1)
    return False


def capture_post_tool_use(raw: bytes, client: str = "claude", now: float | None = None) -> str:
    """Record the evidence of one tool call. Returns what happened (for tests and logs):
    'not_edit', 'other_client', 'off', 'no_edit', 'written', 'write_failed' or 'error'."""
    try:
        if not raw or not may_change_files(raw):
            return "not_edit"
        import json
        payload = json.loads(raw.decode("utf-8-sig"))
        if not isinstance(payload, dict):
            return "not_edit"
        if "cursor_version" in payload:
            # Cursor can run another client's hooks; its own edits are recorded by its own
            # hook, so a Cursor call arriving here would be a second, mislabelled record.
            return "other_client"
        from src.capture.config import load_profile
        profile = load_profile(os.environ.get("CLAUDE_PROJECT_DIR") or None, payload.get("cwd"))
        if not profile.enabled:
            return "off"
        from src.capture.edits import build_edit_records
        now = time.time() if now is None else now
        records = build_edit_records(payload, client, profile, now)
        if not records:
            return "no_edit"
        from src.capture.spool import write_record
        written = [write_record(record, profile.retention_days, now) for record in records]
        return "written" if all(written) else "write_failed"
    except Exception:
        return "error"
