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
    NC='\033[0m' # No Color
else
    RED=''
    GREEN=''
    YELLOW=''
    BLUE=''
    NC=''
fi

echo "🔍 CloudByte Setup - Prerequisites Check"
echo "=========================================="
echo ""
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

# Check Python
echo "🐍 Checking Python..."
PYTHON_CMD=""

if command_exists python3; then
    PYTHON_CMD="python3"
    PYTHON_VERSION=$(python3 --version 2>&1 | sed 's/Python //')
    echo -e "${GREEN}✅ Python $PYTHON_VERSION found${NC}"
elif command_exists python; then
    PYTHON_CMD="python"
    PYTHON_VERSION=$(python --version 2>&1 | sed 's/Python //')
    echo -e "${GREEN}✅ Python $PYTHON_VERSION found${NC}"
else
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
    echo -e "${GREEN}✅ uv $UV_VERSION found${NC}"
    USE_UV=1
else
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
    echo "Running setup with uv..."
    uv run -m src.main setup
else
    echo "Running setup with $PYTHON_CMD..."
    $PYTHON_CMD -m src.main setup
fi

# Capture exit code
EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
    echo ""
    echo "=========================================="
    echo -e "${GREEN}✅ CloudByte Setup Complete!${NC}"
    echo "=========================================="
    exit 0
else
    echo ""
    echo "=========================================="
    echo -e "${RED}❌ Setup Failed${NC}"
    echo "=========================================="
    exit $EXIT_CODE
fi
