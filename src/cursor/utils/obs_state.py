"""
Cursor OBS State — per-turn context injection tracking.

Tracks whether the OBS_REMINDER has already been injected into the model
context for the current generation (turn) via postToolUse's additional_context.

Uses a folder-per-session, file-per-generation layout so concurrent sessions
and subagents are fully isolated — each only ever touches its own files.

Directory layout:
    ~/.cloudbyte/cursor_obs/
        <session_id>/
            <generation_id>.json    ← {"injected": false|true}

Lifecycle:
    beforeSubmitPrompt  → create(session_id, generation_id)
    postToolUse         → check_and_mark(session_id, generation_id) → bool
    afterAgentResponse  → delete(session_id, generation_id)
    sessionEnd          → delete_session(session_id)

All public functions are safe-by-default:
  - missing/corrupt state file → treat as not-yet-injected (inject = safe)
  - None/empty ids             → skip silently (no-op or safe default)
  - any I/O failure            → log as warning, never raise
"""

import json
import shutil
from pathlib import Path

from src.common.logging import get_logger
from src.common.paths import get_cloudbyte_dir


logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Internal path helpers
# ---------------------------------------------------------------------------

def _state_dir() -> Path:
    return get_cloudbyte_dir() / "cursor_obs"


def _session_dir(session_id: str) -> Path:
    return _state_dir() / session_id


def _gen_path(session_id: str, generation_id: str) -> Path:
    return _session_dir(session_id) / f"{generation_id}.json"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def create(session_id: str | None, generation_id: str | None) -> None:
    """
    Called at beforeSubmitPrompt. Creates the state file for this turn.

    Also removes any stale .json files left over from the previous turn
    (covers the case where afterAgentResponse never fired — e.g. an aborted
    turn — so old state doesn't block injection on the new turn).
    """
    if not session_id or not generation_id:
        logger.debug(
            f"obs_state.create: skipped — "
            f"session_id={session_id!r}, generation_id={generation_id!r}"
        )
        return

    try:
        sess_dir = _session_dir(session_id)
        sess_dir.mkdir(parents=True, exist_ok=True)

        for stale in sess_dir.glob("*.json"):
            if stale.stem != generation_id:
                try:
                    stale.unlink()
                    logger.debug(f"obs_state: removed stale gen file {stale.name} for session={session_id[:8]}")
                except Exception as exc:
                    logger.warning(f"obs_state: could not remove stale gen file {stale.name}: {exc}")

        gen_file = sess_dir / f"{generation_id}.json"
        gen_file.write_text(json.dumps({"injected": False}), encoding="utf-8")
        logger.info(
            f"obs_state: turn state created — "
            f"session={session_id[:8]}, gen={generation_id[:8]}"
        )

    except Exception as exc:
        logger.warning(
            f"obs_state.create failed "
            f"(session={session_id!r}, gen={generation_id!r}): {exc}"
        )


def check_and_mark(session_id: str | None, generation_id: str | None) -> bool:
    """
    Called at postToolUse. Returns True if OBS_REMINDER should be injected.

    Returns True  (inject) when:
      • state file is missing or unreadable  — safe fallback
      • state file exists with injected=False — first tool call this turn

    Returns False (skip) when:
      • state file exists with injected=True  — already done this turn

    Writes injected=True whenever it returns True, so subsequent calls in the
    same turn always get False (no double injection).
    """
    if not session_id or not generation_id:
        logger.debug("obs_state.check_and_mark: missing ids — defaulting to inject")
        return True

    gen_file = _gen_path(session_id, generation_id)

    try:
        if not gen_file.exists():
            logger.info(
                f"obs_state: state file missing for gen={generation_id[:8]} "
                f"(beforeSubmitPrompt may not have fired) — injecting as fallback"
            )
            _write_injected(session_id, generation_id)
            return True

        state = json.loads(gen_file.read_text(encoding="utf-8"))

        if state.get("injected"):
            logger.debug(
                f"obs_state: reminder already injected this turn — "
                f"skipping gen={generation_id[:8]}"
            )
            return False

        _write_injected(session_id, generation_id)
        logger.info(
            f"obs_state: injecting OBS reminder — "
            f"session={session_id[:8]}, gen={generation_id[:8]}"
        )
        return True

    except Exception as exc:
        logger.warning(
            f"obs_state.check_and_mark failed "
            f"(session={session_id!r}, gen={generation_id!r}): {exc} — defaulting to inject"
        )
        return True  # Fail-safe: inject rather than silently drop the reminder


def delete(session_id: str | None, generation_id: str | None) -> None:
    """
    Called at afterAgentResponse. Removes the state file for this turn.
    Silent no-op if the file doesn't exist (e.g. zero-tool turn).
    """
    if not session_id or not generation_id:
        return

    try:
        gen_file = _gen_path(session_id, generation_id)
        existed = gen_file.exists()
        gen_file.unlink(missing_ok=True)
        if existed:
            logger.info(
                f"obs_state: turn state deleted — "
                f"session={session_id[:8]}, gen={generation_id[:8]}"
            )
        else:
            logger.debug(
                f"obs_state: no state file to delete (zero-tool turn) — "
                f"gen={generation_id[:8]}"
            )
    except Exception as exc:
        logger.warning(
            f"obs_state.delete failed "
            f"(session={session_id!r}, gen={generation_id!r}): {exc}"
        )


def delete_session(session_id: str | None) -> None:
    """
    Called at sessionEnd. Removes the entire session folder and all its files.
    Safety net for sessions where the last turn was aborted (afterAgentResponse
    never fired, so the last gen file was never cleaned up).
    Silent no-op if the folder doesn't exist.
    """
    if not session_id:
        return

    try:
        sess_dir = _session_dir(session_id)
        if sess_dir.exists():
            shutil.rmtree(sess_dir)
            logger.info(f"obs_state: session state folder deleted — session={session_id[:8]}")
        else:
            logger.debug(f"obs_state: no state folder to delete — session={session_id[:8]}")
    except Exception as exc:
        logger.warning(f"obs_state.delete_session failed (session={session_id!r}): {exc}")


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------

def _write_injected(session_id: str, generation_id: str) -> None:
    """Write injected=True to the state file. Creates the session dir if needed."""
    try:
        sess_dir = _session_dir(session_id)
        sess_dir.mkdir(parents=True, exist_ok=True)
        gen_file = sess_dir / f"{generation_id}.json"
        gen_file.write_text(json.dumps({"injected": True}), encoding="utf-8")
    except Exception as exc:
        logger.warning(f"obs_state._write_injected failed (gen={generation_id!r}): {exc}")
