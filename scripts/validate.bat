@echo off
REM CloudByte Setup Validation Script (Windows)
REM This script is called directly by Claude Code on setup
REM It validates prerequisites and then runs the Python setup

setlocal enabledelayedexpansion

REM Get the directory where this script is located
set "SCRIPT_DIR=%~dp0"
REM Remove trailing backslash
set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"
REM Get plugin root (parent of scripts folder)
set "PLUGIN_ROOT=%SCRIPT_DIR%\.."

echo ==========================================
echo CloudByte Setup - Prerequisites Check
echo ==========================================
echo.
echo Plugin directory: %PLUGIN_ROOT%
echo.

REM Check Python
echo Checking Python...
python --version >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo ❌ Python not found!
    echo.
    echo Python 3.10+ is required to run CloudByte.
    echo.
    echo Please install Python:
    echo   • winget install Python.Python.3.12
    echo   • Or: https://www.python.org/downloads/
    echo.
    echo After installing Python, restart Claude Code and try again.
    pause
    exit /b 1
)

REM Get Python version
for /f "tokens=2" %%i in ('python --version 2^>^&1') do set PYTHON_VERSION=%%i
echo ✅ Python %PYTHON_VERSION% found

REM Check Python version is 3.10+
for /f "tokens=1,2 delims=." %%a in ("%PYTHON_VERSION%") do (
    set MAJOR=%%a
    set MINOR=%%b
)

set VERSION_OK=0
if %MAJOR% GTR 3 set VERSION_OK=1
if %MAJOR% EQU 3 if %MINOR% GEQ 10 set VERSION_OK=1

if %VERSION_OK% EQU 0 (
    echo.
    echo ❌ Python version %PYTHON_VERSION% is too old!
    echo.
    echo Python 3.10 or higher is required.
    echo Please upgrade Python.
    pause
    exit /b 1
)

REM Check for uv (optional)
echo.
echo Checking for uv (optional)...
uv --version >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    for /f %%i in ('uv --version 2^>^&1') do echo ✅ uv %%i found
    set USE_UV=1
) else (
    echo ⚠️  uv not found (will use pip instead)
    set USE_UV=0
)

echo.
echo ==========================================
echo ✅ Prerequisites OK - Running Setup
echo ==========================================
echo.

REM Change to plugin directory
cd /d "%PLUGIN_ROOT%"

REM Run the Python setup
if %USE_UV% EQU 1 (
    echo Running setup with uv...
    uv run -m src.main setup
) else (
    echo Running setup with python...
    python -m src.main setup
)

REM Capture exit code
set EXIT_CODE=%ERRORLEVEL%

if %EXIT_CODE% EQU 0 (
    echo.
    echo ==========================================
    echo ✅ CloudByte Setup Complete!
    echo ==========================================
) else (
    echo.
    echo ==========================================
    echo ❌ Setup Failed
    echo ==========================================
)

exit /b %EXIT_CODE%
