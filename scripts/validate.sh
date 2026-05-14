#!/usr/bin/env bash
# CloudByte Prerequisites Script
# Checks and installs Python 3.12 and uv
# Run directly or via skill - no plugin context required

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

# Logging setup
CLOUDBYTE_DIR="$USER_HOME/.cloudbyte"
LOG_DIR="$CLOUDBYTE_DIR/logs"
SETUP_LOG_DIR="$CLOUDBYTE_DIR/logs/setup"

mkdir -p "$LOG_DIR"
mkdir -p "$SETUP_LOG_DIR"

DATE_STR=$(date '+%Y-%m-%d')
LOG_FILE="$SETUP_LOG_DIR/setup-$DATE_STR.log"
exec > >(tee -a "$LOG_FILE") 2>&1

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

# ── OS Detection ───────────────────────────────────────────────────────────────

if [[ "$OSTYPE" == "darwin"* ]]; then
    OS="macos"
elif [[ "$OSTYPE" == "linux-gnu"* ]]; then
    OS="linux"
elif [[ "$OSTYPE" == "msys" || "$OSTYPE" == "cygwin" ]]; then
    OS="windows"
else
    OS="unknown"
fi

# Windows: defer to validate.ps1
if [ "$OS" = "windows" ]; then
    log "Windows detected - deferring to validate.ps1"
    exit 0
fi

log "=== CloudByte Prerequisites Check ==="
log "OS: $OS ($OSTYPE)"
log "Home: $USER_HOME"

echo ""
echo "╔══════════════════════════════════════╗"
echo "║   CloudByte Prerequisites Check      ║"
echo "╚══════════════════════════════════════╝"
echo ""

# ── Python ─────────────────────────────────────────────────────────────────────

install_python() {
    log "Python not found - installing 3.12..."
    echo -e "${YELLOW}Python not found - installing 3.12...${NC}"

    if [ "$OS" = "macos" ]; then
        if ! command -v brew >/dev/null 2>&1; then
            log "Homebrew not found - installing first..."
            echo "Installing Homebrew..."
            /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
            if [ $? -ne 0 ]; then
                log "Homebrew install failed"
                echo "Please install Homebrew manually: https://brew.sh"
                return 1
            fi
            export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
        fi
        log "Installing Python 3.12 via brew..."
        brew install python@3.12
        INSTALL_EXIT=$?

    elif [ "$OS" = "linux" ]; then
        if command -v apt >/dev/null 2>&1; then
            log "Installing Python 3.12 via apt..."
            sudo apt update -y && sudo apt install -y python3.12 python3.12-venv python3-pip
            INSTALL_EXIT=$?
        elif command -v dnf >/dev/null 2>&1; then
            log "Installing Python 3.12 via dnf..."
            sudo dnf install -y python3.12
            INSTALL_EXIT=$?
        elif command -v pacman >/dev/null 2>&1; then
            log "Installing Python via pacman..."
            sudo pacman -S --noconfirm python
            INSTALL_EXIT=$?
        elif command -v zypper >/dev/null 2>&1; then
            log "Installing Python 3.12 via zypper..."
            sudo zypper install -y python312
            INSTALL_EXIT=$?
        else
            log "No supported package manager found"
            echo "Please install Python 3.12 manually: https://www.python.org/downloads/"
            return 1
        fi
    else
        log "Unknown OS - cannot auto-install Python"
        echo "Please install Python 3.12 manually: https://www.python.org/downloads/"
        return 1
    fi

    if [ "${INSTALL_EXIT:-1}" -ne 0 ]; then
        log "Python 3.12 install failed"
        echo "Please install manually: https://www.python.org/downloads/"
        return 1
    fi

    log "Python 3.12 installed successfully"
    return 0
}

python_works() {
    local ver
    ver=$("$1" --version 2>&1)
    echo "$ver" | grep -q "^Python [0-9]"
}

echo "── Checking Python ──────────────────────"

PYTHON_CMD=""
PYTHON_VERSION=""

if command -v python3 >/dev/null 2>&1 && python_works python3; then
    PYTHON_CMD="python3"
    PYTHON_VERSION=$(python3 --version 2>&1 | sed 's/Python //')
elif command -v python >/dev/null 2>&1 && python_works python; then
    PYTHON_CMD="python"
    PYTHON_VERSION=$(python --version 2>&1 | sed 's/Python //')
else
    install_python
    if [ $? -ne 0 ]; then exit 1; fi
    export PATH="/usr/local/bin:/opt/homebrew/bin:$PATH"
    if command -v python3 >/dev/null 2>&1 && python_works python3; then
        PYTHON_CMD="python3"
        PYTHON_VERSION=$(python3 --version 2>&1 | sed 's/Python //')
    elif command -v python >/dev/null 2>&1 && python_works python; then
        PYTHON_CMD="python"
        PYTHON_VERSION=$(python --version 2>&1 | sed 's/Python //')
    else
        log "Python not available after install"
        echo -e "${RED}✗ Python not available after install${NC}"
        exit 1
    fi
fi

log "Python found: $PYTHON_VERSION ($PYTHON_CMD)"
echo -e "${GREEN}✓ Python $PYTHON_VERSION${NC}"

# Version check - 3.10+ required
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
    echo -e "${RED}✗ Python $PYTHON_VERSION is too old. Need 3.10+${NC}"
    echo "Please install Python 3.12: https://www.python.org/downloads/"
    exit 1
fi

# ── uv ─────────────────────────────────────────────────────────────────────────

echo ""
echo "── Checking uv ──────────────────────────"

if command -v uv >/dev/null 2>&1; then
    UV_VERSION=$(uv --version 2>&1)
    log "uv found: $UV_VERSION"
    echo -e "${GREEN}✓ $UV_VERSION${NC}"
else
    log "uv not found - installing..."
    echo -e "${YELLOW}uv not found - installing...${NC}"

    curl -LsSf https://astral.sh/uv/install.sh | sh
    INSTALL_EXIT=$?

    if [ $INSTALL_EXIT -ne 0 ]; then
        log "uv install failed"
        echo -e "${RED}✗ Failed to install uv${NC}"
        echo "Please install manually: https://docs.astral.sh/uv/getting-started/installation/"
        exit 1
    fi

    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"

    if command -v uv >/dev/null 2>&1; then
        UV_VERSION=$(uv --version 2>&1)
        log "uv installed: $UV_VERSION"
        echo -e "${GREEN}✓ $UV_VERSION installed${NC}"
    else
        log "uv installed but not on PATH"
        echo -e "${YELLOW}⚠ uv installed but needs terminal restart to be on PATH${NC}"
    fi
fi

# ── Done ───────────────────────────────────────────────────────────────────────

log "Prerequisites check complete"
echo ""
echo "╔══════════════════════════════════════╗"
echo "║   ✓  Prerequisites Ready!            ║"
echo "╚══════════════════════════════════════╝"
echo ""
echo "Log saved to: $LOG_FILE"
exit 0