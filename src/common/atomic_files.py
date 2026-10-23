"""
Small file helpers shared by guardrails and security scanning.

    write_atomically   replace a file in one step (temp file + rename)
    save_last_good     remember the last mapping that parsed cleanly
    load_last_good     ... and read it back when the file itself is broken

A policy file that is half-written, hand-mangled or truncated must not change
what is enforced: a reader falls back to the last good copy instead of a
built-in default. The copy is JSON (stdlib), beside the source file.

Stdlib only.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

LAST_GOOD_SUFFIX = ".last-good.json"
_LAST_GOOD_FORMAT = 1


def write_atomically(path: Path, text: str) -> None:
    """
    Write `text` to `path` so a reader sees either the old or the new file,
    never a partial one. Raises on failure; the temp file is always removed.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    try:
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def last_good_path(source: Path) -> Path:
    return source.with_name(source.name + LAST_GOOD_SUFFIX)


def save_last_good(source: Path, raw: dict) -> None:
    """
    Remember `raw` as the last mapping `source` parsed to cleanly. Written only
    when it changed, so the hot path does not rewrite it on every call. Never
    raises.
    """
    try:
        target = last_good_path(source)
        text = json.dumps({"format": _LAST_GOOD_FORMAT, "profile": raw}, sort_keys=True, default=str)
        try:
            if target.read_text(encoding="utf-8") == text:
                return
        except OSError:
            pass
        write_atomically(target, text)
    except Exception:
        pass


def load_last_good(source: Path) -> dict | None:
    """The last good mapping for `source`, or None when there is none. Never raises."""
    try:
        payload = json.loads(last_good_path(source).read_text(encoding="utf-8"))
        if payload.get("format") == _LAST_GOOD_FORMAT and isinstance(payload.get("profile"), dict):
            return payload["profile"]
    except Exception:
        pass
    return None
