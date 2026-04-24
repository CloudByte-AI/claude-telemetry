@echo off
REM CloudByte Installation Script for Windows
REM This script checks for prerequisites and sets up the plugin

setlocal enabledelayedexpansion

echo ==================================================
echo CloudByte Plugin Installation
echo ==================================================
echo.

REM Get the directory where this script is located
set "SCRIPT_DIR=%~dp0"
REM Remove trailing backslash
set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"

echo [1/4] Checking Prerequisites...
echo.

REM Check Python is installed
echo Checking Python...
python --version >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo ❌ Python not found!
    echo.
    echo Python 3.10+ is required to run CloudByte.
    echo.
    echo Please install Python from:
    echo   https://www.python.org/downloads/
    echo.
    echo Or use winget:
    echo   winget install Python.Python.3.12
    echo.
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

if %MAJOR% LSS 3 (
    goto :PYTHON_TOO_OLD
)
if %MAJOR% EQU 3 if %MINOR% LSS 10 (
    goto :PYTHON_TOO_OLD
)
goto :PYTHON_OK

:PYTHON_TOO_OLD
echo.
echo ❌ Python version %PYTHON_VERSION% is too old!
echo.
echo Python 3.10 or higher is required.
echo Please upgrade from: https://www.python.org/downloads/
echo.
pause
exit /b 1

:PYTHON_OK

REM Check for uv
echo.
echo Checking for uv...
uv --version >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    for /f %%i in ('uv --version 2^>^&1') do echo ✅ uv %%i found
    set HAS_UV=1
) else (
    echo ⚠️  uv not found (optional but recommended)
    echo.
    echo uv is a fast Python package manager.
    echo The plugin will work without it, but uv is much faster.
    echo.
    set HAS_UV=0
)

REM Offer to install uv if missing
if %HAS_UV% EQU 0 (
    set /p INSTALL_UV="Would you like to install uv now? [Y/n]: "
    if /i "!INSTALL_UV!"=="" set INSTALL_UV=Y
    if /i "!INSTALL_UV!"=="Y" (
        echo.
        echo Installing uv using pip...
        python -m pip install --user uv
        if %ERRORLEVEL% EQU 0 (
            echo ✅ uv installed successfully!
            set HAS_UV=1
        ) else (
            echo ⚠️  Failed to install uv, continuing with pip...
        )
    )
)

echo.
echo ==================================================
echo [2/4] Plugin Setup
echo ==================================================
echo.
echo Plugin directory: %SCRIPT_DIR%
echo.

REM Check if hooks.json exists
set "HOOKS_FILE=%SCRIPT_DIR%\.claude\hooks.json"
if not exist "%HOOKS_FILE%" (
    echo ⚠️  Warning: hooks.json not found at %HOOKS_FILE%
    echo Continuing anyway...
)

echo.
echo ==================================================
echo [3/4] Running Initial Setup
echo ==================================================
echo.

cd /d "%SCRIPT_DIR%"

REM Run setup with uv or python
if %HAS_UV% EQU 1 (
    echo Running setup with uv...
    uv run -m src.main setup
) else (
    echo Running setup with python...
    python -m src.main setup
)

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo ⚠️  Setup encountered issues.
    echo You can try running manually:
    echo   cd %SCRIPT_DIR%
    echo   python -m src.main setup
    echo.
    pause
    exit /b 1
)

echo.
echo ==================================================
echo [4/4] Installation Complete!
echo ==================================================
echo.
echo ✅ CloudByte plugin is ready to use!
echo.
echo 📁 Plugin directory: %SCRIPT_DIR%
echo 📊 Data directory: %USERPROFILE%\.cloudbyte\
echo.
echo What's been created:
echo   • .cloudbyte\ folder in your user directory
echo   • .cloudbyte\data\cloudbyte.db (SQLite database)
echo   • .cloudbyte\logs\ (log files)
echo   • .cloudbyte\config.json (settings)
echo.
echo 🎉 Start using CloudByte with Claude Code!
echo.
pause
