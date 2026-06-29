#!/usr/bin/env python3
"""
MCP server launcher — cross-platform, venv-aware.

Start modes (tried in order):
  1. Venv python  — used when .venv is ready; supports any future project deps
  2. Direct exec  — fallback when venv not yet built; works because server.py is stdlib-only

Claude Code calls this via:
    uv run --no-project "${CLAUDE_PLUGIN_ROOT}/scripts/start_mcp.py"

SessionStart / UserPromptSubmit hooks build the venv in the background (300s timeout).
On the next Claude restart the launcher automatically switches to full venv mode.
"""

import os
import runpy
import sys
from pathlib import Path

PLUGIN_ROOT = Path(os.environ.get("CLAUDE_PLUGIN_ROOT", Path(__file__).parent.parent))
SERVER = PLUGIN_ROOT / "src" / "mcp" / "server.py"


def _venv_python() -> Path:
    if sys.platform == "win32":
        return PLUGIN_ROOT / ".venv" / "Scripts" / "python.exe"
    return PLUGIN_ROOT / ".venv" / "bin" / "python"


def main() -> None:
    python = _venv_python()

    if python.exists():
        # Full mode: replace this process with venv python running the server.
        # Any future project-level imports in server.py will work here.
        os.execv(str(python), [str(python), str(SERVER)])
    else:
        # Fallback mode: venv not built yet — run server.py directly.
        # Works as long as server.py only needs stdlib (current state).
        # Hooks will build the venv; next Claude restart uses full mode.
        sys.path.insert(0, str(PLUGIN_ROOT))
        runpy.run_path(str(SERVER), run_name="__main__")


if __name__ == "__main__":
    main()
