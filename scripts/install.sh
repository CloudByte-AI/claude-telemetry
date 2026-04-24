#!/usr/bin/env bash
# CloudByte Installation Script for Linux and macOS
# Checks for prerequisites and sets up the plugin

set -e

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

echo "=================================================="
echo "☁️  CloudByte Plugin Installation"
echo "=================================================="
echo ""

# Get the directory where this script is located
# Handle different symlink scenarios
SCRIPT_SOURCE="${BASH_SOURCE[0]:-$0}"
while [ -h "$SCRIPT_SOURCE" ]; do
    SCRIPT_DIR="$(cd -P "$(dirname "$SCRIPT_SOURCE")" && pwd)"
    SCRIPT_SOURCE="$(readlink "$SCRIPT_SOURCE")"
    [[ $SCRIPT_SOURCE != /* ]] && SCRIPT_SOURCE="$SCRIPT_DIR/$SCRIPT_SOURCE"
done
SCRIPT_DIR="$(cd -P "$(dirname "$SCRIPT_SOURCE")" && pwd)"

echo "[1/4] Checking Prerequisites..."
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
echo "Checking Python..."
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
        echo "           OR: brew install python"
    elif [ "$OS" = "linux" ]; then
        echo "  • Ubuntu/Debian: sudo apt update && sudo apt install python3.12"
        echo "  • Fedora: sudo dnf install python3.12"
        echo "  • Arch: sudo pacman -S python"
        echo "  • openSUSE: sudo zypper install python312"
    fi
    echo "  • Or download from: https://www.python.org/downloads/"
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
    echo "Please upgrade Python:"
    if [ "$OS" = "macos" ]; then
        echo "  • macOS: brew upgrade python"
    elif [ "$OS" = "linux" ]; then
        echo "  • Ubuntu/Debian: sudo apt install python3.12"
        echo "  • Fedora: sudo dnf install python3.12"
        echo "  • Arch: sudo pacman -S python"
    fi
    exit 1
fi

# Check for uv
echo ""
echo "Checking for uv..."
HAS_UV=0

if command_exists uv; then
    UV_VERSION=$(uv --version 2>&1)
    echo -e "${GREEN}✅ uv $UV_VERSION found${NC}"
    HAS_UV=1
else
    echo -e "${YELLOW}⚠️  uv not found (optional but recommended)${NC}"
    echo ""
    echo "uv is a fast Python package manager."
    echo "The plugin will work without it, but uv is much faster."
    echo ""

    # Ask if user wants to install uv (only if interactive)
    if [ -t 0 ]; then
        printf "Would you like to install uv now? [Y/n]: "
        read -r REPLY
        echo

        if [ -z "$REPLY" ] || [[ "$REPLY" =~ ^[Yy]$ ]]; then
            echo ""
            echo "Installing uv using pip..."
            if $PYTHON_CMD -m pip install --user uv 2>/dev/null; then
                # Add uv to PATH if installed via --user
                export PATH="$HOME/.local/bin:$PATH"
                echo -e "${GREEN}✅ uv installed successfully!${NC}"
                HAS_UV=1
            else
                echo -e "${YELLOW}⚠️  Failed to install uv, continuing with pip...${NC}"
            fi
        else
            echo -e "${BLUE}ℹ️  Continuing without uv (will use pip instead)...${NC}"
        fi
    else
        echo -e "${BLUE}ℹ️  Non-interactive mode: Continuing without uv...${NC}"
    fi
fi

echo ""
echo "=================================================="
echo "[2/4] Plugin Setup"
echo "=================================================="
echo ""
echo "Plugin directory: $SCRIPT_DIR"
echo ""

# Check if hooks.json exists
HOOKS_FILE="$SCRIPT_DIR/.claude/hooks.json"
if [ ! -f "$HOOKS_FILE" ]; then
    echo -e "${YELLOW}⚠️  Warning: hooks.json not found at $HOOKS_FILE${NC}"
    echo "Continuing anyway..."
fi

echo ""
echo "=================================================="
echo "[3/4] Running Initial Setup"
echo "=================================================="
echo ""

cd "$SCRIPT_DIR"

# Run setup with uv or python
SETUP_SUCCESS=0
if [ $HAS_UV -eq 1 ]; then
    echo "Running setup with uv..."
    if uv run -m src.main setup; then
        SETUP_SUCCESS=1
    fi
else
    echo "Running setup with $PYTHON_CMD..."
    if $PYTHON_CMD -m src.main setup; then
        SETUP_SUCCESS=1
    fi
fi

if [ $SETUP_SUCCESS -eq 0 ]; then
    echo ""
    echo -e "${YELLOW}⚠️  Setup encountered issues.${NC}"
    echo "You can try running manually:"
    echo "  cd $SCRIPT_DIR"
    echo "  $PYTHON_CMD -m src.main setup"
    echo ""
    exit 1
fi

echo ""
echo "=================================================="
echo "[4/4] Installation Complete!"
echo "=================================================="
echo ""
echo -e "${GREEN}✅ CloudByte plugin is ready to use!${NC}"
echo ""
echo "📁 Plugin directory: $SCRIPT_DIR"
echo "📊 Data directory: ~/.cloudbyte/"
echo ""
echo "What's been created:"
echo "  • ~/.cloudbyte/ folder in your home directory"
echo "  • ~/.cloudbyte/data/cloudbyte.db (SQLite database)"
echo "  • ~/.cloudbyte/logs/ (log files)"
echo "  • ~/.cloudbyte/config.json (settings)"
echo ""
echo -e "${GREEN}🎉 Start using CloudByte with Claude Code!${NC}"
echo ""
