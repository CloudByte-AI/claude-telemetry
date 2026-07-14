"""Shared helpers used by every Cursor hook handler."""

import json
import re
import sys

from ftfy import fix_text

from src.common.logging import get_logger


logger = get_logger(__name__)


_LEADING_SLASH_BEFORE_DRIVE = re.compile(r"^/([A-Za-z]:)")


def read_stdin_json() -> dict:
    """Read and parse the JSON hook payload Cursor sends via stdin."""
    try:
        raw = sys.stdin.buffer.read()
    except Exception as e:
        logger.error(f"Error reading stdin: {e}")
        return {}

    if not raw:
        logger.debug("stdin was empty")
        return {}

    # utf-8-sig strips a leading BOM if present; identical to utf-8 otherwise.
    text = raw.decode("utf-8-sig", errors="replace").strip()
    if not text:
        return {}

    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        logger.error(f"Error parsing stdin JSON: {e}")
        logger.error(f"Raw stdin received (first 500 chars): {text[:500]!r}")
        return {}


def normalize_path(path: str | None) -> str | None:
    """Convert backslashes to forward slashes for consistent storage."""
    if not path:
        return path
    return path.replace("\\", "/")


def normalize_cwd(cwd: str) -> str:
    """Strip a leading '/' before a Windows drive letter (Cursor reports '/c:/...')."""
    return _LEADING_SLASH_BEFORE_DRIVE.sub(r"\1", cwd)


def repair_text(text: str | None) -> str | None:
    """Fix Cursor's mojibake (UTF-8 misread as Latin-1) on emoji/em-dashes/curly quotes."""
    return fix_text(text) if text else text


def debug(message: str) -> None:
    """Print a status line to stderr (stdout is reserved for the hook's JSON response)."""
    print(f"[cursor-telemetry] {message}", file=sys.stderr, flush=True)
