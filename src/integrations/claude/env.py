"""
Values Claude Code exposes to a hook process through the environment.

Neither `entrypoint` nor the client version appears in any hook payload, and
searching the hooks reference for "entrypoint" returns nothing at all. Both are
however present in the environment of a process Claude Code spawns, verified by
logging os.environ from a real UserPromptSubmit hook:

    CLAUDE_CODE_ENTRYPOINT=cli
    AI_AGENT=claude-code_2-1-220_harness

They are set per Claude Code process, not machine-wide: two sessions running
concurrently on the same machine reported `cli` / `_harness` and
`claude-vscode` / `_agent` respectively, with different CLAUDE_PID and
CLAUDE_CODE_SESSION_ID values. Nothing in this repository sets them, and
neither the Windows user nor system environment defines them.

IMPORTANT - both variables are undocumented. hooks.md lists only
CLAUDE_PROJECT_DIR, CLAUDE_PLUGIN_ROOT, CLAUDE_PLUGIN_DATA, CLAUDE_CODE_REMOTE,
CLAUDE_CODE_BRIDGE_SESSION_ID and CLAUDE_EFFORT, so there is no compatibility
promise here. Every accessor returns None rather than guessing when the
variable is missing or malformed, and the transcript back-fill in stop() stays
in place as the fallback for exactly that case.

Do not use CLAUDE_CODE_EXECPATH for the version: it also embeds the version
string, but it was absent from the real hook environment even though it is
present in the Bash tool's.
"""

import os
from typing import Optional

from src.common.logging import get_logger


logger = get_logger(__name__)


def get_entrypoint() -> Optional[str]:
    """
    How this Claude Code session was launched - "cli", "claude-vscode", etc.

    Matches the transcript's `entrypoint` field exactly, including the two
    values already present in the database.
    """
    value = (os.environ.get("CLAUDE_CODE_ENTRYPOINT") or "").strip()
    return value or None


def get_client_version() -> Optional[str]:
    """
    Claude Code version, parsed out of AI_AGENT.

    The variable looks like "claude-code_2-1-220_harness": product, then a
    dash-separated version, then the role of the process. The role differs by
    spawn path - "harness" in a hook, "agent" in the Bash tool - so only the
    middle segment is read rather than matching the whole string.

    Returns:
        Dotted version such as "2.1.220", or None if the variable is absent or
        does not have the expected shape.
    """
    raw = (os.environ.get("AI_AGENT") or "").strip()
    if not raw:
        return None

    parts = raw.split("_")
    if len(parts) < 2:
        logger.debug(f"AI_AGENT has unexpected shape, ignoring: {raw!r}")
        return None

    version = parts[1].replace("-", ".")

    # Only accept something that actually looks like a version, so a format
    # change lands as a NULL the transcript can back-fill rather than as
    # garbage in the column.
    if not version or not all(seg.isdigit() for seg in version.split(".") if seg != ""):
        logger.debug(f"AI_AGENT version segment not numeric, ignoring: {raw!r}")
        return None

    return version
