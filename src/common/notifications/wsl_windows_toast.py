"""
Notifications from WSL, shown as Windows toasts.

WSL runs Linux under Windows. Its session bus has no notification service, so
a freedesktop notification is accepted and never shown. Windows is reachable
from WSL, though: this backend builds the same toast as windows_toast and hands
it to Windows PowerShell through WSL's interop, so the notice appears on the
Windows desktop with the CloudByte-AI identity.

- The identity (HKCU AppUserModelId) is created in the same script when it is
  missing, as the Windows install does, so a WSL-only install also works.
- The icon is shown when it lives on a Windows drive (/mnt/<drive>/...); a file
  inside the Linux filesystem is left out rather than risk a broken toast.
- What PowerShell reports goes to ~/.cloudbyte/logs/notifications-wsl.log.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

from src.common.notifications import APP_NAME, Notice, running_in_wsl
from src.common.notifications import windows_toast

# Where Windows PowerShell is when WSL mounts drive C: at /mnt/c, the default.
DEFAULT_POWERSHELL = "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
ERROR_LOG = "notifications-wsl.log"

_DRIVE_PATH = re.compile(r"^/mnt/([a-zA-Z])(/.*)?$")

# Creates the AppUserModelId key with its display name when it is missing.
# Only literal text: nothing from the notice reaches these lines.
_IDENTITY_LINES = (
    f"$cbKey = 'HKCU:\\{windows_toast.REGISTRY_KEY}'",
    "if (-not (Test-Path $cbKey)) { New-Item -Path $cbKey -Force | Out-Null }",
    f"if ((Get-ItemProperty -Path $cbKey -ErrorAction SilentlyContinue).DisplayName -ne '{APP_NAME}') "
    f"{{ Set-ItemProperty -Path $cbKey -Name DisplayName -Value '{APP_NAME}' }}",
)


def available() -> bool:
    return running_in_wsl() and powershell_path() is not None


def powershell_path() -> str | None:
    """Windows PowerShell as WSL reaches it, or None when interop is off."""
    found = shutil.which("powershell.exe")
    if found:
        return found
    return DEFAULT_POWERSHELL if os.path.isfile(DEFAULT_POWERSHELL) else None


def windows_uri(path: Path | None) -> str | None:
    """A file:/// URI Windows can read for a file on a Windows drive, else None."""
    try:
        if path is None or not Path(path).is_file():
            return None
        found = _DRIVE_PATH.match(Path(path).resolve().as_posix())
        if not found:
            return None
        # Built by hand: a Linux Path does not know "C:" is a drive.
        from urllib.parse import quote

        windows = f"{found.group(1).upper()}:{found.group(2) or '/'}"
        return "file:///" + quote(windows, safe="/:")
    except Exception:
        return None


def build_script(notice: Notice) -> bytes | None:
    """The PowerShell for `notice`: the identity check, then the toast. None if it does not fit."""
    identity = ("\r\n".join(_IDENTITY_LINES) + "\r\n").encode("ascii")
    image_uri = windows_uri(notice.icon)
    for limit in windows_toast.TEXT_LIMITS:
        toast = windows_toast._script(windows_toast.toast_xml(notice, image_uri, limit), notice)
        script = identity + toast
        if len(script) <= windows_toast.PIPE_BUDGET:
            return script
    return None


def show(notice: Notice) -> bool:
    """Hand `notice` to Windows PowerShell and return without waiting. Never raises."""
    try:
        program = powershell_path()
        script = build_script(notice)
        if program is None or script is None:
            return False
        errors = _error_log()
        try:
            process = subprocess.Popen([program, *windows_toast.POWERSHELL_ARGS], stdin=subprocess.PIPE,
                                       stdout=subprocess.DEVNULL,
                                       stderr=errors if errors is not None else subprocess.DEVNULL,
                                       close_fds=True, start_new_session=True)
        finally:
            if errors is not None:
                errors.close()
        try:
            process.stdin.write(script)
        finally:
            process.stdin.close()
        return True
    except Exception as exc:
        _log().warning(f"WSL notification not shown: {type(exc).__name__}: {exc}")
        return False


def _error_log():
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
