#!/usr/bin/env bash
# CloudByte Setup Validation Script
# This script is called directly by Claude Code on setup
# It validates prerequisites and then runs the Python setup

set -e

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_ROOT="$(dirname "$SCRIPT_DIR")"

# Colors for output (disable if not a terminal)
if [ -t 1 ]; then
    RED='\033[0;31m'
    GREEN='\033[0;32m'
    YELLOW='\033[1;33m'
    BLUE='\033[0;34m'
    NC='\033[0m'
else
    RED=''; GREEN=''; YELLOW=''; BLUE=''; NC=''
fi

# ── Cross-platform home directory & .cloudbyte folder ─────────────────────────
# Resolves the user home on Windows (USERPROFILE), macOS, and Linux (HOME)
if [[ "$OSTYPE" == "msys" || "$OSTYPE" == "cygwin" || -n "$USERPROFILE" ]]; then
    # Git Bash / Cygwin on Windows
    USER_HOME="${USERPROFILE:-$HOME}"
else
    USER_HOME="$HOME"
fi

CLOUDBYTE_DIR="$USER_HOME/.cloudbyte"
LOG_DIR="$CLOUDBYTE_DIR/logs"

# Create .cloudbyte and logs dir if they don't exist
if [ ! -d "$CLOUDBYTE_DIR" ]; then
    mkdir -p "$CLOUDBYTE_DIR"
    echo -e "${GREEN}✅ Created ~/.cloudbyte at: $CLOUDBYTE_DIR${NC}"
else
    echo -e "${BLUE}ℹ️  ~/.cloudbyte already exists at: $CLOUDBYTE_DIR${NC}"
fi

mkdir -p "$LOG_DIR"
# ── End home dir setup ─────────────────────────────────────────────────────────

# ── Logging setup ──────────────────────────────────────────────────────────────
LOG_FILE="$LOG_DIR/setup_$(date '+%Y%m%d_%H%M%S').log"

# Tee all stdout+stderr to the log file, keeping terminal output too
exec > >(tee -a "$LOG_FILE") 2>&1

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
}

log "=== CloudByte Setup started ==="
log "OS: $OSTYPE"
log "Home directory: $USER_HOME"
log "CloudByte directory: $CLOUDBYTE_DIR"
log "Log file: $LOG_FILE"
echo ""
# ── End logging setup ──────────────────────────────────────────────────────────

# ── Port 8765 cleanup ──────────────────────────────────────────────────────────
log "🔌 Checking port 8765..."
echo "🔌 Checking port 8765..."
PORT_PID=$(lsof -ti tcp:8765 2>/dev/null || true)

if [ -n "$PORT_PID" ]; then
    log "⚠️  Port 8765 in use by PID(s): $PORT_PID — killing..."
    echo -e "${YELLOW}⚠️  Port 8765 is in use by PID(s): $PORT_PID — killing...${NC}"
    kill -9 $PORT_PID 2>/dev/null || true
    sleep 1

    STILL_RUNNING=$(lsof -ti tcp:8765 2>/dev/null || true)
    if [ -n "$STILL_RUNNING" ]; then
        log "❌ Could not free port 8765 (PID $STILL_RUNNING still running). Aborting."
        echo -e "${RED}❌ Could not free port 8765 (PID $STILL_RUNNING still running). Aborting.${NC}"
        exit 1
    fi

    log "✅ Port 8765 successfully freed."
    echo -e "${GREEN}✅ Port 8765 is now free.${NC}"
else
    log "✅ Port 8765 was already free."
    echo -e "${GREEN}✅ Port 8765 is already free.${NC}"
fi
echo ""
# ── End port cleanup ───────────────────────────────────────────────────────────

echo "🔍 CloudByte Setup - Prerequisites Check"
echo "=========================================="
echo ""
log "Plugin directory: $PLUGIN_ROOT"
echo "📁 Plugin directory: $PLUGIN_ROOT"
echo ""

# Function to check command exists
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# Detect OS
if [[ "$OSTYPE" == "darwin"* ]]; then
    OS="macos"
elif [[ "$OSTYPE" == "linux-gnu"* ]]; then
    OS="linux"
elif [[ "$OSTYPE" == "msys" || "$OSTYPE" == "cygwin" ]]; then
    OS="windows"
else
    OS="unknown"
fi
log "Detected OS: $OS"

# Check Python
echo "🐍 Checking Python..."
PYTHON_CMD=""

if command_exists python3; then
    PYTHON_CMD="python3"
    PYTHON_VERSION=$(python3 --version 2>&1 | sed 's/Python //')
    log "Python found: $PYTHON_VERSION (python3)"
    echo -e "${GREEN}✅ Python $PYTHON_VERSION found${NC}"
elif command_exists python; then
    PYTHON_CMD="python"
    PYTHON_VERSION=$(python --version 2>&1 | sed 's/Python //')
    log "Python found: $PYTHON_VERSION (python)"
    echo -e "${GREEN}✅ Python $PYTHON_VERSION found${NC}"
else
    log "❌ Python not found. Aborting."
    echo ""
    echo -e "${RED}❌ Python not found!${NC}"
    echo ""
    echo "Python 3.10+ is required to run CloudByte."
    echo ""
    echo "Please install Python:"
    if [ "$OS" = "macos" ]; then
        echo "  • macOS: brew install python@3.12"
    elif [ "$OS" = "linux" ]; then
        echo "  • Ubuntu/Debian: sudo apt install python3.12"
        echo "  • Fedora: sudo dnf install python3.12"
        echo "  • Arch: sudo pacman -S python"
    else
        echo "  • Windows: winget install Python.Python.3.12"
        echo "  • Or: https://www.python.org/downloads/"
    fi
    echo ""
    echo "After installing Python, restart Claude Code and try again."
    exit 1
fi

# Check Python version is 3.10+
PYTHON_MAJOR=$(echo "$PYTHON_VERSION" | cut -d. -f1)
PYTHON_MINOR=$(echo "$PYTHON_VERSION" | cut -d. -f2)

VERSION_OK=0
if [ "$PYTHON_MAJOR" -gt 3 ]; then
    VERSION_OK=1
elif [ "$PYTHON_MAJOR" -eq 3 ] && [ "$PYTHON_MINOR" -ge 10 ]; then
    VERSION_OK=1
fi

if [ $VERSION_OK -eq 0 ]; then
    log "❌ Python version $PYTHON_VERSION is too old. Aborting."
    echo ""
    echo -e "${RED}❌ Python version $PYTHON_VERSION is too old!${NC}"
    echo ""
    echo "Python 3.10 or higher is required."
    echo "Please upgrade Python."
    exit 1
fi

# Check for uv (optional)
echo ""
echo "⚡ Checking for uv (optional)..."
if command_exists uv; then
    UV_VERSION=$(uv --version 2>&1)
    log "uv found: $UV_VERSION"
    echo -e "${GREEN}✅ uv $UV_VERSION found${NC}"
    USE_UV=1
else
    log "uv not found — will use pip."
    echo -e "${YELLOW}⚠️  uv not found (will use pip instead)${NC}"
    USE_UV=0
fi

echo ""
echo "=========================================="
echo -e "${GREEN}✅ Prerequisites OK - Running Setup${NC}"
echo "=========================================="
echo ""

# Change to plugin directory
cd "$PLUGIN_ROOT"

# Run the Python setup
if [ $USE_UV -eq 1 ]; then
    log "Running setup with uv..."
    echo "Running setup with uv..."
    uv run -m src.main setup
else
    log "Running setup with $PYTHON_CMD..."
    echo "Running setup with $PYTHON_CMD..."
    $PYTHON_CMD -m src.main setup
fi

EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
    log "✅ CloudByte Setup completed successfully."
    echo ""
    echo "=========================================="
    echo -e "${GREEN}✅ CloudByte Setup Complete!${NC}"
    echo "=========================================="
    echo ""
    echo "📄 Log saved to: $LOG_FILE"
    exit 0
else
    log "❌ Setup failed with exit code $EXIT_CODE."
    echo ""
    echo "=========================================="
    echo -e "${RED}❌ Setup Failed (exit code: $EXIT_CODE)${NC}"
    echo "=========================================="
    echo ""
    echo "📄 Log saved to: $LOG_FILE"
    exit $EXIT_CODE
fi