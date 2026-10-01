"""
Audit spool - one file per event.

Keeps SQLite off the hot path: the hook writes the event here, and a later hook
that nobody is waiting on drains it into TOOL_GUARDRAIL_EVENT (see db_writer).

One file per event, not one shared append-only file, because many processes and
threads write at once and appends are not atomic on Windows. Each event is
written to a temporary name and renamed into place (atomic within one
directory), so a reader sees the whole event or no file, and the drain deletes
exactly the files it read, so a write that lands mid-drain is not lost.

Deferred to drain time: masking evidence (the masker is too heavy to import on
every tool call; the spool sits beside the database in ~/.cloudbyte and evidence
is capped at 256 characters) and user_decision (see db_writer).
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from src.common.paths import get_guardrails_dir

SPOOL_DIRNAME = "audit_spool"
EVENT_SUFFIX = ".json"
TEMP_SUFFIX = ".tmp"

# Bounds disk use if the drain never runs. When exceeded, the OLDEST events are
# dropped, since recent events are the ones worth keeping.
MAX_SPOOL_FILES = 5000

# A temp file older than this was abandoned by a writer that died mid-write.
STALE_TEMP_SECONDS = 300


def spool_dir() -> Path:
    return get_guardrails_dir() / SPOOL_DIRNAME


def _row(decision, call) -> dict:
    return {
        "event_id": str(uuid.uuid4()),
        "session_id": call.session_id,
        "prompt_id": call.prompt_id,
        "tool_use_id": call.tool_use_id,
        "tool_name": call.tool_name,
        "platform": call.platform,
        "operation": decision.operation,
        "rule_id": decision.rule_id,
        "rule_rank": decision.rule_rank,
        "reason": decision.reason_text or None,
        "profile_hash": decision.profile_hash,
        "matcher_id": decision.matcher_id,
        "target": decision.target,
        "evidence": decision.evidence,
        "command_name": decision.command_name,
        "tool_input_hash": call.subject_hash,
        "action": decision.action,
        "alert_level": decision.alert_level,
        "decision_source": decision.source,
        # Not a column: tells the drain an ask was never shown (see db_writer).
        "prompted": decision.prompted,
        "eval_ms": decision.eval_ms,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def append_event(decision, call) -> bool:
    """
    Spool one decision. Returns True when the event was written.

    Never raises: an audit failure must never affect the tool call.
    """
    temporary = None
    try:
        directory = spool_dir()
        directory.mkdir(parents=True, exist_ok=True)
        row = _row(decision, call)

        # Timestamp first so a plain name sort is chronological; the event id
        # makes the name unique across processes writing in the same instant.
        stem = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')}-{row['event_id']}"
        final = directory / f"{stem}{EVENT_SUFFIX}"
        temporary = directory / f"{stem}{TEMP_SUFFIX}"

        temporary.write_text(json.dumps(row, default=str), encoding="utf-8")
        os.replace(temporary, final)       # atomic within one directory
        temporary = None

        _enforce_cap(directory)
        return True
    except Exception:
        if temporary is not None:
            try:
                os.remove(temporary)
            except Exception:
                pass
        return False


def read_batch() -> list[tuple[Path, dict]]:
    """
    Every spooled event with the file it came from, oldest first. Never raises.

    The path lets the drain delete exactly what it wrote to the database.
    Unparseable files are skipped here; remove_unreadable() deletes them.
    """
    batch: list[tuple[Path, dict]] = []

    directory = spool_dir()
    if not directory.exists():
        return batch

    try:
        files = sorted(directory.glob(f"*{EVENT_SUFFIX}"))
    except Exception:
        return batch

    for path in files:
        try:
            batch.append((path, json.loads(path.read_text(encoding="utf-8"))))
        except Exception:
            continue
    return batch


def read_events() -> list[dict]:
    """Every spooled row, oldest first. Never raises."""
    return [row for _, row in read_batch()]


def remove(paths) -> None:
    """Delete exactly these spool files. Never raises."""
    for path in set(paths):
        try:
            os.remove(path)
        except Exception:
            pass


def remove_unreadable() -> int:
    """
    Delete event files that will never parse, plus abandoned temp files.

    Returns how many were removed. A temp file counts as abandoned only once it
    is old enough that no live writer could still be about to rename it.
    """
    directory = spool_dir()
    if not directory.exists():
        return 0

    removed = 0
    now = datetime.now(timezone.utc).timestamp()
    try:
        for path in directory.glob(f"*{EVENT_SUFFIX}"):
            try:
                json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                try:
                    os.remove(path)
                    removed += 1
                except Exception:
                    pass
        for path in directory.glob(f"*{TEMP_SUFFIX}"):
            try:
                if now - path.stat().st_mtime > STALE_TEMP_SECONDS:
                    os.remove(path)
                    removed += 1
            except Exception:
                pass
    except Exception:
        pass
    return removed


def clear() -> None:
    """Remove every spooled event. Never raises. Tests and resets only."""
    remove(path for path, _ in read_batch())
    remove_unreadable()


def _enforce_cap(directory: Path) -> None:
    """Drop the oldest events once the spool exceeds MAX_SPOOL_FILES."""
    try:
        files = sorted(directory.glob(f"*{EVENT_SUFFIX}"))
        excess = len(files) - MAX_SPOOL_FILES
        if excess > 0:
            remove(files[:excess])
    except Exception:
        pass
