#!/usr/bin/env python3
"""
MCP server launcher — cross-platform, venv-aware.

Start modes (tried in order):
  1. Venv python  — used when .venv is ready; supports any future project deps
  2. Direct exec  — fallback when venv not yet built; works because server.py is stdlib-only

Claude Code calls this via:
    uv run --no-project "${CLAUDE_PLUGIN_ROOT}/scripts/start_mcp.py"

Cursor calls this via the same script, mcp.json at the plugin root:
    uv run --no-project "${CURSOR_PLUGIN_ROOT}/scripts/start_mcp.py"

SessionStart / UserPromptSubmit hooks build the venv in the background (300s timeout).
On the next Claude restart the launcher automatically switches to full venv mode.
"""

import os
import runpy
import subprocess
import sys
from pathlib import Path

_plugin_root = os.environ.get("CLAUDE_PLUGIN_ROOT") or os.environ.get("CURSOR_PLUGIN_ROOT")
PLUGIN_ROOT = Path(_plugin_root) if _plugin_root else Path(__file__).parent.parent
SERVER = PLUGIN_ROOT / "src" / "mcp" / "server.py"


def _venv_python() -> Path:
    if sys.platform == "win32":
        return PLUGIN_ROOT / ".venv" / "Scripts" / "python.exe"
    return PLUGIN_ROOT / ".venv" / "bin" / "python"


def main() -> None:
    python = _venv_python()

    if python.exists():
        if sys.platform == "win32":
            # On Windows, os.execv does NOT replace the current process — it
            # spawns a new child and exits the caller.  When the caller exits,
            # uv run (which is waiting for its subprocess) also exits, and
            # Cursor/Claude detects the top-level process death and closes the
            # stdio pipe before tools/list is ever sent.  Use subprocess.run
            # instead: this process stays alive (blocking on the child), the
            # pipe stays open, and the venv Python still handles the server.
            proc = subprocess.run([str(python), str(SERVER)])
            sys.exit(proc.returncode)
        else:
            # On Unix/macOS execv truly replaces the current process (same PID,
            # same file descriptors) — safe to use here.
            os.execv(str(python), [str(python), str(SERVER)])
    else:
        # Fallback mode: venv not built yet — run server.py directly.
        # Works as long as server.py only needs stdlib (current state).
        # Hooks will build the venv; next Claude restart uses full mode.
        sys.path.insert(0, str(PLUGIN_ROOT))
        runpy.run_path(str(SERVER), run_name="__main__")


if __name__ == "__main__":
    main()
