"""
macOS notices through AppleScript, run by the built-in osascript.

Two styles (Notice.mac_style):

- dialog (the default): `display dialog` with the notice's icon. A script may
  show a dialog without any permission, so it appears on every Mac as soon as
  the plugin is installed. It comes to the front and closes itself after
  DIALOG_SECONDS, or earlier when the user clicks OK.
- notification: `display notification`. It appears under Script Editor's name
  and icon (a custom icon would need a signed app bundle, which this does not
  ship), and macOS shows it only once the user has allowed Script Editor to
  notify; until then it is dropped without an error.

- The icon: the notice's PNG, converted once to .icns with the built-in sips
  and kept in ~/.cloudbyte/assets. Without it the dialog shows the caution icon.
- Text is passed as arguments to the script's `on run argv` handler, never as
  script, so quotes, backslashes or AppleScript in it are displayed, not run.
- A title starting with "-" gets a leading space: osascript reads options up to
  its first plain argument, which is always the title.
- What osascript reports goes to ~/.cloudbyte/logs/notifications-macos.log, so
  a notice that did not appear can be explained.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from src.common.notifications import MAC_NOTIFICATION, Notice, one_line

# POSIX paths kept as strings, so the command line is the same whichever OS builds it.
OSASCRIPT = "/usr/bin/osascript"
SIPS = "/usr/bin/sips"

# The notification script, one `-e` line each. Its arguments are title, subtitle, message.
SCRIPT_LINES = (
    "on run argv",
    "display notification (item 3 of argv) with title (item 1 of argv) subtitle (item 2 of argv)",
    "end run",
)

# The dialog script. Its arguments are title, text, icon file (or ""), seconds
# before it closes itself. An icon that cannot be read falls back to caution.
DIALOG_LINES = (
    "on run argv",
    "set theTitle to item 1 of argv",
    "set theText to item 2 of argv",
    "set theWait to (item 4 of argv) as integer",
    "try",
    "set theIcon to (POSIX file (item 3 of argv)) as alias",
    'display dialog theText with title theTitle buttons {"OK"} default button "OK" '
    "with icon theIcon giving up after theWait",
    "on error number errorNumber",
    "if errorNumber is not -128 then "
    'display dialog theText with title theTitle buttons {"OK"} default button "OK" '
    "with icon caution giving up after theWait",
    "end try",
    "end run",
)

# Seconds a dialog stays before it closes itself, for an ask and a block alike.
DIALOG_SECONDS = 20

# Characters per field. macOS shortens long text itself; this bounds what travels.
TEXT_LIMIT = 200

ERROR_LOG = "notifications-macos.log"


def available() -> bool:
    return os.path.isfile(OSASCRIPT)


def show(notice: Notice) -> bool:
    """Launch osascript for `notice`. Never raises, never waits."""
    try:
        if notice.mac_style == MAC_NOTIFICATION:
            launch(argv(notice))
        else:
            launch(dialog_argv(notice, icns_for(notice.icon)))
        return True
    except Exception as exc:
        _log().warning(f"macOS notification not shown: osascript could not be started: {type(exc).__name__}: {exc}")
        return False


def argv(notice: Notice) -> list[str]:
    """
    The notification command line. Pure.

    Title, subtitle and message are the notice's title, text and detail. A
    notice without a detail shows its text as the message, which AppleScript
    requires, and leaves the subtitle empty.
    """
    title = _title(notice)
    text = one_line(notice.text, TEXT_LIMIT)
    detail = one_line(notice.detail, TEXT_LIMIT)
    subtitle, message = (text, detail) if detail else ("", text)

    command = [str(OSASCRIPT)]
    for line in SCRIPT_LINES:
        command += ["-e", line]
    return command + [title, subtitle, message]


def dialog_argv(notice: Notice, icon: Path | None) -> list[str]:
    """
    The dialog command line. Pure.

    The window title is the notice's title; the body is its text, then its
    detail after a blank line.
    """
    body = one_line(notice.text, TEXT_LIMIT)
    detail = one_line(notice.detail, TEXT_LIMIT)
    if detail:
        body = f"{body}\n\n{detail}"
    seconds = DIALOG_SECONDS

    command = [str(OSASCRIPT)]
    for line in DIALOG_LINES:
        command += ["-e", line]
    return command + [_title(notice), body, str(icon) if icon else "", str(seconds)]


def icns_for(png: Path | None) -> Path | None:
    """
    `png` as an .icns icon in ~/.cloudbyte/assets, made once with sips and
    remade when the PNG changes. None when there is no PNG or sips cannot
    convert it. Never raises.
    """
    try:
        if png is None or not Path(png).is_file():
            return None
        from src.common.paths import get_assets_dir

        source = Path(png)
        target = get_assets_dir() / f"{source.stem}.icns"
        if target.is_file() and target.stat().st_mtime >= source.stat().st_mtime:
            return target
        if not os.path.isfile(SIPS):
            return None
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f"{target.stem}.{os.getpid()}.icns")
        result = subprocess.run([SIPS, "-s", "format", "icns", str(source), "--out", str(temporary)],
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, timeout=10)
        if result.returncode != 0 or not temporary.is_file():
            return None
        os.replace(temporary, target)
        return target
    except Exception:
        return None


def _title(notice: Notice) -> str:
    title = one_line(notice.title, TEXT_LIMIT)
    return " " + title if title.startswith("-") else title


def launch(command: list[str]) -> None:
    """
    Start osascript in its own session and return. Its output goes to the
    error log, so a notice macOS refused can be explained afterwards.
    """
    errors = _error_log()
    try:
        subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=errors if errors is not None else subprocess.DEVNULL,
                         close_fds=True, start_new_session=True)
    finally:
        if errors is not None:
            errors.close()


def _error_log():
    """The error log, opened for appending, or None. Never raises."""
    try:
        from src.common.paths import get_logs_dir

        directory = get_logs_dir()
        directory.mkdir(parents=True, exist_ok=True)
        return open(directory / ERROR_LOG, "ab")
    except Exception:
        return None


def _log():
    from src.common.logging import get_logger
    return get_logger(__name__)
