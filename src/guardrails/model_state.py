"""
Which model a session is using, for clients that do not say it with every prompt.

Claude Code names the model in three places, none of them the prompt hook:

    SessionStart       `model`, only when the session starts fresh
    PostModelSwitch    `to_model`, after every change, including the ones the
                       client makes itself (a resumed session's model)
    the transcript     `message.model` on every assistant reply

The two hooks write a small state file per session; current_model() combines
it with the transcript and takes whichever is newer. One case is left open on
purpose: a client too old for the model-switch events changes the model with
`/model` after a startup record. The transcript then shows a newer `/model`
command than the state, and the model counts as unknown until the next reply
names it, rather than staying pinned to the model the user just left.

State files live under ~/.cloudbyte/guardrails/model_state/ and are swept a
day after their last write. Every function here never raises.
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from src.common.paths import get_guardrails_dir

STATE_DIRNAME = "model_state"
SOURCE_STARTUP = "startup"      # SessionStart's `model`
SOURCE_SWITCH = "switch"        # PostModelSwitch's `to_model`

# How much of the transcript's end is read: enough for several turns, and a
# fixed cost however long the session has run.
TRANSCRIPT_TAIL_BYTES = 256 * 1024
SWEEP_AFTER_SECONDS = 24 * 60 * 60

_MODEL_COMMAND = "<command-name>/model</command-name>"
_UNSAFE = re.compile(r"[^A-Za-z0-9_.-]")


def _log():
    """The logger, imported on first use - see config._log() for why."""
    from src.common.logging import get_logger
    return get_logger(__name__)


def state_dir() -> Path:
    return get_guardrails_dir() / STATE_DIRNAME


def _path(session_id: str) -> Path:
    return state_dir() / f"{_UNSAFE.sub('_', session_id)[:128]}.json"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_time(value) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def record(session_id: str | None, model: str | None, source: str) -> bool:
    """Remember the session's model. Returns True when written."""
    if not session_id or not isinstance(model, str) or not model.strip():
        return False
    try:
        from src.common.atomic_files import write_atomically
        path = _path(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        write_atomically(path, json.dumps({"model": model.strip(), "source": source,
                                            "at": _now().isoformat()}))
        return True
    except Exception as exc:
        _log().debug(f"model state: could not record the model for {session_id!r}: {exc}")
        return False


def recorded(session_id: str | None) -> dict | None:
    """The state file's contents ({model, source, at}), or None."""
    if not session_id:
        return None
    try:
        state = json.loads(_path(session_id).read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(state, dict) or not isinstance(state.get("model"), str):
        return None
    return state


def sweep(max_age_seconds: int = SWEEP_AFTER_SECONDS) -> int:
    """Delete state files not written for `max_age_seconds`. Returns how many went."""
    removed = 0
    cutoff = time.time() - max_age_seconds
    try:
        with os.scandir(state_dir()) as entries:
            for entry in entries:
                try:
                    if entry.is_file() and entry.stat().st_mtime < cutoff:
                        os.remove(entry.path)
                        removed += 1
                except OSError:
                    continue
    except OSError:
        pass
    return removed


def _tail_lines(transcript_path: str) -> list[str]:
    with open(transcript_path, "rb") as handle:
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        start = max(0, size - TRANSCRIPT_TAIL_BYTES)
        handle.seek(start)
        data = handle.read()
    lines = data.decode("utf-8", errors="replace").splitlines()
    return lines[1:] if start else lines     # the first line may be cut


def _is_model_command(record: dict) -> bool:
    if record.get("type") == "system":
        content = record.get("content")
    elif record.get("type") == "user":
        content = (record.get("message") or {}).get("content")
    else:
        return False
    return isinstance(content, str) and _MODEL_COMMAND in content


def from_transcript(transcript_path: str | None) -> tuple[str | None, datetime | None, datetime | None]:
    """
    (model of the last assistant reply, when it was written, when the last
    `/model` command was run) from the end of the transcript. Synthetic
    replies (a model name in angle brackets) do not count.
    """
    model = model_at = command_at = None
    if not transcript_path:
        return model, model_at, command_at
    try:
        lines = _tail_lines(transcript_path)
    except Exception:
        return model, model_at, command_at
    for line in reversed(lines):
        if model is not None and command_at is not None:
            break
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict):
            continue
        if model is None and entry.get("type") == "assistant":
            name = (entry.get("message") or {}).get("model")
            if isinstance(name, str) and name and not name.startswith("<"):
                model, model_at = name, _parse_time(entry.get("timestamp"))
        elif command_at is None and _is_model_command(entry):
            command_at = _parse_time(entry.get("timestamp"))
    return model, model_at, command_at


def _newer(first: datetime | None, second: datetime | None) -> bool:
    """Whether `first` is known to be later than `second` (an unknown time never is)."""
    return first is not None and (second is None or first > second)


def current_model(session_id: str | None, transcript_path: str | None) -> str | None:
    """The model this session uses now, or None when it cannot be told."""
    state = recorded(session_id)
    reply_model, reply_at, command_at = from_transcript(transcript_path)
    state_at = _parse_time(state.get("at")) if state else None

    if reply_model and (state is None or _newer(reply_at, state_at)):
        return reply_model
    if state is None:
        return None
    if state.get("source") == SOURCE_STARTUP and _newer(command_at, state_at):
        return None
    return state["model"]
