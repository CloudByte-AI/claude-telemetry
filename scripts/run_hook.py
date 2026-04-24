#!/usr/bin/env python3
"""
CloudByte Hook Runner

Automatically detects the plugin directory and runs the appropriate hook command.
This script is designed to work when installed via Claude Code marketplace.
"""

import sys
from pathlib import Path

# Find the plugin root by going up from this script's location
SCRIPT_DIR = Path(__file__).parent.resolve()
PLUGIN_ROOT = SCRIPT_DIR.parent

# Add plugin root to path so we can import src.modules
sys.path.insert(0, str(PLUGIN_ROOT))

# Import and run the appropriate hook
if len(sys.argv) < 2:
    print("Error: No hook command specified", file=sys.stderr)
    sys.exit(1)

command = sys.argv[1]

if command == "setup":
    from src.main import setup
    setup()
elif command == "session_start":
    from src.main import session_start
    session_start()
elif command == "user_prompt":
    from src.main import user_prompt
    user_prompt()
elif command == "stop":
    from src.main import stop
    stop()
elif command == "session_end":
    from src.main import session_end
    session_end()
else:
    print(f"Error: Unknown hook command '{command}'", file=sys.stderr)
    sys.exit(1)
