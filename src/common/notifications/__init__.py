"""
Desktop notifications - one small API, one backend per operating system.

    notice = Notice(level=CRITICAL, title="...", text="...", detail="...")
    show(notice)        # True once handed to the OS; never raises

    win32    windows_toast.py       Windows toast notifications
    darwin   macos_osascript.py     AppleScript's `display notification`
    linux    linux_freedesktop.py   the freedesktop.org notification spec
                                    (notify-send, or gdbus when it is missing)

`supported()` is False on an OS with no backend, or on Linux with no desktop
bus; `show()` then returns False and does nothing, so callers need no OS check.
A new OS is one module defining `available()` and `show(notice)`, plus one line
in _BACKENDS.

Fire-and-forget: a backend hands the notice to a separate process and returns
without waiting, so this is safe to call from a hook. Nothing here raises.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

CRITICAL = "critical"
WARN = "warn"
LEVELS = (CRITICAL, WARN)

# The sender name, where the OS lets an app choose one (Windows, Linux).
APP_NAME = "CloudByte-AI"

_BACKENDS = {
    "win32": "src.common.notifications.windows_toast",
    "darwin": "src.common.notifications.macos_osascript",
    "linux": "src.common.notifications.linux_freedesktop",
}

_REPLACEMENT = chr(0xFFFD)


@dataclass(frozen=True)
class Notice:
    """One notification, in OS-neutral terms."""

    level: str                      # CRITICAL or WARN
    title: str                      # first line
    text: str                       # second line
    detail: str = ""                # third line, smaller
    icon: Path | None = None        # image beside the text, where the OS shows one
    # The leading words of `title` to make heavier on Windows, through bold().
    # macOS and Linux already set the title in bold.
    emphasis: str = ""
    # A later notice with the same tag replaces this one in the notification
    # list instead of stacking under it, where the OS supports that.
    tag: str | None = None
    group: str | None = None

    @property
    def sticky(self) -> bool:
        """Critical notices stay on screen until dismissed; warnings time out."""
        return self.level == CRITICAL


def supported() -> bool:
    """Whether this machine can show a notification: a backend, and what it needs."""
    backend = _backend()
    if backend is None:
        return False
    try:
        return bool(backend.available())
    except Exception:
        return False


def show(notice: Notice) -> bool:
    """
    Hand `notice` to the OS. True when that worked, False otherwise - including
    on an OS with no backend. Never raises, never waits for the notice to appear.
    """
    backend = _backend()
    if backend is None:
        return False
    try:
        return bool(backend.show(notice))
    except Exception as exc:
        _log().debug(f"notification not shown: {exc}")
        return False


def one_line(value: str | None, limit: int | None = None) -> str:
    """
    `value` as one line of display text, cut to `limit` characters with "...".

    Line breaks (including Unicode ones) and tabs become one space; spacing
    inside a line is kept. Other control characters and lone surrogates become
    U+FFFD, since some notification systems reject the whole notice over one.
    """
    parts = str(value or "").replace("\t", " ").splitlines()
    text = " ".join(part.strip() for part in parts if part.strip())
    text = "".join(_REPLACEMENT if _unprintable(ch) else ch for ch in text)
    if limit is not None and len(text) > limit:
        text = text[: max(limit - 3, 1)].rstrip() + "..."
    return text


def bold(text: str) -> str:
    """
    `text` with A-Z and a-z replaced by Unicode "Mathematical Sans-Serif Bold"
    letters; everything else unchanged.

    Windows notification text has no bold markup; these letters render heavier
    at the same size. Screen readers may spell them out letter by letter, so
    use this for a few words only, never for a message.
    """
    out = []
    for ch in text:
        if "A" <= ch <= "Z":
            out.append(chr(0x1D5D4 + ord(ch) - ord("A")))
        elif "a" <= ch <= "z":
            out.append(chr(0x1D5EE + ord(ch) - ord("a")))
        else:
            out.append(ch)
    return "".join(out)


def _unprintable(ch: str) -> bool:
    code = ord(ch)
    return code < 0x20 or 0x7F <= code < 0xA0 or 0xD800 <= code <= 0xDFFF


def _backend():
    """This OS's backend module, or None. Never raises."""
    module_path = _BACKENDS.get(sys.platform)
    if module_path is None:
        return None
    try:
        import importlib
        return importlib.import_module(module_path)
    except Exception as exc:
        _log().debug(f"notification backend {module_path} unavailable: {exc}")
        return None


def _log():
    """The logger, imported on first use: src.common.logging is slow to import."""
    from src.common.logging import get_logger
    return get_logger(__name__)
