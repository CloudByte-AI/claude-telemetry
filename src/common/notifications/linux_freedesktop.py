"""
Linux notifications through the freedesktop.org Desktop Notifications
Specification (GNOME, KDE Plasma, Xfce, Cinnamon, MATE, dunst, mako).

Senders, tried in order: notify-send (libnotify), then gdbus calling
org.freedesktop.Notifications.Notify directly.

- Needs a D-Bus session bus. Without one (SSH, servers, containers, CI)
  available() is False and nothing is attempted.
- Critical notices never expire and close only when dismissed; warnings time out.
- The icon is a path for notify-send and a file:// URI for gdbus.
- The body is markup-escaped (`&`, `<`, `>`), since servers interpret body
  markup. The summary is plain text and is left alone.
- Text travels only as separate arguments, never through a shell or as script:
  `--` ends notify-send's options, and gdbus gets every string as a quoted
  GVariant literal, so no argument after the method name starts with "-".
"""

from __future__ import annotations

import html
import os
import shutil
import subprocess
from pathlib import Path

from src.common.notifications import APP_NAME, Notice, one_line

# Characters per field. Servers shorten long text themselves; this bounds what travels.
TEXT_LIMIT = 200

# How long a warning stays, where the server honours a timeout.
WARN_TIMEOUT_MS = 25000

SENDERS = ("notify-send", "gdbus")


def available() -> bool:
    return _sender() is not None and has_session_bus()


def show(notice: Notice) -> bool:
    """Send `notice` with the first sender found. Never raises, never waits."""
    try:
        sender = _sender()
        if sender is None or not has_session_bus():
            return False
        name, program = sender
        build = notify_send_argv if name == "notify-send" else gdbus_argv
        launch(build(notice, program))
        return True
    except Exception as exc:
        _log().debug(f"Linux notification not shown: {exc}")
        return False


def has_session_bus() -> bool:
    """A D-Bus session bus, named in the environment or at its systemd default path."""
    if os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
        return True
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    return bool(runtime) and Path(runtime, "bus").exists()


def _sender() -> tuple[str, str] | None:
    for name in SENDERS:
        program = shutil.which(name)
        if program:
            return name, program
    return None


# ── Content ───────────────────────────────────────────────────────────────────

def summary(notice: Notice) -> str:
    return one_line(notice.title, TEXT_LIMIT)


def body(notice: Notice) -> str:
    """The text, and the detail on a second line, escaped for body markup."""
    lines = [one_line(notice.text, TEXT_LIMIT)]
    if notice.detail:
        lines.append(one_line(notice.detail, TEXT_LIMIT))
    return html.escape("\n".join(lines), quote=False)


def notify_send_argv(notice: Notice, program: str = "notify-send") -> list[str]:
    command = [program, f"--app-name={APP_NAME}",
               f"--urgency={'critical' if notice.sticky else 'normal'}"]
    if not notice.sticky:
        command.append(f"--expire-time={WARN_TIMEOUT_MS}")
    icon = _icon(notice)
    if icon is not None:
        command.append(f"--icon={icon}")
    return command + ["--", summary(notice), body(notice)]


def gdbus_argv(notice: Notice, program: str = "gdbus") -> list[str]:
    """
    Notify(app_name, replaces_id, app_icon, summary, body, actions, hints,
    expire_timeout). An expire_timeout of 0 means "never expire" in the
    specification, which is what a critical notice wants.
    """
    icon = _icon(notice)
    urgency = 2 if notice.sticky else 1
    timeout = 0 if notice.sticky else WARN_TIMEOUT_MS
    return [
        program, "call", "--session",
        "--dest", "org.freedesktop.Notifications",
        "--object-path", "/org/freedesktop/Notifications",
        "--method", "org.freedesktop.Notifications.Notify",
        gvariant_string(APP_NAME),
        "0",
        gvariant_string(icon.as_uri() if icon is not None else ""),
        gvariant_string(summary(notice)),
        gvariant_string(body(notice)),
        "[]",
        f"{{'urgency': <byte {urgency}>}}",
        str(timeout),
    ]


def gvariant_string(text: str) -> str:
    """
    `text` as a GVariant text-format string literal. GLib's format takes
    `\\n` for a line break and copies any other character after a backslash
    literally, so escaping the backslash and the double quote is enough.
    """
    escaped = text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{escaped}"'


def _icon(notice: Notice) -> Path | None:
    try:
        if notice.icon is not None and Path(notice.icon).is_file():
            return Path(notice.icon).resolve()
    except Exception:
        pass
    return None


# ── Launch ────────────────────────────────────────────────────────────────────

def launch(command: list[str]) -> None:
    """Start the sender in its own session with every handle redirected, and return."""
    subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, close_fds=True, start_new_session=True)


def _log():
    from src.common.logging import get_logger
    return get_logger(__name__)
