"""
Edit evidence for Cursor, from hooks the plugin already registers there (no new hook).

Cursor reports an agent edit as `Write` carrying the whole new file, and gives no line
positions (checked live on Cursor 3.24.12, 23 Nov 2026). So the lines come from the file
itself, read before and after the call:

    preToolUse            Write / StrReplace / EditNotebook / Delete: copy the target file
    (hook_entry cursor     (or note that it does not exist yet), keyed by tool_use_id
     pre_tool_use)
    postToolUse           the same call: read the file again and diff it against the copy:
    (src.cursor.main)       one evidence record, in the format of edits.py (client "cursor")
    beforeShellExecution  a shell command: snapshot.take_before (one git status + copies)
    postToolUse /         the same command: snapshot.take_after (exact lines per file)
    postToolUseFailure

Cursor's `afterFileEdit` is not used: Cursor waits for it, so it would add a hook start
(~1.5 s there) to every edit, and it carries no positions either.

Ids: session_id <- session_id or conversation_id, prompt_id <- generation_id (as the Cursor
handlers store them). Only Cursor's own install records (src/common/install_owner.py); the
callers check that. Never raises, never prints. Stdlib only.
"""

from __future__ import annotations

import os
import time

FILE_TOOLS = frozenset({"Write", "StrReplace", "EditNotebook", "Delete", "Edit"})
SHELL_TOOLS = frozenset({"Shell", "Bash"})
_TOOL_NAME_KEY = b'"tool_name"'
_FILE_TOOL_VALUES = tuple(f'"{name}"'.encode() for name in sorted(FILE_TOOLS))
_SHELL_TOOL_VALUES = tuple(f'"{name}"'.encode() for name in sorted(SHELL_TOOLS))


def _names(raw: bytes, values: tuple) -> bool:
    from src.capture.hook import _names_tool
    return _names_tool(raw, values)


def _payload(raw: bytes):
    import json
    payload = json.loads(raw.decode("utf-8-sig"))
    return payload if isinstance(payload, dict) else None


def normalise(payload: dict, tool_name: str | None = None) -> dict:
    """Cursor's payload in the shape edits.py and snapshot.py read."""
    tool_input = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
    command = payload.get("command") or tool_input.get("command")
    roots = payload.get("workspace_roots") if isinstance(payload.get("workspace_roots"), list) else []
    return {
        "session_id": payload.get("session_id") or payload.get("conversation_id"),
        "prompt_id": payload.get("generation_id"),
        "tool_use_id": payload.get("tool_use_id"),
        "tool_name": tool_name or payload.get("tool_name"),
        "cwd": tool_input.get("cwd") or payload.get("cwd") or (roots[0] if roots else None),
        "duration_ms": payload.get("duration"),
        "tool_input": {**tool_input, "command": command} if command else tool_input,
        "tool_response": {},
    }


def _profile(payload: dict):
    from src.capture.config import load_profile
    return load_profile(os.environ.get("CURSOR_PROJECT_DIR") or None, payload.get("cwd"))


def _file_path(payload: dict):
    tool_input = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
    return tool_input.get("file_path") or tool_input.get("path") or payload.get("file_path")


def _write(records, profile, now) -> bool:
    from src.capture.spool import write_record
    return all([write_record(record, profile.retention_days, now) for record in records])


# ── Before ────────────────────────────────────────────────────────────────────

def capture_before(raw: bytes, hook: str, now: float | None = None) -> str:
    """preToolUse (a file tool) or beforeShellExecution. Returns what happened."""
    try:
        shell_hook = hook == "before_shell_execution"
        if not raw or not (shell_hook or _names(raw, _FILE_TOOL_VALUES)):
            return "not_captured"
        payload = _payload(raw)
        if payload is None:
            return "not_captured"
        tool = "Shell" if shell_hook else payload.get("tool_name")
        if not shell_hook and tool not in FILE_TOOLS:
            return "not_captured"
        norm = normalise(payload, tool)
        profile = _profile(norm)
        if not profile.enabled:
            return "off"
        now = time.time() if now is None else now
        if shell_hook:
            from src.capture.snapshot import take_before
            outcome, notes = take_before(norm, "cursor", profile, now)
            if notes:
                _write(notes, profile, now)
            return outcome
        return _copy_file(norm, _file_path(payload), profile, now)
    except Exception:
        return "error"


def _copy_file(norm: dict, path, profile, now: float) -> str:
    import json
    from src.capture import snapshot
    from src.capture.edits import excluded
    if not path or not norm.get("tool_use_id"):
        return "no_file"
    path = os.path.abspath(str(path))
    state = {"v": 1, "kind": "file", "file": path, "time": now, "exists": os.path.lexists(path)}
    if excluded(path, profile.exclude):
        state["excluded"] = True                          # never copied
    else:
        content = snapshot._read(path)
        if content is not None:
            state["copy"] = snapshot._store_blob(content)
        elif state["exists"]:
            state["too_large"] = True
    snapshot._write_atomic(snapshot.pending_path(norm["tool_use_id"]), json.dumps(state).encode("utf-8"))
    return "copied"


# ── After ─────────────────────────────────────────────────────────────────────

def capture_after(raw: bytes, failed: bool = False, now: float | None = None) -> str:
    """postToolUse / postToolUseFailure for a file tool or a shell command."""
    try:
        if not raw:
            return "not_captured"
        is_file = _names(raw, _FILE_TOOL_VALUES)
        if not is_file and not _names(raw, _SHELL_TOOL_VALUES):
            return "not_captured"
        payload = _payload(raw)
        if payload is None:
            return "not_captured"
        tool = payload.get("tool_name")
        norm = normalise(payload)
        profile = _profile(norm)
        if not profile.enabled:
            return "off"
        now = time.time() if now is None else now
        if tool in SHELL_TOOLS:
            from src.capture.snapshot import take_after
            records = take_after(norm, "cursor", profile, now, failed) or []
        elif tool in FILE_TOOLS:
            records = _file_records(norm, payload, profile, now, failed)
        else:
            return "not_captured"
        if not records:
            return "no_edit"
        return "written" if _write(records, profile, now) else "write_failed"
    except Exception:
        return "error"


def _file_records(norm: dict, payload: dict, profile, now: float, failed: bool) -> list[dict]:
    from src.capture import snapshot
    from src.capture.edits import _record, _with_lines

    pending = snapshot.pending_path(norm.get("tool_use_id"))
    state = snapshot._load_json(pending)
    if isinstance(state, dict):
        try:
            os.remove(pending)
        except OSError:
            pass
    if not isinstance(state, dict) or state.get("kind") != "file":
        state = None
    path = (state or {}).get("file") or _file_path(payload)
    if not path:
        return []
    path = os.path.abspath(str(path))
    exists_now = os.path.lexists(path)
    content_now = snapshot._read(path)
    existed = state.get("exists") if state else None

    if not exists_now and not existed:
        return []                                         # nothing there before or after
    op = "delete" if not exists_now else "create" if existed is False else "update"
    record = _record(norm, "cursor", now, path, op, None, None, None)
    if failed:
        record["failed"] = True

    def no_diff(reason):
        record.update(no_diff=True, reason=reason)
        if reason == "excluded":
            record["excluded"] = True
        return [record]

    if state is None:
        return no_diff("no_before_copy")
    if state.get("excluded"):
        return no_diff("excluded")
    before = b"" if existed is False else snapshot._blob(state.get("copy"))
    if before is None:
        return no_diff("too_large" if state.get("too_large") else "no_before_copy")
    if exists_now and content_now is None:
        return no_diff("too_large")
    before_text, after_text = snapshot.decode_text(before), snapshot.decode_text(content_now)
    if before_text is None or after_text is None:
        return no_diff("binary")
    if before_text == after_text and op == "update":
        return []                                         # the call changed nothing
    before_lines, after_lines = before_text.splitlines(), after_text.splitlines()
    if op == "create":
        record.update(added=len(after_lines), removed=0)
        return [_with_lines(record, profile, content=after_text)]
    hunks = snapshot.diff_hunks(before_lines, after_lines)
    if hunks is None:
        return no_diff("diff_too_large")
    record["added"] = sum(1 for h in hunks for x in h["lines"] if x.startswith("+"))
    record["removed"] = sum(1 for h in hunks for x in h["lines"] if x.startswith("-"))
    return [_with_lines(record, profile, hunks=hunks)]
