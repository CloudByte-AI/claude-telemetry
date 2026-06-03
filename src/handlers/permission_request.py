"""
PermissionRequest Handler

Called when Claude Code is about to request a permission from the user
(e.g. allow a Bash command, file write, MCP tool call, etc.).

Plays an alert WAV file so the user notices the pending dialog even when
they are not actively watching the terminal.

Uses ``sounddevice`` + ``soundfile`` for cross-platform WAV playback (Windows / macOS / Linux).

Configuration
-------------
Set ``settings.alert_sound`` in ``~/.cloudbyte/config.json`` to an absolute
path of any WAV file you want to use:

    {
      "settings": {
        "alert_sound": "",
        "alert_sound_name": "chime"
      }
    }

Priority:
  1. ``settings.alert_sound`` (absolute path to custom WAV file)
  2. ``settings.alert_sound_name`` (built-in sound name: "chime", "soft", or "urgent")
  3. Platform default WAV fallback
  4. winsound.Beep (Windows) or ASCII BEL (\a) fallback
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.common.logging import get_logger, setup_logging

logger = get_logger(__name__)


def _default_wav_path() -> str | None:
    """
    Return a platform-appropriate default WAV file path.
    Only returns WAV files — simpleaudio requires WAV format.
    Falls back to None if no suitable file is found.
    """
    if sys.platform == "win32":
        candidates = [
            r"C:\Windows\Media\Windows Exclamation.wav",
            r"C:\Windows\Media\Alarm01.wav",
            r"C:\Windows\Media\chimes.wav",
            r"C:\Windows\Media\notify.wav",
        ]
    elif sys.platform == "darwin":
        candidates = [
            "/System/Library/Sounds/Funk.wav",
            "/System/Library/Sounds/Glass.wav",
            "/usr/share/sounds/alsa/Front_Right.wav",
        ]
    else:  # Linux / other
        candidates = [
            "/usr/share/sounds/alsa/Front_Right.wav",
            "/usr/share/sounds/alsa/Noise.wav",
        ]

    for path in candidates:
        if Path(path).exists():
            return path

    return None


def _get_wav_path() -> str | None:
    """
    Resolve the WAV path to play.

    Priority:
      1. settings.alert_sound in ~/.cloudbyte/config.json
      2. settings.alert_sound_name in ~/.cloudbyte/config.json ("chime", "soft", "urgent")
      3. Platform default (see _default_wav_path)
      4. None  →  caller falls back to winsound.Beep / BEL
    """
    try:
        from src.common.paths import get_config_file
        from src.common.file_io import read_json

        config_file = get_config_file()
        if config_file.exists():
            config = read_json(config_file)
            settings = config.get("settings", {})

            # 1. Custom path override
            custom = settings.get("alert_sound", "")
            if custom:
                p = Path(custom)
                if p.exists():
                    return str(p)
                else:
                    logger.warning(f"alert_sound path not found: {custom}")

            # 2. Shipped/built-in sound name
            sound_name = settings.get("alert_sound_name", "chime")
            if sound_name:
                sound_path = Path(__file__).parent.parent / "sounds" / f"{sound_name}.wav"
                if sound_path.exists():
                    return str(sound_path)
                else:
                    logger.warning(f"Built-in sound name {sound_name!r} not found at {sound_path}")
    except Exception as e:
        logger.debug(f"Could not read configuration: {e}")

    return _default_wav_path()


def _play_alert_sound() -> None:
    """
    Play the configured alert WAV file using simpleaudio (cross-platform).

    Fallback chain:
      1. simpleaudio.WaveObject  — works on Windows / macOS / Linux
      2. winsound.Beep           — Windows only, no WAV file needed
      3. ASCII BEL (\a)          — last resort
    """
    played = False
    wav_path = _get_wav_path()

    # ── Primary: sounddevice + soundfile (cross-platform) ──────────────────
    if wav_path:
        try:
            import soundfile as sf
            import sounddevice as sd
            data, samplerate = sf.read(wav_path, dtype="float32")
            sd.play(data, samplerate)
            sd.wait()  # block until playback finishes
            played = True
            logger.debug(f"Alert WAV played via sounddevice: {wav_path}")
        except Exception as e:
            logger.debug(f"sounddevice playback failed ({wav_path}): {e}")

    # ── Fallback 1: winsound.Beep (Windows, no WAV needed) ─────────────────
    if not played and sys.platform == "win32":
        try:
            import winsound
            winsound.Beep(1000, 200)
            winsound.Beep(1200, 300)
            played = True
            logger.debug("Alert sound played via winsound.Beep (fallback)")
        except Exception as e:
            logger.debug(f"winsound.Beep failed: {e}")

    # ── Fallback 2: ASCII BEL ──────────────────────────────────────────────
    if not played:
        try:
            sys.stderr.write("\a")
            sys.stderr.flush()
            logger.debug("Alert sound played via ASCII BEL (\\a) — final fallback")
        except Exception as e:
            logger.debug(f"BEL fallback failed: {e}")


def handle_permission_request() -> None:
    """
    Handle the PermissionRequest hook.

    Reads the hook payload from stdin (Claude passes JSON with details
    about the requested permission), plays an alert sound, then exits
    cleanly so Claude can display the permission dialog normally.

    Stdin payload shape (Claude Code docs):
    {
        "session_id": "...",
        "tool_name": "Bash",
        "tool_input": { ... },
        "permission_type": "...",
        ...
    }
    """
    setup_logging(log_to_file=True, log_to_console=False)
    logger.info("=== PermissionRequest Hook ===")

    # Parse stdin — non-fatal if absent or malformed
    hook_data: dict = {}
    try:
        raw = sys.stdin.read().strip()
        if raw:
            hook_data = json.loads(raw)
    except Exception as e:
        logger.debug(f"Could not parse PermissionRequest stdin: {e}")

    tool_name = hook_data.get("tool_name", "<unknown>")
    permission_type = hook_data.get("permission_type", "")
    session_id = hook_data.get("session_id", os.environ.get("CLAUDE_SESSION_ID", ""))

    logger.info(
        f"Permission requested — tool={tool_name!r}, "
        f"type={permission_type!r}, session={session_id}"
    )

    # Play the alert
    _play_alert_sound()

    logger.info("Alert sound emitted for permission dialog")
