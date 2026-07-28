"""
Blocked-prompt desktop notification.

The CLI renders a hook's `reason` / `systemMessage` as a yellow warning, but
that's easy to miss when several sessions/terminals are open at once. This
fires a native OS popup - branded to match the CloudByte dashboard - as a
supplementary heads-up for every client (CLI and VS Code alike), so whichever
session got blocked is obvious regardless of which terminal window has focus.

The popup auto-closes after AUTO_CLOSE_SECONDS if left unattended, and can
always be dismissed early via its OK button / window close.

The popup is spawned fully detached in its own process, so the hook process
that calls this returns immediately regardless of whether/when the popup is
dismissed - it never adds to the hook's own timeout budget. Any failure here
(no GUI session, missing binary, unsupported OS, etc.) is swallowed - it must
never affect the block decision itself.
"""

import re
import subprocess
import sys
import tempfile
from pathlib import Path

from src.common.logging import get_logger

logger = get_logger(__name__)

_ASSETS_DIR = Path(__file__).parent / "assets"
_WINDOWS_XAML_PATH = _ASSETS_DIR / "blocked_prompt_dialog.xaml"

AUTO_CLOSE_SECONDS = 60

# WPF's TextBlock renders with plain "Segoe UI", which has no color-emoji
# glyphs - emoji show up as tofu boxes instead of falling back gracefully.
# Stripped only from what's displayed; the clipboard copy keeps the original
# text untouched so resubmitting still gives back the exact sanitized prompt.
_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001FAFF"  # symbols & pictographs, emoticons, transport, supplemental
    "\U00002600-\U000027BF"  # misc symbols, dingbats
    "\U0001F1E6-\U0001F1FF"  # regional indicators (flags)
    "\U00002190-\U000021FF"  # arrows (variation-selected ones render as tofu too)
    "\U0000FE0F"             # variation selector-16 (emoji presentation)
    "\U0000200D"             # zero-width joiner
    "]+"
)


def _strip_emoji(text: str) -> str:
    return _EMOJI_RE.sub("", text)


def notify_blocked_prompt(message: str, masked_prompt: str | None = None) -> None:
    """
    Best-effort, fire-and-forget desktop popup for a blocked prompt.

    masked_prompt: the sanitized prompt text to put on the clipboard when the
    user clicks the copy icon (Windows only - the WPF dialog is the only popup
    with room for a second control). Falls back to nothing copyable if omitted.
    """
    try:
        if sys.platform == "win32":
            _notify_windows(message, masked_prompt or "")
        elif sys.platform == "darwin":
            _notify_macos(message)
        else:
            _notify_linux(message)
    except Exception as e:
        logger.warning(f"Blocked-prompt popup skipped (non-fatal): {e}", exc_info=True)


def _xml_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _notify_windows(message: str, masked_prompt: str) -> None:
    CREATE_NO_WINDOW = 0x08000000
    CREATE_BREAKAWAY_FROM_JOB = 0x01000000

    display_message = _strip_emoji(message)
    body_lines = "<LineBreak/>".join(
        _xml_escape(line) for line in display_message.replace("\r\n", "\n").split("\n")
    )

    xaml = _WINDOWS_XAML_PATH.read_text(encoding="utf-8").replace(
        "__MESSAGE_BODY__", body_lines
    )

    ps_script = f"""Add-Type -AssemblyName PresentationFramework
Add-Type -AssemblyName PresentationCore
Add-Type -AssemblyName WindowsBase

$xaml = @'
{xaml}
'@

$maskedPrompt = @'
{masked_prompt}
'@

$window = [Windows.Markup.XamlReader]::Parse($xaml)
$button = $window.FindName("OkButton")
$button.Add_Click({{ $window.Close() }})

$copyButton = $window.FindName("CopyButton")
$copyIdleBrush = [System.Windows.Media.SolidColorBrush]([System.Windows.Media.ColorConverter]::ConvertFromString("#242424"))
$copyActiveBrush = [System.Windows.Media.SolidColorBrush]([System.Windows.Media.ColorConverter]::ConvertFromString("#22c55e"))
$copyButton.Add_Click({{
    [System.Windows.Clipboard]::SetText($maskedPrompt)
    $copyButton.Background = $copyActiveBrush
    $flashTimer = New-Object System.Windows.Threading.DispatcherTimer
    $flashTimer.Interval = [TimeSpan]::FromMilliseconds(500)
    $flashTimer.Add_Tick({{
        $copyButton.Background = $copyIdleBrush
        $flashTimer.Stop()
    }})
    $flashTimer.Start()
}})

$timer = New-Object System.Windows.Threading.DispatcherTimer
$timer.Interval = [TimeSpan]::FromSeconds({AUTO_CLOSE_SECONDS})
$timer.Add_Tick({{ $timer.Stop(); $window.Close() }})
$timer.Start()

[void]$window.ShowDialog()
Remove-Item -LiteralPath $MyInvocation.MyCommand.Path -Force -ErrorAction SilentlyContinue
"""

    # `-Command`/`-EncodedCommand` were tried first (to sidestep quoting for
    # arbitrary finding text) but confirmed by direct testing to make
    # ShowDialog() return instantly without ever actually blocking/displaying -
    # PowerShell doesn't drive a proper message pump for a WPF window in that
    # mode. `-File` does. So the script is written to a temp file instead; it
    # deletes itself as its last line once the window closes.
    # Windows PowerShell (powershell.exe, not pwsh) reads .ps1 files using the
    # legacy system codepage UNLESS a UTF-8 BOM is present - without it, any
    # multi-byte UTF-8 sequence (emoji, bullets, em-dashes) gets misread as
    # several garbled single-byte characters. utf-8-sig writes that BOM.
    fd, script_path = tempfile.mkstemp(suffix=".ps1", prefix="cloudbyte-blocked-prompt-")
    with open(fd, "w", encoding="utf-8-sig") as f:
        f.write(ps_script)

    argv = ["powershell", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-File", script_path]
    popen_kwargs = dict(
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )

    # powershell.exe is a console-subsystem app: DETACHED_PROCESS (no console at
    # all) makes it die almost immediately on startup, before running a single
    # line of the script - confirmed by direct testing. CREATE_NO_WINDOW (a
    # hidden console, not no console) is the correct flag for silently running
    # a console app in the background.
    #
    # CREATE_BREAKAWAY_FROM_JOB additionally escapes any Job Object the hook's
    # own process tree might belong to, so the popup isn't torn down the
    # instant the short-lived hook process exits.
    try:
        subprocess.Popen(
            argv,
            creationflags=CREATE_NO_WINDOW | CREATE_BREAKAWAY_FROM_JOB,
            **popen_kwargs,
        )
    except OSError:
        # Some jobs don't permit breakaway (CreateProcess then fails outright) -
        # fall back to plain CREATE_NO_WINDOW rather than losing the popup entirely.
        subprocess.Popen(
            argv,
            creationflags=CREATE_NO_WINDOW,
            **popen_kwargs,
        )


def _notify_macos(message: str) -> None:
    safe_message = message.replace("\\", "\\\\").replace('"', '\\"')
    script = (
        f'display alert "CloudByte" message "{safe_message}" '
        f"as warning giving up after {AUTO_CLOSE_SECONDS}"
    )
    subprocess.Popen(
        ["osascript", "-e", script],
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _notify_linux(message: str) -> None:
    subprocess.Popen(
        ["notify-send", "-t", str(AUTO_CLOSE_SECONDS * 1000), "CloudByte - Prompt blocked", message],
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
