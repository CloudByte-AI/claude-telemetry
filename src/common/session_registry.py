"""
Active Session Registry

Tracks which sessions are currently active, across BOTH Claude Code and
Cursor (and any future plugin), so the shared background worker/dashboard
at localhost:8765 is never torn down while another plugin's session is
still using it.

Same proven pattern as src/cursor/utils/obs_state.py - one JSON file per
key in a directory under ~/.cloudbyte, fail-safe on any error, no shared
file to race on.

Directory layout:
    ~/.cloudbyte/active_sessions/
        <session_id>.json    ← {"client": "claude_code"|"cursor", "last_seen": "..."}

Lifecycle (both plugins call the same functions):
    sessionStart / SessionStart  → register(session_id, client)
    every prompt (heartbeat)     → register(session_id, client)   [also the
                                    fallback if sessionStart never fired -
                                    e.g. Cursor only fires sessionStart for a
                                    genuinely NEW composer conversation, never
                                    when an existing one is resumed - so this
                                    is the only place that session ever gets
                                    registered at all]
    sessionEnd / SessionEnd      → unregister(session_id) then check
                                    has_other_active_sessions() before
                                    killing the shared worker

A session whose entry hasn't been touched in STALE_AFTER_SECONDS is
ignored by has_other_active_sessions() - this is what prevents a crashed
session (whose sessionEnd hook never fires) from permanently blocking the
worker from ever shutting down.
"""

import json
from pathlib import Path
from typing import Optional

from src.common.logging import get_logger
from src.common.paths import get_cloudbyte_dir
from src.common.time_utils import get_now_ist_iso


logger = get_logger(__name__)

STALE_AFTER_SECONDS = 24 * 60 * 60  # 24 hours - see module docstring


def _registry_dir() -> Path:
    return get_cloudbyte_dir() / "active_sessions"


def _entry_path(session_id: str) -> Path:
    return _registry_dir() / f"{session_id}.json"


def register(session_id: Optional[str], client: str) -> None:
    """Mark a session as active. Safe to call repeatedly (overwrites)."""
    if not session_id:
        return
    try:
        _registry_dir().mkdir(parents=True, exist_ok=True)
        _entry_path(session_id).write_text(
            json.dumps({"client": client, "last_seen": get_now_ist_iso()}),
            encoding="utf-8",
        )
        logger.debug(f"session_registry: registered session={session_id[:8]} client={client!r}")
    except Exception as exc:
        logger.warning(f"session_registry.register failed (session={session_id!r}): {exc}")


def unregister(session_id: Optional[str]) -> None:
    """Mark a session as no longer active. Silent no-op if already gone."""
    if not session_id:
        return
    try:
        _entry_path(session_id).unlink(missing_ok=True)
        logger.debug(f"session_registry: unregistered session={session_id[:8]}")
    except Exception as exc:
        logger.warning(f"session_registry.unregister failed (session={session_id!r}): {exc}")


def has_other_active_sessions(exclude_session_id: Optional[str] = None) -> bool:
    """
    Return True if any OTHER session is still active (fresh, non-stale entry).

    Fail-safe direction: any error scanning the registry returns True (assume
    something else might still be active) rather than False - a bug here must
    never cause the shared worker to be killed out from under a real session.
    """
    from datetime import datetime

    try:
        reg_dir = _registry_dir()
        if not reg_dir.exists():
            return False

        now = datetime.fromisoformat(get_now_ist_iso())

        for entry_file in reg_dir.glob("*.json"):
            if entry_file.stem == exclude_session_id:
                continue
            try:
                data = json.loads(entry_file.read_text(encoding="utf-8"))
                last_seen = datetime.fromisoformat(data["last_seen"])
                age_seconds = (now - last_seen).total_seconds()
                if age_seconds <= STALE_AFTER_SECONDS:
                    logger.debug(
                        f"session_registry: active session found - "
                        f"{entry_file.stem[:8]} (age={age_seconds:.0f}s)"
                    )
                    return True
                else:
                    logger.debug(
                        f"session_registry: ignoring stale entry {entry_file.stem[:8]} "
                        f"(age={age_seconds:.0f}s > {STALE_AFTER_SECONDS}s)"
                    )
            except Exception as exc:
                logger.debug(f"session_registry: could not read {entry_file.name}: {exc}")
                continue

        return False

    except Exception as exc:
        logger.warning(f"session_registry.has_other_active_sessions failed: {exc} - assuming active (fail-safe)")
        return True
