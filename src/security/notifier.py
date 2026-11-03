"""
Blocked-prompt desktop notification.

The CLI renders a hook's `reason` / `systemMessage` as a yellow warning, but
that's easy to miss when several sessions/terminals are open at once. This
fires a native OS popup - in the CloudByte-AI brand (logo and name as the
desktop notifications show them, blue and cyan accents), light or dark to follow the Windows app theme - as a supplementary heads-up for every client (CLI and VS Code alike), so whichever
session got blocked is obvious regardless of which terminal window has focus.

The popup auto-closes after AUTO_CLOSE_SECONDS if left unattended, and can
always be dismissed early via its OK button / window close.

The popup is spawned fully detached in its own process, so the hook process
that calls this returns immediately regardless of whether/when the popup is
dismissed - it never adds to the hook's own timeout budget. Any failure here
(no GUI session, missing binary, unsupported OS, etc.) is swallowed - it must
never affect the block decision itself.
"""

import base64
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

# The Windows dialog's colours per app theme: each NAME fills __C_NAME__ in
# blocked_prompt_dialog.xaml. The brand accents (logo, gradient, primary
# button) live in the XAML and are the same in both. COPY_DONE colours the
# "Copied" confirmation.
THEME_DARK = "dark"
THEME_LIGHT = "light"
THEMES: dict[str, dict[str, str]] = {
    THEME_DARK: {
        "SURFACE": "#0B1220", "PANEL": "#101A2E", "LINE": "#1E2B45",
        "TEXT": "#EEF3FB", "TEXT_BODY": "#B9C6DB", "TEXT_MUTED": "#7186A6",
        "SCROLL_THUMB": "#2B3C5C",
        "SECONDARY_BG": "#12203A", "SECONDARY_BORDER": "#24395F", "SECONDARY_TEXT": "#CFE4FF",
        "SECONDARY_HOVER_BG": "#18294A", "COPY_ICON": "#9FD8F0", "COPY_DONE": "#4ADE80",
        "ICON_HOVER_BG": "#1A2744", "CLOSE_ICON": "#8FA3C0",
        "STATUS_BG": "#1C1117", "STATUS_BORDER": "#4A1F2A", "STATUS_ICON_BG": "#3B1520",
        "STATUS_ICON": "#F05252", "STATUS_TEXT": "#D9A7B0",
        "SHADOW_OPACITY": "0.55",
    },
    THEME_LIGHT: {
        "SURFACE": "#FFFFFF", "PANEL": "#F5F8FC", "LINE": "#DCE3EF",
        "TEXT": "#0B1730", "TEXT_BODY": "#3A4A64", "TEXT_MUTED": "#6A7A94",
        "SCROLL_THUMB": "#C2CDDF",
        "SECONDARY_BG": "#FFFFFF", "SECONDARY_BORDER": "#C8D5EB", "SECONDARY_TEXT": "#0B3A8F",
        "SECONDARY_HOVER_BG": "#EFF5FF", "COPY_ICON": "#0079B8", "COPY_DONE": "#15803D",
        "ICON_HOVER_BG": "#EEF2F8", "CLOSE_ICON": "#5B6B85",
        "STATUS_BG": "#FEF3F3", "STATUS_BORDER": "#F6C9CC", "STATUS_ICON_BG": "#FDE1E3",
        "STATUS_ICON": "#DC2626", "STATUS_TEXT": "#9B3A42",
        "SHADOW_OPACITY": "0.22",
    },
}

# Windows' "Choose your app mode" setting: AppsUseLightTheme = 1 is light.
_PERSONALIZE_KEY = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"


def windows_theme() -> str:
    """The Windows app theme, light or dark. Dark when it cannot be read. Never raises."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _PERSONALIZE_KEY) as key:
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        return THEME_LIGHT if value == 1 else THEME_DARK
    except Exception:
        return THEME_DARK


def _themed_xaml(xaml: str, theme: str) -> str:
    """The dialog XAML with every __C_NAME__ colour of `theme` filled in."""
    for name, value in THEMES.get(theme, THEMES[THEME_DARK]).items():
        xaml = xaml.replace(f"__C_{name}__", value)
    return xaml

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
    user clicks "Copy sanitized prompt" (Windows only - the WPF dialog is the
    only popup with room for a second control). Without it the button is hidden.
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


def _notify_windows(message: str, masked_prompt: str, theme: str | None = None) -> None:
    """The WPF dialog, in `theme` (light or dark), else the current Windows app theme."""
    CREATE_NO_WINDOW = 0x08000000
    CREATE_BREAKAWAY_FROM_JOB = 0x01000000

    display_message = _strip_emoji(message)
    body_lines = "<LineBreak/>".join(
        _xml_escape(line) for line in display_message.replace("\r\n", "\n").split("\n")
    )

    theme = theme if theme in THEMES else windows_theme()
    colours = THEMES[theme]
    from src.common.notifications import APP_NAME

    xaml = _themed_xaml(_WINDOWS_XAML_PATH.read_text(encoding="utf-8"), theme)
    # The message body last: it is user text and may itself contain a placeholder.
    xaml = (xaml.replace("__APP_NAME__", _xml_escape(APP_NAME))
            .replace("__AUTO_CLOSE__", str(AUTO_CLOSE_SECONDS))
            .replace("__MESSAGE_BODY__", body_lines))

    from src.common.notifications.windows_toast import APP_ICON
    logo_path = str(APP_ICON) if APP_ICON.is_file() else ""
    # Base64, not a here-string: a prompt line starting with '@ would end the
    # here-string early and stop the script, so no popup would appear.
    masked_prompt_b64 = base64.b64encode(masked_prompt.encode("utf-8")).decode("ascii")

    ps_script = f"""Add-Type -AssemblyName PresentationFramework
Add-Type -AssemblyName PresentationCore
Add-Type -AssemblyName WindowsBase

$xaml = @'
{xaml}
'@

$maskedPrompt = [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String('{masked_prompt_b64}'))

$logoPath = @'
{logo_path}
'@

$window = [Windows.Markup.XamlReader]::Parse($xaml)

# The logo is set here rather than in the XAML, so a missing image only
# leaves the space empty instead of stopping the window from loading.
if ($logoPath.Trim()) {{
    try {{
        $logo = New-Object System.Windows.Media.Imaging.BitmapImage
        $logo.BeginInit()
        $logo.UriSource = New-Object System.Uri($logoPath.Trim())
        $logo.CacheOption = [System.Windows.Media.Imaging.BitmapCacheOption]::OnLoad
        $logo.EndInit()
        $window.FindName("Logo").Source = $logo
    }} catch {{ }}
}}

$window.FindName("OkButton").Add_Click({{ $window.Close() }})
$window.FindName("CloseButton").Add_Click({{ $window.Close() }})
$window.Add_KeyDown({{ if ($_.Key -eq [System.Windows.Input.Key]::Escape) {{ $window.Close() }} }})
$window.FindName("Chrome").Add_MouseLeftButtonDown({{ try {{ $window.DragMove() }} catch {{ }} }})

$copyButton = $window.FindName("CopyButton")
$copyLabel = $window.FindName("CopyLabel")
if (-not $maskedPrompt.Trim()) {{
    $copyButton.Visibility = [System.Windows.Visibility]::Collapsed
}}
$copyIdleBrush = [System.Windows.Media.SolidColorBrush]([System.Windows.Media.ColorConverter]::ConvertFromString("{colours['SECONDARY_TEXT']}"))
$copyDoneBrush = [System.Windows.Media.SolidColorBrush]([System.Windows.Media.ColorConverter]::ConvertFromString("{colours['COPY_DONE']}"))
$copyButton.Add_Click({{
    [System.Windows.Clipboard]::SetText($maskedPrompt)
    $copyLabel.Text = "Copied"
    $copyLabel.Foreground = $copyDoneBrush
    $flashTimer = New-Object System.Windows.Threading.DispatcherTimer
    $flashTimer.Interval = [TimeSpan]::FromMilliseconds(1500)
    $flashTimer.Add_Tick({{
        $copyLabel.Text = "Copy sanitized prompt"
        $copyLabel.Foreground = $copyIdleBrush
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
    _show_shared(message)


def _notify_linux(message: str) -> None:
    _show_shared(message)


def _show_shared(message: str) -> None:
    """
    macOS, Linux and WSL: the shared notification backends. On macOS that is a
    dialog with the CloudByte icon (no permission needed); text travels as
    arguments, never as script. On WSL it is a Windows toast.
    """
    from src.common import notifications
    from src.common.notifications.windows_toast import APP_ICON

    # The same name as the Windows dialog and the other desktop notifications.
    notifications.show(notifications.Notice(
        level=notifications.CRITICAL,
        title=f"{notifications.APP_NAME} Security  " + chr(0xB7) + "  Prompt blocked",
        text=_strip_emoji(message),
        icon=APP_ICON if APP_ICON.is_file() else None,
        mac_style=notifications.MAC_DIALOG,
    ))
