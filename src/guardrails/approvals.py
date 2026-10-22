"""
Remembered approvals - an `ask` the user approved does not ask again, for the
same action, for a few minutes.

Without this every matching call asks, however often the user has just said
yes: a page of web fetches or a build that clears `dist/` meant the same prompt
again and again, which trains people to approve without reading. With it, an
approval opens a WINDOW (the policy's `ask_memory.minutes`) in which the same
action passes without a prompt. The policy turns it on (`ask_memory.mode`);
absent, nothing is remembered and every ask prompts, as before.

What "the same action" is, per mode:

    same_target   the same rule AND the same target: path, host, MCP tool, or
                  the command itself. Approving `rm -rf build` does not cover
                  `rm -rf src`.
    any_target    the same rule, whatever its target.

and always: the same session, the same agent (a subagent keeps its own
approvals), the same policy (its fingerprint and scope - a changed policy is
asked again), so an approval never travels further than where it was given.

The window is FIXED: it ends `minutes` after the approval, however often the
action runs in it, and a repeat never extends it. A rejected ask opens nothing.

How an approval is known. Claude Code reports no "the user said yes", so:

    PreToolUse asks      -> record_pending(): a note, keyed by the call's
                            tool_use_id, of the key that ask would remember
    the tool then runs   -> PostToolUse -> grant_from_pending(): the note
                            becomes a window. A rejected call never runs, so
                            its note is never turned into a window.

Only Claude Code: Cursor shows no prompt for an ask (Decision.prompted), so
there is no approval to remember.

Storage, under the guardrails directory (which selfguard already protects from
the agent - it may not grant itself an approval):

    approvals/pending/<h(tool_use_id)>.json
    approvals/sessions/<h(session)>/<h(agent)>/<h(key)>.json

One file per key, written atomically, so parallel hooks need no lock. Only
hashes and the rule id are stored: a key may contain a command, and commands
can carry secrets. Every function here is non-fatal: on any failure the call
is asked, as if nothing were remembered.

Stdlib only, plus src.common.paths: the PostToolUse hook imports this module
on every tool call, so it must stay cheap to import.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from src.common.lexical_paths import WINDOWS_DRIVE

# ── Modes and limits ─────────────────────────────────────────────────────────

MODE_OFF = "off"
MODE_SAME_TARGET = "same_target"
MODE_ANY_TARGET = "any_target"
MODES = (MODE_OFF, MODE_SAME_TARGET, MODE_ANY_TARGET)

DEFAULT_MINUTES = 15
MAX_MINUTES = 60                     # a window longer than this is never honoured

# A note older than this is not an approval: the prompt it was written for has
# long been answered or abandoned.
PENDING_MAX_AGE_SECS = 24 * 3600
# Clock moved back: a window that starts this far in the future is not trusted.
CLOCK_SKEW_SECS = 120
# Anything older than this is swept (sweep()).
SWEEP_AGE_SECS = 24 * 3600

_DIRNAME = "approvals"
_PENDING = "pending"
_SESSIONS = "sessions"
_MAIN_AGENT = "main"
_SUFFIX = ".json"


def approvals_dir() -> Path:
    from src.common.paths import get_guardrails_dir
    return get_guardrails_dir() / _DIRNAME


def _h(text: str, length: int = 40) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:length]


def _session_dir(session_id: str, agent_id: str | None) -> Path:
    agent = f"agent-{_h(agent_id, 24)}" if agent_id else _MAIN_AGENT
    return approvals_dir() / _SESSIONS / _h(session_id, 32) / agent


def _pending_path(tool_use_id: str) -> Path:
    return approvals_dir() / _PENDING / f"{_h(tool_use_id)}{_SUFFIX}"


def _write_json(path: Path, data: dict) -> None:
    """Atomic within one directory: a reader sees the old file or the new one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(json.dumps(data), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            try:
                temporary.unlink()
            except OSError:
                pass


def _read_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


# ── What "the same action" is ────────────────────────────────────────────────

_WHITESPACE = re.compile(r"\s+")


def normalise_target(value: str | None) -> str:
    """
    One spelling per target, so `C:\\repo\\build\\` and `c:/repo/build` are the
    same path and `https://Docs.Example.com/a` and `docs.example.com` the same
    host. Never raises; '' for nothing.
    """
    if not value:
        return ""
    text = str(value).strip()
    if "://" in text:
        try:
            host = urlsplit(text).hostname
            if host:
                return host.lower()
        except ValueError:
            pass
    looks_like_path = ("/" in text or "\\" in text) and not _WHITESPACE.search(text)
    if looks_like_path:
        text = text.replace("\\", "/")
        while len(text) > 1 and text.endswith("/") and not text.endswith(":/"):
            text = text[:-1]
        # A drive-letter path is a Windows path, where case does not matter;
        # decided by the path itself, never by the host (guardrails stay
        # platform-neutral - test_contracts.py).
        return text.casefold() if WINDOWS_DRIVE.match(text) else text
    if "." in text and " " not in text and "/" not in text:
        return text.lower()          # a bare host name
    return _WHITESPACE.sub(" ", text)


@dataclass(frozen=True)
class Memory:
    """
    Remembered approvals for one call under one policy: what to look up, and
    what an ask under that policy would remember if approved.
    """

    mode: str
    minutes: int
    session_id: str
    agent_id: str | None
    # Identifies the policy: its fingerprint, scope and workspace folder.
    policy: str

    def key(self, rule_id: str, target: str | None) -> str:
        """The key one rule match is remembered under (a hash; never the raw target)."""
        parts = [self.policy, rule_id]
        if self.mode == MODE_SAME_TARGET:
            parts.append(normalise_target(target))
        return _h("\x1f".join(parts), 64)

    def is_remembered(self, key: str, now: float | None = None) -> bool:
        """
        True while an approval window for `key` is open. False on any doubt.

        A window found expired (or unreadable) is deleted on the spot, so a long
        session does not keep its old windows until it ends.
        """
        try:
            path = _session_dir(self.session_id, self.agent_id) / f"{key[:40]}{_SUFFIX}"
            data = _read_json(path)
            current = time.time() if now is None else now
            if _window_open(data, key, current):
                return True
            if path.exists() and _is_dead(data, current):
                _remove_if_unchanged(path, data)
            return False
        except Exception:
            return False


def _window_open(data: dict | None, key: str, now: float) -> bool:
    if not data or data.get("key") != key:
        return False
    try:
        approved = float(data["approved_at"])
        expires = float(data["expires_at"])
    except (KeyError, TypeError, ValueError):
        return False
    if expires - approved > MAX_MINUTES * 60 or expires <= approved:
        return False                 # not a window this module writes
    if approved > now + CLOCK_SKEW_SECS:
        return False                 # starts in the future: the clock moved back
    return now < expires


def _is_dead(data: dict | None, now: float) -> bool:
    """
    A window that can never be honoured again: unreadable, or ended. One that
    starts in the future (the clock moved back) is kept - it may become valid.
    """
    if not data:
        return True
    try:
        return now >= float(data["expires_at"])
    except (KeyError, TypeError, ValueError):
        return True


def _remove_if_unchanged(path: Path, seen: dict | None) -> None:
    """
    Delete a dead window unless it was replaced since it was read: another
    hook may have just opened a fresh window under the same name. Losing that
    race costs at most one extra prompt, never a silent pass. Never raises.
    """
    try:
        if _read_json(path) == seen:
            path.unlink()
    except OSError:
        pass


def memory_for(call, profile, policy_scope: str, workspace_root: str | None) -> Memory | None:
    """
    The Memory for this call under this policy, or None when nothing may be
    remembered: the policy leaves it off, the platform cannot show an ask and
    report its approval (ToolCall.remembers_approvals - Cursor cannot), or the
    call carries no session to scope it to.
    """
    mode = getattr(profile, "ask_memory_mode", None)
    if mode not in (MODE_SAME_TARGET, MODE_ANY_TARGET):
        return None
    if not getattr(call, "remembers_approvals", False) or not getattr(call, "session_id", None):
        return None
    minutes = int(getattr(profile, "ask_memory_minutes", DEFAULT_MINUTES) or DEFAULT_MINUTES)
    policy = "\x1f".join((profile.fingerprint or "", policy_scope or "", workspace_root or ""))
    return Memory(mode=mode, minutes=max(1, min(minutes, MAX_MINUTES)),
                  session_id=str(call.session_id), agent_id=getattr(call, "agent_id", None) or None,
                  policy=policy)


# ── Approval: PreToolUse note -> PostToolUse window ──────────────────────────

def record_pending(decision, call) -> bool:
    """
    Note what an ask would remember if the user approves it. Called for every
    ask the user is shown; never for a remembered pass or a deny. Never raises.
    """
    try:
        if (decision.action != "ask" or getattr(decision, "remembered", False)
                or not getattr(decision, "memory_key", None) or not call.tool_use_id
                or not call.session_id):
            return False
        keys = [k for k in (getattr(decision, "memory_keys", ()) or (decision.memory_key,)) if k]
        _write_json(_pending_path(str(call.tool_use_id)), {
            "keys": keys,
            "session": _h(str(call.session_id), 32),
            "agent": call.agent_id or None,
            "minutes": int(decision.memory_minutes or DEFAULT_MINUTES),
            "rule_id": decision.rule_id,
            "created_at": time.time(),
        })
        return True
    except Exception:
        return False


def grant_from_pending(tool_use_id: str | None, session_id: str | None,
                       now: float | None = None) -> bool:
    """
    The tool ran, so its ask was approved: open the window. Called by the
    PostToolUse hook for every tool call; the usual answer is "no note", one
    failed file lookup. Never raises.
    """
    if not tool_use_id or not session_id:
        return False
    try:
        pending = _pending_path(str(tool_use_id))
        if not pending.exists():
            return False
        taken = pending.with_name(f"{pending.name}.{os.getpid()}.taken")
        try:
            os.replace(pending, taken)       # exactly one hook consumes a note
        except OSError:
            return False
        try:
            note = _read_json(taken)
        finally:
            try:
                taken.unlink()
            except OSError:
                pass
        current = time.time() if now is None else now
        if not note or note.get("session") != _h(str(session_id), 32):
            return False
        created = float(note.get("created_at", 0))
        if current - created > PENDING_MAX_AGE_SECS or created > current + CLOCK_SKEW_SECS:
            return False
        keys = [str(k) for k in (note.get("keys") or []) if k]
        minutes = max(1, min(int(note.get("minutes") or DEFAULT_MINUTES), MAX_MINUTES))
        if not keys:
            return False
        folder = _session_dir(str(session_id), note.get("agent"))
        for key in keys:
            _write_json(folder / f"{key[:40]}{_SUFFIX}", {
                "key": key,
                "rule_id": note.get("rule_id"),
                "approved_at": current,
                "expires_at": current + minutes * 60,
            })
        return True
    except Exception:
        return False


# ── Housekeeping ─────────────────────────────────────────────────────────────

def forget_session(session_id: str | None) -> None:
    """Drop every window of a session (SessionEnd). Never raises."""
    if not session_id:
        return
    try:
        shutil.rmtree(approvals_dir() / _SESSIONS / _h(str(session_id), 32), ignore_errors=True)
    except Exception:
        pass


def sweep(now: float | None = None, max_age: float = SWEEP_AGE_SECS) -> int:
    """
    Remove notes and session folders untouched for `max_age`: a session that
    never ended cleanly, a note for an ask that was rejected. Never raises.
    """
    current = time.time() if now is None else now
    removed = 0
    try:
        base = approvals_dir()
        for note in (base / _PENDING).glob(f"*{_SUFFIX}*"):
            try:
                if current - note.stat().st_mtime > max_age:
                    note.unlink()
                    removed += 1
            except OSError:
                pass
        sessions = base / _SESSIONS
        if sessions.is_dir():
            for folder in sessions.iterdir():
                try:
                    newest = max((p.stat().st_mtime for p in folder.rglob("*")), default=folder.stat().st_mtime)
                    if current - newest > max_age:
                        shutil.rmtree(folder, ignore_errors=True)
                        removed += 1
                except OSError:
                    pass
    except Exception:
        pass
    return removed
