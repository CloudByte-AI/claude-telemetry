"""
Proof that a shown ask was approved - for its audit row's user_decision.

The drain (db_writer) labels an ask approved when the tool's TOOL row holds a
result, and rejected when the turn ended without one. A tool call made inside a
subagent never gets a TOOL row, so every ask a subagent raised was labelled
rejected, approved or not. The PostToolUse hook is better evidence: Claude Code
fires it only after the tool has run, which for an ask means the user approved.

So, for every ask the user is shown:

    PreToolUse      writes  asks/<h(tool_use_id)>.shown
    PostToolUse     renames it to  <h>.ran            (the tool ran: approved)
    the drain       reads <h>.ran -> approved, then removes the marker

One empty file per ask, atomic renames, hashed names (a tool_use_id is not a
safe file name everywhere). A marker whose ask is never resolved - a rejected
ask whose turn ended - is removed with the rest by the drain, and anything left
behind (a crash, a deleted database) by sweep() after a day.

Independent of remembered approvals (approvals.py): asks are labelled the same
way whether or not a policy remembers them. Never raises.
"""

from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path

_DIRNAME = "asks"
SHOWN = ".shown"
RAN = ".ran"
# Anything older than this is swept (sweep()).
SWEEP_AGE_SECS = 24 * 3600


def asks_dir() -> Path:
    from src.common.paths import get_guardrails_dir
    return get_guardrails_dir() / _DIRNAME


def _name(tool_use_id: str) -> str:
    return hashlib.sha256(str(tool_use_id).encode("utf-8", errors="replace")).hexdigest()[:40]


def note_shown(tool_use_id: str | None) -> bool:
    """PreToolUse: the user is being asked about this call. True when noted."""
    if not tool_use_id:
        return False
    try:
        directory = asks_dir()
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{_name(tool_use_id)}{SHOWN}").touch()
        return True
    except Exception:
        return False


def note_ran(tool_use_id: str | None) -> bool:
    """
    PostToolUse: the call ran. True when it was a shown ask, now marked as
    approved; False for every other call (one failed rename).
    """
    if not tool_use_id:
        return False
    try:
        directory = asks_dir()
        stem = _name(tool_use_id)
        os.replace(directory / f"{stem}{SHOWN}", directory / f"{stem}{RAN}")
        return True
    except Exception:
        return False


def has_ran(tool_use_id: str | None) -> bool:
    """Whether PostToolUse saw this asked-about call run."""
    if not tool_use_id:
        return False
    try:
        return (asks_dir() / f"{_name(tool_use_id)}{RAN}").exists()
    except Exception:
        return False


def forget(tool_use_ids) -> int:
    """Remove the markers of resolved asks. Returns how many files went."""
    removed = 0
    try:
        directory = asks_dir()
    except Exception:
        return 0
    for tool_use_id in tool_use_ids or ():
        if not tool_use_id:
            continue
        stem = _name(tool_use_id)
        for suffix in (SHOWN, RAN):
            try:
                os.remove(directory / f"{stem}{suffix}")
                removed += 1
            except OSError:
                pass
    return removed


def sweep(now: float | None = None, max_age: float = SWEEP_AGE_SECS) -> int:
    """Remove markers untouched for `max_age`. Never raises."""
    current = time.time() if now is None else now
    removed = 0
    try:
        directory = asks_dir()
        if not directory.is_dir():
            return 0
        for marker in directory.iterdir():
            try:
                if marker.suffix in (SHOWN, RAN) and current - marker.stat().st_mtime > max_age:
                    marker.unlink()
                    removed += 1
            except OSError:
                pass
    except Exception:
        pass
    return removed
