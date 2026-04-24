#!/bin/bash
# CloudByte Hook Runner for Unix/Linux/Mac
# Automatically detects the plugin directory and runs the appropriate hook

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PLUGIN_ROOT="$(dirname "$SCRIPT_DIR")"

# Run the hook using uv
cd "$PLUGIN_ROOT"
uv run python scripts/run_hook.py "$@"
