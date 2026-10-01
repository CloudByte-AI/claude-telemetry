"""
Windows toast notifications, sent by an app without a package.

- Own identity: ensure_identity() writes a per-user AppUserModelId registry
  key, with no admin rights or installer step; scripts/uninstall.ps1 removes
  it. Borrowing PowerShell's identity would let its notification setting
  silence ours.
- Windows PowerShell 5.1 by absolute path: CreateProcess searches the current
  directory first, and a hook's current directory is the user's project.
- The script goes on stdin with `-Command -`, never as a .ps1 file: the default
  Restricted execution policy refuses script files but not commands. No
  `-ExecutionPolicy Bypass` or `-EncodedCommand`, which endpoint protection
  looks for.
- All three standard handles redirected: a child that inherited a Cursor
  hook's stdout would hold Cursor's pipe open until PowerShell exited.
- An ASCII script within the pipe buffer: stdin is decoded with the console
  code page, so other characters travel as XML character references, and long
  lines are shortened until the script fits, so the caller never waits.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from src.common.notifications import APP_NAME, Notice, bold, one_line
from src.common.paths import get_assets_dir

APP_ID = "CloudByte.AI"
REGISTRY_KEY = "Software\\Classes\\AppUserModelId\\" + APP_ID

APP_ICON = Path(__file__).parent / "assets" / "cloudbyte-ai.png"

POWERSHELL_ARGS = ("-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command", "-")

# Bytes. Below the 4096-byte pipe buffer, with room to spare.
PIPE_BUDGET = 4000

# Characters per line, tried in order until the script fits PIPE_BUDGET.
# Windows cuts every line at the notification's width anyway.
TEXT_LIMITS = (160, 100, 60, 30)

# Windows before 10 1703 rejects a tag or group longer than 16 characters.
_ID_LENGTH = 16
_UNSAFE_ID_CHARS = re.compile(r"[^A-Za-z0-9._-]")

_CREATE_NO_WINDOW = 0x08000000
_CREATE_BREAKAWAY_FROM_JOB = 0x01000000

_ENTITIES = {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}


def available() -> bool:
    """Windows PowerShell 5.1 ships with every Windows 10 and 11."""
    return powershell_path().is_file()


def show(notice: Notice) -> bool:
    """Build the toast, make sure the app identity exists, and launch it. Never raises."""
    try:
        script = build_script(notice)
        if script is None or not ensure_identity():
            return False
        return launch(script)
    except Exception as exc:
        _log().debug(f"windows notification not shown: {exc}")
        return False


# ── Identity ──────────────────────────────────────────────────────────────────

def ensure_identity(registry=None) -> bool:
    """
    Create or repair the AppUserModelId key, writing only values that differ.
    `registry` is winreg; tests pass a stand-in. Never raises.
    """
    try:
        if registry is None:
            import winreg as registry

        values = {"DisplayName": APP_NAME}
        icon = installed_app_icon()
        if icon is not None:
            values["IconUri"] = str(icon)

        access = registry.KEY_READ | registry.KEY_SET_VALUE
        with registry.CreateKeyEx(registry.HKEY_CURRENT_USER, REGISTRY_KEY, 0, access) as key:
            for name, value in values.items():
                try:
                    current = registry.QueryValueEx(key, name)[0]
                except OSError:
                    current = None
                if current != value:
                    registry.SetValueEx(key, name, 0, registry.REG_SZ, value)
        return True
    except Exception as exc:
        _log().debug(f"notification identity not registered: {exc}")
        return False


def installed_app_icon() -> Path | None:
    """
    The app icon, copied to ~/.cloudbyte/assets and refreshed when the shipped
    one changes. Windows keeps the IconUri path, so it must outlive the plugin
    version that set it (see get_assets_dir). None when there is no usable copy.
    """
    target = get_assets_dir() / APP_ICON.name
    try:
        shipped = APP_ICON.read_bytes()
        try:
            if target.read_bytes() == shipped:
                return target
        except OSError:
            pass
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f"{target.name}.{os.getpid()}.tmp")
        try:
            temporary.write_bytes(shipped)
            os.replace(temporary, target)
        finally:
            try:
                temporary.unlink()
            except OSError:
                pass
        return target
    except Exception:
        # Another process holding the old copy open is fine: it is still an icon.
        return target if target.exists() else None


# ── Content ───────────────────────────────────────────────────────────────────

def toast_xml(notice: Notice, image_uri: str | None = None, limit: int = TEXT_LIMITS[0]) -> str:
    """
    The toast payload. Pure, and ASCII by construction.

    Each text is held to one line (`hint-maxLines="1"`) because Windows pins
    the image to the top of the text. A sticky notice uses the reminder
    scenario, which stays until dismissed and needs a button, hence Dismiss.
    """
    sticky = notice.sticky
    parts = ['<toast duration="long" scenario="reminder">' if sticky else '<toast duration="long">',
             '<visual><binding template="ToastGeneric">',
             f'<text hint-maxLines="1">{_text(title(notice), limit)}</text>']
    if image_uri:
        parts.append(f'<image placement="appLogoOverride" src="{_escape(image_uri)}"/>')
    parts.append(f'<text hint-maxLines="1">{_text(notice.text, limit)}</text>')
    if notice.detail:
        parts.append('<group><subgroup><text hint-style="captionSubtle" hint-maxLines="1">'
                     f'{_text(notice.detail, limit)}</text></subgroup></group>')
    parts.append('</binding></visual>')
    if sticky:
        parts.append('<actions><action content="Dismiss" arguments="dismiss" '
                     'activationType="system"/></actions>')
    parts.append('<audio silent="true"/></toast>')
    return "".join(parts)


def build_script(notice: Notice) -> bytes | None:
    """
    The PowerShell that shows `notice`, as ASCII bytes within PIPE_BUDGET.
    Lines are shortened step by step until it fits; None if it never does.
    """
    image_uri = _file_uri(notice.icon)
    for limit in TEXT_LIMITS:
        script = _script(toast_xml(notice, image_uri, limit), notice)
        if len(script) <= PIPE_BUDGET:
            return script
    return None


def _script(xml: str, notice: Notice) -> bytes:
    # The XML sits in a single-quoted PowerShell string. It cannot contain a
    # quote: _escape turns every `'` into a character reference.
    lines = [
        "$ErrorActionPreference = 'Stop'",
        "[void][Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime]",
        "[void][Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime]",
        "$xml = New-Object Windows.Data.Xml.Dom.XmlDocument",
        f"$xml.LoadXml('{xml}')",
        "$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)",
    ]
    tag, group = _safe_id(notice.tag), _safe_id(notice.group)
    if tag:
        lines.append(f"$toast.Tag = '{tag}'")
    if group:
        lines.append(f"$toast.Group = '{group}'")
    lines.append(f"[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{APP_ID}').Show($toast)")
    return ("\r\n".join(lines) + "\r\n").encode("ascii")


def title(notice: Notice) -> str:
    """The title, with its emphasis in bold letters (see Notice.emphasis)."""
    emphasis = notice.emphasis
    if emphasis and notice.title.startswith(emphasis):
        return bold(emphasis) + notice.title[len(emphasis):]
    return notice.title


def _text(value: str | None, limit: int) -> str:
    """One line of display text, cut to `limit`, escaped."""
    return _escape(one_line(value, limit))


def _escape(text: str) -> str:
    """
    XML-escape `text` into pure ASCII. Characters XML 1.0 forbids (most
    control characters, lone surrogates) become U+FFFD, since one of them
    would make LoadXml reject the whole notification.
    """
    out = []
    for ch in text:
        code = ord(ch)
        if ch in _ENTITIES:
            out.append(_ENTITIES[ch])
        elif 0x20 <= code <= 0x7E:
            out.append(ch)
        elif _xml_allows(code):
            out.append(f"&#x{code:X};")
        else:
            out.append("&#xFFFD;")
    return "".join(out)


def _xml_allows(code: int) -> bool:
    return (code in (0x9, 0xA, 0xD) or 0x20 <= code <= 0xD7FF
            or 0xE000 <= code <= 0xFFFD or 0x10000 <= code <= 0x10FFFF)


def _file_uri(path: Path | None) -> str | None:
    """A file:/// URI (percent-encoded, so ASCII) for an image that exists, else None."""
    try:
        if path is not None and Path(path).is_file():
            return Path(path).resolve().as_uri()
    except Exception:
        pass
    return None


def _safe_id(value: str | None) -> str | None:
    if not value:
        return None
    return _UNSAFE_ID_CHARS.sub("", str(value))[:_ID_LENGTH] or None


# ── Launch ────────────────────────────────────────────────────────────────────

def powershell_path() -> Path:
    root = os.environ.get("SystemRoot") or os.environ.get("windir") or r"C:\Windows"
    return Path(root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"


def powershell_argv() -> list[str]:
    return [str(powershell_path()), *POWERSHELL_ARGS]


def launch(script: bytes) -> bool:
    """
    Start PowerShell hidden, hand it `script` on stdin, and return without
    waiting. The script fits the pipe buffer (build_script), so the write
    completes before PowerShell has even started.
    """
    argv = powershell_argv()
    options = dict(stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL, close_fds=True)
    # CREATE_NO_WINDOW, not DETACHED_PROCESS: powershell.exe is a console
    # program and exits at once without a console. Breaking away from the
    # caller's job keeps the toast alive after a short-lived hook process ends.
    try:
        process = subprocess.Popen(argv, creationflags=_CREATE_NO_WINDOW | _CREATE_BREAKAWAY_FROM_JOB,
                                   **options)
    except OSError:
        # A job that forbids breakaway makes CreateProcess fail outright; run
        # inside it rather than not at all.
        process = subprocess.Popen(argv, creationflags=_CREATE_NO_WINDOW, **options)
    try:
        process.stdin.write(script)
    finally:
        process.stdin.close()
    return True


def _log():
    from src.common.logging import get_logger
    return get_logger(__name__)
