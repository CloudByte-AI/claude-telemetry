#!/usr/bin/env python3
"""
MCP server launcher - cross-platform, venv-aware.

Start modes (tried in order):
  1. Venv python  - used when .venv is ready; supports any future project deps
  2. Direct exec  - fallback when venv not yet built, or when the venv python
                    cannot be started (for example a Windows App Control block);
                    works because server.py is stdlib-only

On Windows this launcher also keeps the plugin environment on a signed Python
(src/common/windows_signed_python.py). It runs on uv's interpreter, not on the
environment's launcher, so it still runs when Windows blocks that launcher and
every hook fails; it then starts the move, or tells the user what to install.

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
import threading
import time
from pathlib import Path

_plugin_root = os.environ.get("CLAUDE_PLUGIN_ROOT") or os.environ.get("CURSOR_PLUGIN_ROOT")
PLUGIN_ROOT = Path(_plugin_root) if _plugin_root else Path(__file__).parent.parent
SERVER = PLUGIN_ROOT / "src" / "mcp" / "server.py"


def _venv_python() -> Path:
    if sys.platform == "win32":
        return PLUGIN_ROOT / ".venv" / "Scripts" / "python.exe"
    return PLUGIN_ROOT / ".venv" / "bin" / "python"


# Long enough for the first hook of a fresh install or update to have built the
# environment, which happens after this launcher starts.
RECHECK_DELAY_SECONDS = 45


def _signed_python_module():
    if str(PLUGIN_ROOT) not in sys.path:
        sys.path.insert(0, str(PLUGIN_ROOT))
    from src.common import windows_signed_python

    return windows_signed_python


def _start_signed_python_check(trigger: str, launcher_blocked: bool = False,
                               delay: float = 0.0) -> None:
    """
    Windows only: keep the environment on a signed Python. When Windows already
    blocks the environment's python, the check starts at once in its own
    process, because the client may close this launcher within a second (the
    session's first prompt was blocked); that process also shows the steps when
    no signed Python is installed. Otherwise it runs later on a daemon thread.
    Writes nothing to stdout (the MCP channel). Never raises.
    """
    if sys.platform != "win32":
        return
    try:
        signed = _signed_python_module()
        if launcher_blocked:
            signed.start_check(trigger, PLUGIN_ROOT, launcher_blocked=True)
            return

        def run() -> None:
            time.sleep(delay)
            try:
                signed.ensure_signed_environment(trigger, PLUGIN_ROOT)
            except Exception:
                pass

        threading.Thread(target=run, name="cloudbyte-signed-python", daemon=True).start()
    except Exception:
        pass


def main() -> None:
    python = _venv_python()

    if python.exists():
        try:
            if sys.platform == "win32":
                # Checks again once the session is running: a fresh install or
                # update builds the environment after this launcher starts.
                _start_signed_python_check("mcp_recheck", delay=RECHECK_DELAY_SECONDS)
                # On Windows, os.execv does NOT replace the current process - it
                # spawns a new child and exits the caller.  When the caller exits,
                # uv run (which is waiting for its subprocess) also exits, and
                # Cursor/Claude detects the top-level process death and closes the
                # stdio pipe before tools/list is ever sent.  Use subprocess.run
                # instead: this process stays alive (blocking on the child), the
                # pipe stays open, and the venv Python still handles the server.
                proc = subprocess.run([str(python), str(SERVER)])
                sys.exit(proc.returncode)
            # On Unix/macOS execv truly replaces the current process (same PID,
            # same file descriptors) - safe to use here.
            os.execv(str(python), [str(python), str(SERVER)])
        except OSError as exc:
            # The environment's python cannot be started. The server needs only
            # the standard library, so it runs in this interpreter instead.
            print(f"CloudByte: the plugin's Python could not start ({exc}); "
                  "running the observation server in the launcher's Python.", file=sys.stderr)
            blocked = getattr(exc, "winerror", None) in (4551, 1260)
            _start_signed_python_check("mcp_blocked", launcher_blocked=blocked)
    else:
        _start_signed_python_check("mcp_recheck", delay=RECHECK_DELAY_SECONDS)
    # Fallback mode: venv not built yet, or its python cannot start - run
    # server.py directly. Works as long as server.py only needs stdlib.
    sys.path.insert(0, str(PLUGIN_ROOT))
    runpy.run_path(str(SERVER), run_name="__main__")


if __name__ == "__main__":
    main()
