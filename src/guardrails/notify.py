"""
Desktop notifications for guardrail decisions - what to say, and when.

Informational only: the decision has already been answered by the time a
notification is shown, and nothing here can change it.

    deny                       Blocked: <what>
    ask, prompt shown          Approval needed: <what>      (Claude Code)
    ask, no prompt possible    Ran without review: <what>   (Cursor)

followed by the rule's reason. Only critical and warn rules notify; an `info`
rule and an `allow` never do, whatever the settings say.

Settings live in ~/.cloudbyte/guardrails/guardrails_notifications.yaml:

    show: critical_and_warn     # off | critical | critical_and_warn

No file means the default, critical_and_warn. The same rule on the same call
within REPEAT_WINDOW_SECONDS is shown once.

Platform-neutral: the icon is assets/notifications/<platform>-<level>.png, so
a new client needs its two icons and nothing else. The text is not masked: it
shows the command as the agent wrote it, as the terminal does.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

import src.common.notifications as notifications
from src.common.paths import get_guardrails_dir
from src.guardrails.decision import ASK, CRITICAL, DENY, MESSAGE_PREFIX, WARN, Decision
from src.guardrails.toolcall import (
    KIND_DELETE,
    KIND_EDIT,
    KIND_MCP,
    KIND_READ,
    KIND_SEARCH,
    KIND_SHELL,
    KIND_WRITE,
    ToolCall,
    resolve_path,
)

SETTINGS_FILENAME = "guardrails_notifications.yaml"
RECENT_FILENAME = "guardrails_notifications.recent.json"
ICONS_DIR = Path(__file__).parent / "assets" / "notifications"

SHOW_OFF = "off"
SHOW_CRITICAL = "critical"
SHOW_CRITICAL_AND_WARN = "critical_and_warn"
SHOW_LEVELS: dict[str, frozenset[str]] = {
    SHOW_OFF: frozenset(),
    SHOW_CRITICAL: frozenset({CRITICAL}),
    SHOW_CRITICAL_AND_WARN: frozenset({CRITICAL, WARN}),
}
DEFAULT_SHOW = SHOW_CRITICAL_AND_WARN

REPEAT_WINDOW_SECONDS = 60
GROUP = "guardrails"

# Alert level -> (the notice's level, the word in its title). Levels missing
# here (info) never notify.
_LEVELS = {
    CRITICAL: (notifications.CRITICAL, "Critical"),
    WARN: (notifications.WARN, "Warning"),
}

_TITLE_SEPARATOR = "  \u00b7  "    # a middle dot, spaced out

_FILE_VERBS = {
    KIND_READ: "read",
    KIND_WRITE: "write",
    KIND_EDIT: "edit",
    KIND_DELETE: "delete",
    KIND_SEARCH: "search",
}


def _log():
    """The logger, imported on first use (src.common.logging is slow to import)."""
    from src.common.logging import get_logger
    return get_logger(__name__)


# ── Entry point ───────────────────────────────────────────────────────────────

def notify_decision(decision: Decision, call: ToolCall) -> bool:
    """
    Show a notification for `decision` if it earns one. True when one was
    handed to the OS. Never raises, never waits for the notification.

    Call it after the decision has been answered, from the install that
    records the decision, so one decision makes one notification.
    """
    try:
        if not wants_notification(decision) or not notifications.supported():
            return False
        if decision.alert_level not in SHOW_LEVELS[load_show_setting()]:
            return False
        key = repeat_key(decision, call)
        if shown_recently(key):
            return False
        return notifications.show(build_notice(decision, call, tag=key))
    except Exception as exc:
        _log().debug(f"guardrails notification skipped: {exc}")
        return False


def wants_notification(decision: Decision) -> bool:
    """An ask or a deny from a critical or warn rule. Pure; no settings read."""
    return decision.action in (ASK, DENY) and decision.alert_level in _LEVELS


# ── Wording ───────────────────────────────────────────────────────────────────

def build_notice(decision: Decision, call: ToolCall, tag: str | None = None) -> notifications.Notice:
    """The notification for one decision. Pure apart from checking the icon exists."""
    level, word = _LEVELS[decision.alert_level]
    icon = ICONS_DIR / f"{call.platform}-{decision.alert_level}.png"
    return notifications.Notice(
        level=level,
        title=f"{MESSAGE_PREFIX}{_TITLE_SEPARATOR}{word}",
        emphasis=MESSAGE_PREFIX,
        text=f"{headline(decision)}: {describe(call)}",
        detail=decision.reason_text,
        icon=icon if icon.is_file() else None,
        tag=tag,
        group=GROUP,
    )


def headline(decision: Decision) -> str:
    """What happened, in two or three words."""
    if decision.action == DENY:
        return "Blocked"
    if decision.prompted:
        return "Approval needed"
    return "Ran without review"


def describe(call: ToolCall) -> str:
    """What the call acts on, in the shortest form a person recognises."""
    if call.kind == KIND_SHELL and call.command:
        return call.command
    if call.kind == KIND_MCP:
        server = (call.mcp_server or "").removeprefix("mcp__").removesuffix("__")
        tool = call.mcp_tool or call.tool_name
        return f"{tool} ({server} MCP)" if server else f"{tool} (MCP)"
    if call.urls:
        return call.urls[0]
    if call.file_paths:
        verb = _FILE_VERBS.get(call.kind) or call.tool_name or "use"
        return f"{verb} {display_path(call.file_paths[0], call.cwd)}"
    query = call.raw_input.get("query") if isinstance(call.raw_input, dict) else None
    if query:
        return f"search {query}"
    return call.tool_name or "a tool call"


def display_path(path: str, cwd: str | None) -> str:
    """
    `path` relative to `cwd` when it lies inside it. A notification line holds
    one line, and the file name is the part that has to fit.
    """
    base = resolve_path(cwd, None) if cwd else None
    if base:
        base = base.rstrip("/")
        if _fold(path).startswith(_fold(base) + "/"):
            return path[len(base) + 1:]
    return path


def _fold(path: str) -> str:
    """Drive-letter paths compare case-insensitively, as Windows does; others as written."""
    return path.lower() if len(path) > 1 and path[1] == ":" else path


# ── Settings ──────────────────────────────────────────────────────────────────

def settings_path() -> Path:
    return get_guardrails_dir() / SETTINGS_FILENAME


def load_show_setting(path: Path | None = None) -> str:
    """The `show` setting. A missing, unreadable or unknown value is the default."""
    target = path or settings_path()
    if not target.exists():
        return DEFAULT_SHOW
    from src.guardrails.config import read_yaml_mapping

    raw = read_yaml_mapping(target, "guardrails notification settings")
    if raw is None:
        return DEFAULT_SHOW
    return parse_show(raw.get("show", DEFAULT_SHOW))


def parse_show(value) -> str:
    """
    A `show` value as written in YAML. YAML 1.1 reads a bare `off`, `no` or
    `false` as the boolean false, and `on`, `yes` or `true` as true, so those
    arrive as booleans rather than as the words.
    """
    if value is False:
        return SHOW_OFF
    if value is True or value is None:
        return DEFAULT_SHOW
    text = str(value).strip().lower()
    if text in SHOW_LEVELS:
        return text
    _log().warning(
        f"guardrails notifications: unknown show value {value!r} (use off, critical or "
        f"critical_and_warn), using {DEFAULT_SHOW}"
    )
    return DEFAULT_SHOW


# ── Repeats ───────────────────────────────────────────────────────────────────

def repeat_key(decision: Decision, call: ToolCall) -> str:
    """
    Same client, rule, verdict and subject -> same key. It is also the
    notification's tag, so a repeat outside the window replaces the earlier
    notification in the list instead of stacking under it.
    """
    parts = (call.platform, decision.rule_id or "", decision.action, call.subject_hash or describe(call))
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:16]


def shown_recently(key: str, now: float | None = None) -> bool:
    """
    True when `key` was shown within REPEAT_WINDOW_SECONDS. Otherwise records
    it as shown now and returns False. Never raises: when the record cannot be
    read, the notification is shown.
    """
    now = time.time() if now is None else now
    path = get_guardrails_dir() / RECENT_FILENAME
    try:
        recent = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(recent, dict):
            recent = {}
    except Exception:
        recent = {}

    def fresh(stamp) -> bool:
        return isinstance(stamp, (int, float)) and 0 <= now - stamp < REPEAT_WINDOW_SECONDS

    if fresh(recent.get(key)):
        return True
    kept = {k: v for k, v in recent.items() if fresh(v)}
    kept[key] = now
    _write_atomically(path, json.dumps(kept))
    return False


def _write_atomically(path: Path, text: str) -> None:
    """Write via a temporary file and a rename. Never raises; never leaves the temp file."""
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, path)
    except Exception:
        pass
    finally:
        try:
            temporary.unlink()
        except OSError:
            pass
