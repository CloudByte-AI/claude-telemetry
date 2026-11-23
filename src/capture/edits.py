"""
Evidence records from one tool call: one record per file the call changed.

The client's own result says exactly what changed. For Claude Code, PostToolUse's
`tool_response` (identical to the transcript's `toolUseResult`, verified 2026-11-23) carries:

    Edit                 structuredPatch: hunks {oldStart, oldLines, newStart, newLines, lines}
                         where each line starts with " " (context), "-" (removed) or "+" (added)
    Write, type=create   content: the whole new file (every line is the assistant's)
    Write, type=update   structuredPatch, as for Edit
    Bash                 bashEditDiff, when the command changed files:
                           files: [{filePath, hunks, created?, deleted?}]   diffs, same hunk shape
                           changedFiles: [path, ...]   every changed file; those beyond `files`
                                                      (moreFiles of them) have no diff
                           shared / unavailable: true  the client could not tell what changed
    NotebookEdit         not verified: the record names the notebook, no lines or counts
    MultiEdit            treated as Edit (structuredPatch); current Claude Code has no MultiEdit

A record keeps the hunks or the content as given, with counts, the ids that tie it to the
session, the prompt and the tool call, and when it happened. A failed call (the result is
error text, not an object) changed nothing and gives no record; neither does a shell command
whose changes the client could not tell (shared / unavailable).

Excluded paths (credentials, keys: config.DEFAULT_EXCLUDE plus the user's list) keep the
counts but never the lines. A record larger than MAX_RECORD_CHARS keeps the counts and is
marked truncated.

Stdlib only (fnmatch, json, posixpath, time: nothing heavier on the hook path).
"""

from __future__ import annotations

import fnmatch
import json
import posixpath
import time

FORMAT_VERSION = 1
EDIT_TOOLS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit"})
SHELL_TOOLS = frozenset({"Bash", "PowerShell"})
SHELL_DIFF_KEY = "bashEditDiff"
MAX_RECORD_CHARS = 1_000_000


def _iso(now: float) -> str:
    """ISO 8601 UTC with milliseconds, e.g. 2026-11-23T10:33:20.123Z (no datetime import)."""
    micros = round(now * 1_000_000)
    seconds, millis = divmod(micros // 1000, 1000)
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(seconds)) + f".{millis:03d}Z"


def excluded(path: str, patterns) -> bool:
    """True when `path` matches an exclusion glob. A pattern without a slash matches the file
    name (`*.pem`, `.env`); one with a slash matches the whole path or a trailing part of it
    (`private/*` matches /repo/private/notes.txt). Case is ignored on every OS, so a file is
    excluded or not the same way everywhere."""
    norm = str(path).replace("\\", "/").lower()
    base = posixpath.basename(norm)
    for pattern in patterns:
        p = str(pattern).replace("\\", "/").lower()
        if "/" in p:
            if fnmatch.fnmatchcase(norm, p) or fnmatch.fnmatchcase(norm, "*/" + p):
                return True
        elif fnmatch.fnmatchcase(base, p):
            return True
    return False


def _hunks(raw) -> list[dict]:
    """The client's patch hunks in a fixed shape; anything malformed is dropped."""
    out = []
    for h in raw if isinstance(raw, list) else []:
        if not isinstance(h, dict) or not isinstance(h.get("lines"), list):
            continue
        out.append({
            "old_start": h.get("oldStart"), "old_lines": h.get("oldLines"),
            "new_start": h.get("newStart"), "new_lines": h.get("newLines"),
            "lines": [str(line) for line in h["lines"]],
        })
    return out


def _counts(hunks: list[dict]) -> tuple[int, int]:
    added = removed = 0
    for h in hunks:
        for line in h["lines"]:
            if line.startswith("+"):
                added += 1
            elif line.startswith("-"):
                removed += 1
    return added, removed


def _record(payload: dict, client: str, now: float, path, op: str, added, removed, user_modified) -> dict:
    return {
        "v": FORMAT_VERSION,
        "kind": "edit",
        "time": _iso(now),
        "client": client,
        "session_id": payload.get("session_id"),
        "prompt_id": payload.get("prompt_id"),
        "tool_use_id": payload.get("tool_use_id"),
        "tool_name": payload.get("tool_name"),
        "cwd": payload.get("cwd"),
        "file": str(path),
        "op": op,
        "added": added,
        "removed": removed,
        "user_modified": user_modified if isinstance(user_modified, bool) else None,
        "duration_ms": payload.get("duration_ms"),
    }


def _with_lines(record: dict, profile, hunks: list | None = None, content: str | None = None) -> dict:
    """Attach the changed lines unless the path is excluded or the record would be too big."""
    if excluded(record["file"], profile.exclude):
        record["excluded"] = True                     # counts only, never the lines
        return record
    if content is not None:
        record["content"] = content
    elif hunks is not None:
        record["hunks"] = hunks
    if len(json.dumps(record, ensure_ascii=False)) > MAX_RECORD_CHARS:
        record.pop("content", None)
        record.pop("hunks", None)
        record["truncated"] = True
    return record


def _edit_tool_records(payload: dict, client: str, profile, now: float, result: dict) -> list[dict]:
    tool = payload.get("tool_name")
    tool_input = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
    path = (result.get("filePath") or tool_input.get("file_path")
            or result.get("notebook_path") or tool_input.get("notebook_path"))
    if not path:
        return []
    user_modified = result.get("userModified")

    if tool == "NotebookEdit":
        # Its result shape is not verified (no notebook edits in the data checked), so the
        # record says which notebook was edited and nothing about the lines.
        return [_with_lines(_record(payload, client, now, path, "notebook", None, None, user_modified), profile)]
    if tool == "Write" and result.get("type") == "create":
        content = result.get("content")
        if not isinstance(content, str):
            content = tool_input.get("content") if isinstance(tool_input.get("content"), str) else ""
        record = _record(payload, client, now, path, "create", len(content.splitlines()), 0, user_modified)
        return [_with_lines(record, profile, content=content)]
    hunks = _hunks(result.get("structuredPatch"))
    added, removed = _counts(hunks)
    op = "update" if tool == "Write" else "edit"
    return [_with_lines(_record(payload, client, now, path, op, added, removed, user_modified), profile, hunks=hunks)]


def _shell_records(payload: dict, client: str, profile, now: float, diff) -> list[dict]:
    if not isinstance(diff, dict):
        return []
    records, seen = [], set()
    for entry in diff.get("files") if isinstance(diff.get("files"), list) else []:
        if not isinstance(entry, dict) or not entry.get("filePath"):
            continue
        path = str(entry["filePath"])
        hunks = _hunks(entry.get("hunks"))
        added, removed = _counts(hunks)
        op = "create" if entry.get("created") is True else "delete" if entry.get("deleted") is True else "edit"
        records.append(_with_lines(_record(payload, client, now, path, op, added, removed, None), profile, hunks=hunks))
        seen.add(path)
    # Files the client lists as changed but gives no diff for (beyond its per-call limit).
    for path in diff.get("changedFiles") if isinstance(diff.get("changedFiles"), list) else []:
        if isinstance(path, str) and path and path not in seen:
            record = _record(payload, client, now, path, "changed", None, None, None)
            record["no_diff"] = True
            records.append(record)
            seen.add(path)
    return records


def build_edit_records(payload: dict, client: str, profile, now: float) -> list[dict]:
    """The evidence records for one tool call: one per changed file, [] when it changed none."""
    tool = payload.get("tool_name")
    result = payload.get("tool_response")
    if not isinstance(result, dict):
        return []                                     # error text: nothing happened
    if tool in EDIT_TOOLS:
        return _edit_tool_records(payload, client, profile, now, result)
    if tool in SHELL_TOOLS and SHELL_DIFF_KEY in result:
        return _shell_records(payload, client, profile, now, result[SHELL_DIFF_KEY])
    return []


def build_edit_record(payload: dict, client: str, profile, now: float) -> dict | None:
    """The single record of an edit tool call (Edit, Write, NotebookEdit), or None."""
    records = build_edit_records(payload, client, profile, now) if payload.get("tool_name") in EDIT_TOOLS else []
    return records[0] if records else None
