@echo off
REM CloudByte Hook Runner for Windows
REM Automatically detects the plugin directory and runs the appropriate hook

REM Get the directory where this batch file is located
set SCRIPT_DIR=%~dp0
set PLUGIN_ROOT=%SCRIPT_DIR%..

REM Run the hook using uv
cd /d "%PLUGIN_ROOT%"
uv run python scripts/run_hook.py %*
