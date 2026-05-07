#!/usr/bin/env bash
# CloudByte Setup Validation Script
# Pure installation setup - auto-installs Python and uv if missing

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_ROOT="$(dirname "$SCRIPT_DIR")"

# Colors
if [ -t 1 ]; then
    RED='\033[0;31m'
    GREEN='\033[0;32m'
    YELLOW='\033[1;33m'
    BLUE='\033[0;34m'
    NC='\033[0m'
else
    RED=''; GREEN=''; YELLOW=''; BLUE=''; NC=''
fi

# Home directory
if [[ "$OSTYPE" == "msys" || "$OSTYPE" == "cygwin" || -n "$USERPROFILE" ]]; then
    USER_HOME="${USERPROFILE:-$HOME}"
else
    USER_HOME="$HOME"
fi

CLOUDBYTE_DIR="$USER_HOME/.cloudbyte"
LOG_DIR="$CLOUDBYTE_DIR/logs"
SETUP_LOG_DIR="$CLOUDBYTE_DIR/logs/setup"
INIT_FILE="$CLOUDBYTE_DIR/.initialized"
VERSION_FILE="$CLOUDBYTE_DIR/.version"

mkdir -p "$LOG_DIR"
mkdir -p "$SETUP_LOG_DIR"

# Get current plugin version from .claude-plugin/plugin.json
CURRENT_VERSION=$(grep '"version"' "$PLUGIN_ROOT/.claude-plugin/plugin.json" \
    2>/dev/null | head -1 | \
    sed 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/' | \
    tr -d '[:space:]\r')

# OS Detection
if [[ "$OSTYPE" == "darwin"* ]]; then
    OS="macos"
elif [[ "$OSTYPE" == "linux-gnu"* ]]; then
    OS="linux"
elif [[ "$OSTYPE" == "msys" || "$OSTYPE" == "cygwin" ]]; then
    OS="windows"
else
    OS="unknown"
fi

# Windows: exit cleanly, let ps1 handle setup
if [ "$OS" = "windows" ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] SH: Windows detected - deferring to validate.ps1" \
        >> "$LOG_DIR/hook_trace.log"
    exit 0
fi

# Early exit if already initialized for this version
if [ -f "$INIT_FILE" ] && [ -f "$VERSION_FILE" ]; then
    SAVED_VERSION=$(cat "$VERSION_FILE" 2>/dev/null)
    if [ "$SAVED_VERSION" = "$CURRENT_VERSION" ]; then
        exit 0
    fi
    echo "Version changed: $SAVED_VERSION to $CURRENT_VERSION"
    echo "Re-running setup for new version..."
fi

# Logging - setup/setup-YYYY-MM-DD.log
DATE_STR=$(date '+%Y-%m-%d')
LOG_FILE="$SETUP_LOG_DIR/setup-$DATE_STR.log"
exec > >(tee -a "$LOG_FILE") 2>&1

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

log "=== CloudByte Setup started ==="
log "Version: $CURRENT_VERSION"
log "OS: $OSTYPE"
log "Home: $USER_HOME"
log "Plugin: $PLUGIN_ROOT"
echo ""

log "Detected OS: $OS"

echo "CloudByte Setup - Prerequisites Check"
echo "=========================================="
echo ""
echo "Plugin directory: $PLUGIN_ROOT"
echo ""

# Python install function
install_python() {
    log "Python not found - installing automatically..."

    if [ "$OS" = "macos" ]; then
        if command -v brew >/dev/null 2>&1; then
            log "Installing Python via brew..."
            brew install python@3.12
            INSTALL_EXIT=$?
        else
            log "Homebrew not found - installing homebrew first..."
            /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
            INSTALL_EXIT=$?
            if [ $INSTALL_EXIT -eq 0 ]; then
                export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
                brew install python@3.12
                INSTALL_EXIT=$?
            fi
        fi
    elif [ "$OS" = "linux" ]; then
        if command -v apt >/dev/null 2>&1; then
            log "Installing Python via apt..."
            sudo apt update -y
            sudo apt install -y python3.12 python3.12-venv python3-pip
            INSTALL_EXIT=$?
        elif command -v dnf >/dev/null 2>&1; then
            log "Installing Python via dnf..."
            sudo dnf install -y python3.12
            INSTALL_EXIT=$?
        elif command -v pacman >/dev/null 2>&1; then
            log "Installing Python via pacman..."
            sudo pacman -S --noconfirm python
            INSTALL_EXIT=$?
        elif command -v zypper >/dev/null 2>&1; then
            log "Installing Python via zypper..."
            sudo zypper install -y python312
            INSTALL_EXIT=$?
        else
            log "No supported package manager found"
            echo "Please install Python 3.10+ manually: https://www.python.org/downloads/"
            return 1
        fi
    else
        log "Unknown OS - cannot auto-install Python"
        echo "Please install Python 3.10+ manually: https://www.python.org/downloads/"
        return 1
    fi

    if [ "${INSTALL_EXIT:-1}" -ne 0 ]; then
        log "Failed to install Python"
        echo "Please install manually: https://www.python.org/downloads/"
        return 1
    fi

    log "Python installed successfully"
    return 0
}

# Python check - detects Windows Store stub
echo "Checking Python..."
PYTHON_CMD=""
PYTHON_VERSION=""

python_works() {
    local cmd=$1
    local ver
    ver=$("$cmd" --version 2>&1)
    echo "$ver" | grep -q "^Python [0-9]"
    return $?
}

if command -v python3 >/dev/null 2>&1 && python_works python3; then
    PYTHON_CMD="python3"
    PYTHON_VERSION=$(python3 --version 2>&1 | sed 's/Python //')
    log "Python found: $PYTHON_VERSION (python3)"
    echo "Python $PYTHON_VERSION found"
elif command -v python >/dev/null 2>&1 && python_works python; then
    PYTHON_CMD="python"
    PYTHON_VERSION=$(python --version 2>&1 | sed 's/Python //')
    log "Python found: $PYTHON_VERSION (python)"
    echo "Python $PYTHON_VERSION found"
else
    install_python
    if [ $? -ne 0 ]; then
        exit 1
    fi
    export PATH="/usr/local/bin:/opt/homebrew/bin:$PATH"
    if command -v python3 >/dev/null 2>&1 && python_works python3; then
        PYTHON_CMD="python3"
        PYTHON_VERSION=$(python3 --version 2>&1 | sed 's/Python //')
        log "Python now available: $PYTHON_VERSION"
    elif command -v python >/dev/null 2>&1 && python_works python; then
        PYTHON_CMD="python"
        PYTHON_VERSION=$(python --version 2>&1 | sed 's/Python //')
        log "Python now available: $PYTHON_VERSION"
    else
        log "Python not available after install"
        exit 1
    fi
fi

# Python version 3.10+ check
PYTHON_MAJOR=$(echo "$PYTHON_VERSION" | cut -d. -f1)
PYTHON_MINOR=$(echo "$PYTHON_VERSION" | cut -d. -f2)

VERSION_OK=0
if [ "$PYTHON_MAJOR" -gt 3 ]; then
    VERSION_OK=1
elif [ "$PYTHON_MAJOR" -eq 3 ] && [ "$PYTHON_MINOR" -ge 10 ]; then
    VERSION_OK=1
fi

if [ $VERSION_OK -eq 0 ]; then
    log "Python $PYTHON_VERSION too old - need 3.10+"
    echo "Please upgrade: https://www.python.org/downloads/"
    exit 1
fi

# uv check and auto-install
echo ""
echo "Checking uv..."

if command -v uv >/dev/null 2>&1; then
    UV_VERSION=$(uv --version 2>&1)
    log "uv: $UV_VERSION"
    USE_UV=1
else
    log "uv not found - installing automatically..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    INSTALL_EXIT=$?

    if [ $INSTALL_EXIT -ne 0 ]; then
        log "Failed to install uv"
        echo "Please install manually: https://docs.astral.sh/uv/getting-started/installation/"
        exit 1
    fi

    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"

    if command -v uv >/dev/null 2>&1; then
        log "uv installed successfully"
        USE_UV=1
    else
        log "uv installed but not on PATH yet"
        USE_UV=0
    fi
fi

# Run setup
echo ""
echo "=========================================="
echo "Prerequisites OK - Running Setup"
echo "=========================================="
echo ""

cd "$PLUGIN_ROOT"

if [ $USE_UV -eq 1 ]; then
    log "Running setup with uv..."
    uv run -m src.main setup
    EXIT_CODE=$?
else
    log "Running setup with $PYTHON_CMD..."
    $PYTHON_CMD -m src.main setup
    EXIT_CODE=$?
fi

# Write .initialized on success
if [ $EXIT_CODE -eq 0 ]; then
    echo "$CURRENT_VERSION" > "$VERSION_FILE"
    touch "$INIT_FILE"
    log "Initialized version $CURRENT_VERSION"
    log "Setup completed successfully"
    echo ""
    echo "=========================================="
    echo "CloudByte Setup Complete!"
    echo "=========================================="
    echo ""
    echo "Log saved to: $LOG_FILE"
    exit 0
else
    log "Setup failed with exit code $EXIT_CODE"
    echo ""
    echo "=========================================="
    echo "Setup Failed (exit code: $EXIT_CODE)"
    echo "=========================================="
    echo ""
    echo "Log saved to: $LOG_FILE"
    exit $EXIT_CODE
fi
