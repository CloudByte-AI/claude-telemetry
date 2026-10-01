"""
macOS notifications through AppleScript's `display notification`.

- Built into macOS, nothing to install. Notices appear under Script Editor's
  name and icon (a custom icon would need a signed app bundle, which this does
  not ship); a critical one stays on screen only if the user chose the alert
  style for Script Editor.
- Text is passed as arguments to the script's `on run argv` handler, never as
  script, so quotes, backslashes or AppleScript in it are displayed, not run.
- A title starting with "-" gets a leading space: osascript reads options up to
  its first plain argument, which is always the title.
"""

from __future__ import annotations

import os
import subprocess

from src.common.notifications import Notice, one_line

# A POSIX path kept as a string, so the command line is the same whichever OS builds it.
OSASCRIPT = "/usr/bin/osascript"

# The script, one `-e` line each. Its arguments are title, subtitle, message.
SCRIPT_LINES = (
    "on run argv",
    "display notification (item 3 of argv) with title (item 1 of argv) subtitle (item 2 of argv)",
    "end run",
)

# Characters per field. macOS shortens long text itself; this bounds what travels.
TEXT_LIMIT = 200


def available() -> bool:
    return os.path.isfile(OSASCRIPT)


def show(notice: Notice) -> bool:
    """Launch osascript for `notice`. Never raises, never waits."""
    try:
        launch(argv(notice))
        return True
    except Exception as exc:
        _log().debug(f"macOS notification not shown: {exc}")
        return False


def argv(notice: Notice) -> list[str]:
    """
    The osascript command line. Pure.

    Title, subtitle and message are the notice's title, text and detail. A
    notice without a detail shows its text as the message, which AppleScript
    requires, and leaves the subtitle empty.
    """
    title = one_line(notice.title, TEXT_LIMIT)
    if title.startswith("-"):
        title = " " + title
    text = one_line(notice.text, TEXT_LIMIT)
    detail = one_line(notice.detail, TEXT_LIMIT)
    subtitle, message = (text, detail) if detail else ("", text)

    command = [str(OSASCRIPT)]
    for line in SCRIPT_LINES:
        command += ["-e", line]
    return command + [title, subtitle, message]


def launch(command: list[str]) -> None:
    """Start osascript in its own session with every handle redirected, and return."""
    subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, close_fds=True, start_new_session=True)


def _log():
    from src.common.logging import get_logger
    return get_logger(__name__)
