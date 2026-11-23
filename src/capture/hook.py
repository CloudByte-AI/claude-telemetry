"""
The hook-side entry points for edit evidence.

    capture_pre_tool_use(raw)     PreToolUse: a shell call's before-snapshot (snapshot.py)
    capture_post_tool_use(raw)    PostToolUse: an edit tool's own result (edits.py), or a shell
                                  call's after-snapshot; PostToolUseFailure (failed=True) for a
                                  shell call that failed, which can still have changed files

Each is called with the raw hook input before anything else looks at it. They never raise
and never print: capture must not affect the tool call, the guardrails answer or the turn.

Cost on the hot path (every tool call): a few byte searches, with nothing imported but `os`
and `time` (already loaded). Only a call that may change files (an edit tool, a shell
command) parses the JSON and reads the settings (usually two stats); a shell call then runs
`git status` once before and once after (snapshot.py).
"""

import os
import time

_TOOL_NAME_KEY = b'"tool_name"'
_EDIT_TOOL_VALUES = (b'"Edit"', b'"Write"', b'"MultiEdit"', b'"NotebookEdit"')
_SHELL_TOOL_VALUES = (b'"Bash"', b'"PowerShell"')
_SHELL_DIFF = b'"bashEditDiff"'


def _names_tool(raw: bytes, values: tuple) -> bool:
    """Whether the raw input's `tool_name` is one of `values`. Inside a JSON string a quote is
    escaped, so `"tool_name"` unescaped is a key or a value of exactly that text."""
    start = raw.find(_TOOL_NAME_KEY)
    while start >= 0:
        rest = raw[start + len(_TOOL_NAME_KEY):start + len(_TOOL_NAME_KEY) + 32].lstrip()
        if rest[:1] == b":" and rest[1:].lstrip().startswith(values):
            return True
        start = raw.find(_TOOL_NAME_KEY, start + 1)
    return False


# Where snapshot.py keeps unfinished before-snapshots: config.CAPTURE_RELATIVE_DIR + shell/pending,
# duplicated so this check imports nothing (asserted equal in tests/capture).
_PENDING_RELATIVE_DIR = (".cloudbyte", "capture", "shell", "pending")


def _has_pending_snapshot() -> bool:
    """Whether any shell call is waiting for its after-snapshot (one directory listing)."""
    try:
        with os.scandir(os.path.join(os.path.expanduser("~"), *_PENDING_RELATIVE_DIR)) as entries:
            return any(entry.name.endswith(".json") for entry in entries)
    except OSError:
        return False


def may_change_files(raw: bytes) -> bool:
    """A cheap pre-filter: False means this call certainly left no evidence to record."""
    if _SHELL_DIFF in raw or _names_tool(raw, _EDIT_TOOL_VALUES):
        return True
    return _names_tool(raw, _SHELL_TOOL_VALUES) and _has_pending_snapshot()


def _payload(raw: bytes):
    import json
    payload = json.loads(raw.decode("utf-8-sig"))
    return payload if isinstance(payload, dict) else None


def _profile(payload: dict):
    from src.capture.config import load_profile
    return load_profile(os.environ.get("CLAUDE_PROJECT_DIR") or None, payload.get("cwd"))


def _write(records, profile, now) -> bool:
    from src.capture.spool import write_record
    return all([write_record(record, profile.retention_days, now) for record in records])


def capture_pre_tool_use(raw: bytes, client: str = "claude", now: float | None = None) -> str:
    """Take a shell call's before-snapshot. Returns what happened (for tests and logs):
    'not_shell', 'other_client', 'off', or the snapshot outcome ('taken', 'no_repo', ...)."""
    try:
        if not raw or not _names_tool(raw, _SHELL_TOOL_VALUES):
            return "not_shell"
        payload = _payload(raw)
        if payload is None or payload.get("tool_name") not in ("Bash", "PowerShell"):
            return "not_shell"
        if "cursor_version" in payload:
            return "other_client"                     # see capture_post_tool_use
        profile = _profile(payload)
        if not profile.enabled:
            return "off"
        from src.capture.snapshot import take_before
        now = time.time() if now is None else now
        outcome, notes = take_before(payload, client, profile, now)
        if notes:
            _write(notes, profile, now)
        return outcome
    except Exception:
        return "error"


def capture_post_tool_use(raw: bytes, client: str = "claude", now: float | None = None,
                          failed: bool = False) -> str:
    """Record the evidence of one tool call. Returns what happened (for tests and logs):
    'not_edit', 'other_client', 'off', 'no_edit', 'written', 'write_failed' or 'error'."""
    try:
        if not raw or not may_change_files(raw):
            return "not_edit"
        payload = _payload(raw)
        if payload is None:
            return "not_edit"
        if "cursor_version" in payload:
            # Cursor can run another client's hooks; its own edits are recorded by its own
            # hook, so a Cursor call arriving here would be a second, mislabelled record.
            return "other_client"
        profile = _profile(payload)
        if not profile.enabled:
            return "off"
        now = time.time() if now is None else now
        records = None
        if payload.get("tool_name") in ("Bash", "PowerShell"):
            from src.capture.snapshot import take_after
            records = take_after(payload, client, profile, now, failed)   # None: no snapshot
        if records is None:
            if failed:
                return "no_edit"
            from src.capture.edits import build_edit_records
            records = build_edit_records(payload, client, profile, now)  # the client's own diff
        if not records:
            return "no_edit"
        return "written" if _write(records, profile, now) else "write_failed"
    except Exception:
        return "error"
