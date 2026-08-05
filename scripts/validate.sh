#!/usr/bin/env bash
# CloudByte Prerequisites Script (macOS / Linux)
# Ensures uv is installed, then ensures a Python 3.12 interpreter is available
# TO UV - not to the user's shell.
#
# POSIX counterpart of scripts/validate.ps1, and rewritten to match it. Python
# is provisioned through `uv python install`, which places a managed interpreter
# under uv's own data directory and touches neither PATH nor any system python.
# A pre-existing Python of any version is left exactly as it is: an older
# interpreter (3.9, say) is no longer an error, because nothing the plugin runs
# depends on the user's default `python`.
#
# This replaces the earlier version of this script, which installed a SYSTEM
# Python through the package manager and accepted anything from 3.10 up. That
# contradicted pyproject.toml (requires-python = ">=3.12") and .python-version
# (3.12): a machine with only 3.10 passed validation and then failed at
# `uv sync --python 3.12`. The package-manager path survives here only as a
# last resort for when uv cannot reach its download host.
#
# Run directly, via the installer's Step 3, or as the plugin's Setup hook - no
# plugin context required. Never prompts: the Setup hook runs it unattended.
#
# Exit codes:  0 ready   1 could not provide the prerequisites

PY_TARGET="3.12"

# ── Home directory ─────────────────────────────────────────────────────────────

if [ -n "${USERPROFILE:-}" ] && { [ "${OSTYPE:-}" = "msys" ] || [ "${OSTYPE:-}" = "cygwin" ]; }; then
    USER_HOME="${USERPROFILE:-$HOME}"
else
    USER_HOME="$HOME"
fi

CLOUDBYTE_DIR="$USER_HOME/.cloudbyte"
LOG_DIR="$CLOUDBYTE_DIR/logs"
SETUP_LOG_DIR="$CLOUDBYTE_DIR/logs/setup"

mkdir -p "$LOG_DIR" "$SETUP_LOG_DIR" 2>/dev/null || true

LOG_FILE="$SETUP_LOG_DIR/setup-$(date '+%Y-%m-%d').log"

if [ -t 1 ]; then
    C_RED=$'\033[0;31m'; C_GREEN=$'\033[0;32m'; C_YELLOW=$'\033[1;33m'; C_NC=$'\033[0m'
else
    C_RED=''; C_GREEN=''; C_YELLOW=''; C_NC=''
fi

# Prints AND appends. Deliberately not `exec > >(tee -a ...)` as the previous
# version did: a tee'd stdout is a pipe, which defeats the `[ -t 1 ]` colour and
# terminal tests above.
log() {
    local line
    line="[$(date '+%Y-%m-%d %H:%M:%S')] $*"
    printf '%s\n' "$line"
    printf '%s\n' "$line" >> "$LOG_FILE" 2>/dev/null || true
}

have() { command -v "$1" >/dev/null 2>&1; }

# ── OS detection ───────────────────────────────────────────────────────────────

case "$(uname -s 2>/dev/null || echo unknown)" in
    Darwin)                 OS="macos" ;;
    Linux)                  OS="linux" ;;
    MINGW*|MSYS*|CYGWIN*)   OS="windows" ;;
    FreeBSD|NetBSD|OpenBSD|DragonFly) OS="bsd" ;;
    *)                      OS="unknown" ;;
esac

# Windows: defer to validate.ps1. The Setup hook runs this file through bash on
# every platform, so a Git Bash session on Windows lands here - and the
# PowerShell script is the one that knows about the registry, winget and MSI.
if [ "$OS" = "windows" ]; then
    log "Windows detected - deferring to validate.ps1"
    exit 0
fi

log "=== CloudByte Prerequisites Check ==="
log "OS: $OS ($(uname -sm 2>/dev/null))"
log "Home: $USER_HOME"

printf '\n'
printf '%s\n' "======================================"
printf '%s\n' "  CloudByte Prerequisites Check"
printf '%s\n' "======================================"
printf '\n'

# An activated virtualenv would make uv resolve to that environment instead of a
# real interpreter, so probes run without it.
if [ -n "${VIRTUAL_ENV:-}" ]; then
    log "Ignoring active VIRTUAL_ENV for probes: $VIRTUAL_ENV"
    unset VIRTUAL_ENV
fi

# ── Helpers ────────────────────────────────────────────────────────────────────

NATIVE_OUT=""
NATIVE_EXIT=0
run_native() {
    NATIVE_OUT="$("$@" 2>&1)"
    NATIVE_EXIT=$?
    return 0
}

fetch() {
    if have curl; then
        curl -fsSL "$1" -o "$2"
    elif have wget; then
        wget -q -O "$2" "$1"
    else
        return 1
    fi
}

refresh_path() {
    local extra p
    extra="$HOME/.local/bin
$HOME/.cargo/bin
/opt/homebrew/bin
/usr/local/bin"

    # uv's installer writes this env file; sourcing it is how uv itself tells a
    # running shell about the new PATH entry.
    if [ -f "$HOME/.local/bin/env" ]; then
        # shellcheck disable=SC1091
        . "$HOME/.local/bin/env" >/dev/null 2>&1 || true
    fi

    while IFS= read -r p; do
        [ -n "$p" ] || continue
        [ -d "$p" ] || continue
        case ":$PATH:" in
            *":$p:"*) ;;
            *) PATH="$PATH:$p" ;;
        esac
    done <<EOF
$extra
EOF
    export PATH
}

# Where the terminal is, if any. Used only to decide whether a sudo password can
# be asked for at all - this script must never block waiting for input.
TTY_DEV=""
if [ -c /dev/tty ] && (: < /dev/tty) 2>/dev/null; then
    TTY_DEV="/dev/tty"
elif [ -t 0 ]; then
    TTY_DEV="/dev/stdin"
fi

SUDO=""
sudo_available() {
    if [ "$(id -u)" -eq 0 ]; then SUDO=""; return 0; fi
    have sudo || return 1
    # Passwordless sudo works unattended.
    if sudo -n true 2>/dev/null; then SUDO="sudo"; return 0; fi
    # Otherwise it needs a terminal to ask on. Without one, refuse rather than
    # hang - the Setup hook has a timeout, not a user.
    if [ -n "$TTY_DEV" ]; then SUDO="sudo"; return 0; fi
    return 1
}

# ── uv ─────────────────────────────────────────────────────────────────────────
# uv comes first: it is the hard requirement, and it is what provisions Python.

printf '%s\n' "-- Checking uv --------------------------"

have uv || refresh_path

if have uv; then
    UV_VERSION="$(uv --version 2>&1 | head -n 1)"
    log "uv found: $UV_VERSION"
    printf '%s\n' "${C_GREEN}[OK]${C_NC} $UV_VERSION"
else
    log "uv not found - installing..."
    printf '%s\n' "${C_YELLOW}uv not found - installing...${C_NC}"

    # Downloaded and run as a FILE rather than `curl ... | sh`. A piped
    # installer executes while it downloads, so a dropped connection can leave
    # half of it already run; running the file is all-or-nothing. It also keeps
    # the installer's stdin free of our own script text.
    # No ".sh" suffix: BSD mktemp (macOS) only substitutes trailing X's.
    UV_INSTALLER="$(mktemp "${TMPDIR:-/tmp}/uv-install-XXXXXXXX" 2>/dev/null)" \
        || UV_INSTALLER="${TMPDIR:-/tmp}/uv-install-$$"

    if fetch "https://astral.sh/uv/install.sh" "$UV_INSTALLER" && [ -s "$UV_INSTALLER" ]; then
        sh "$UV_INSTALLER" < /dev/null
        log "uv installer exit: $?"
    else
        log "Could not download the uv installer"
    fi
    rm -f "$UV_INSTALLER"

    refresh_path

    if have uv; then
        UV_VERSION="$(uv --version 2>&1 | head -n 1)"
        log "uv installed: $UV_VERSION"
        printf '%s\n' "${C_GREEN}[OK]${C_NC} $UV_VERSION installed"
    else
        log "uv installed but not on PATH"
        printf '%s\n' "${C_RED}[FAIL]${C_NC} uv installed but is not on PATH yet"
        printf '%s\n' "Open a new terminal and re-run, or install manually:"
        printf '%s\n' "  https://docs.astral.sh/uv/getting-started/installation/"
        exit 1
    fi
fi

# ── Python ─────────────────────────────────────────────────────────────────────

printf '\n'
printf '%s\n' "-- Checking Python $PY_TARGET ---------------"

# Report whatever the user's shell currently resolves, purely so the log shows it
# was seen and deliberately left alone.
SYSTEM_PYTHON=""
for cmd in python3 python; do
    have "$cmd" || continue
    run_native "$cmd" --version
    case "$NATIVE_OUT" in
        Python\ [0-9]*) SYSTEM_PYTHON="$(printf '%s' "$NATIVE_OUT" | head -n 1)"; break ;;
    esac
done
if [ -n "$SYSTEM_PYTHON" ]; then
    log "System python on PATH: $SYSTEM_PYTHON (left untouched)"
    printf '%s\n' "     Your default python is ${SYSTEM_PYTHON#Python } - it will not be changed"
fi

PYTHON_PATH=""

find_python_target() {
    # --no-project  so a pyproject.toml in the working directory cannot redirect
    #               the answer to an unrelated project environment
    # --system      so a .venv in the working directory is not mistaken for an
    #               interpreter; a venv cannot be used as a base for building the
    #               plugin's own environment. uv-managed installs are still
    #               included, only virtualenvs are excluded.
    PYTHON_PATH=""
    run_native uv python find "$PY_TARGET" --no-project --system
    if [ "$NATIVE_EXIT" -eq 0 ]; then
        local p
        p="$(printf '%s' "$NATIVE_OUT" | head -n 1)"
        if [ -n "$p" ] && [ -e "$p" ]; then
            PYTHON_PATH="$p"
            return 0
        fi
    fi
    return 1
}

install_python_via_uv() {
    log "Installing managed Python $PY_TARGET via uv..."
    printf '%s\n' "Python $PY_TARGET not available - downloading a managed copy (~30MB)..."
    printf '%s\n' "  (installs under uv's own data directory - PATH is untouched)"
    printf '\n'

    # --no-bin: do not add even a versioned python3.12 shim to ~/.local/bin. uv
    #   discovers its own managed installs without one, so the interpreter stays
    #   completely invisible to the user's shell. (--default, which WOULD take
    #   over bare `python`, is never passed.)
    uv python install "$PY_TARGET" --no-bin < /dev/null
    local code=$?

    # Older uv builds predate --no-bin and reject it outright. Retry without it
    # rather than fail: a versioned shim is a cosmetic difference, no interpreter
    # at all is not. `--default` is still never passed either way.
    if [ "$code" -ne 0 ]; then
        log "uv python install --no-bin exit: $code - retrying without --no-bin"
        uv python install "$PY_TARGET" < /dev/null
        code=$?
    fi

    log "uv python install exit: $code"
    return $code
}

# ── Last-resort system install ─────────────────────────────────────────────────
# Only reached when uv cannot provision Python at all (blocked download host,
# air-gapped mirror). uv discovers system interpreters, so a real package
# install still helps - but it is deliberately last, it needs root on Linux, and
# it can change which python the user's shell resolves.

install_python_via_package_manager() {
    printf '\n'
    printf '%s\n' "${C_YELLOW}[WARN]${C_NC} Falling back to the system package manager."
    printf '%s\n' "       Unlike the uv-managed interpreter, this installs Python system-wide"
    printf '%s\n' "       and may change which python your shell resolves."
    printf '\n'

    if [ "$OS" = "macos" ]; then
        if ! have brew; then
            log "Homebrew not found - cannot install Python without it"
            printf '%s\n' "Homebrew is not installed - install it first: https://brew.sh"
            return 1
        fi
        log "Installing Python $PY_TARGET via brew..."
        brew install "python@$PY_TARGET" < /dev/null
        return $?
    fi

    if ! sudo_available; then
        log "No usable sudo - cannot install a system Python unattended"
        printf '%s\n' "Root access is required for a system Python install, and none is available."
        printf '%s\n' "Install it yourself with:  uv python install $PY_TARGET"
        return 1
    fi

    if have apt-get; then
        log "Installing Python $PY_TARGET via apt..."
        $SUDO apt-get update -y < /dev/null
        # python3.12 is not in every release's default archive; if it is missing,
        # the error below is more useful than a silent skip.
        $SUDO apt-get install -y "python$PY_TARGET" "python$PY_TARGET-venv" < /dev/null
        return $?
    elif have dnf; then
        log "Installing Python $PY_TARGET via dnf..."
        $SUDO dnf install -y "python$PY_TARGET" < /dev/null
        return $?
    elif have zypper; then
        log "Installing Python $PY_TARGET via zypper..."
        $SUDO zypper --non-interactive install "python312" < /dev/null
        return $?
    elif have pacman; then
        # Arch's `python` tracks the newest release, which may be past 3.12. This
        # is attempted anyway because the alternative is nothing at all; the
        # re-check below is what decides whether it actually helped.
        log "Installing Python via pacman (rolling - may not be $PY_TARGET)..."
        $SUDO pacman -S --noconfirm python < /dev/null
        return $?
    elif have apk; then
        log "Installing Python via apk..."
        $SUDO apk add --no-cache python3 < /dev/null
        return $?
    fi

    log "No supported package manager found"
    printf '%s\n' "No supported package manager found (tried apt, dnf, zypper, pacman, apk)."
    return 1
}

# ── Ensure a 3.12 interpreter exists for uv ────────────────────────────────────

if find_python_target; then
    log "Python $PY_TARGET already available at: $PYTHON_PATH"
    printf '%s\n' "${C_GREEN}[OK]${C_NC} Python $PY_TARGET available"
    printf '%s\n' "     $PYTHON_PATH"
else
    log "No Python $PY_TARGET found - provisioning"

    uv_install_ok=0
    install_python_via_uv && uv_install_ok=1

    if [ "$uv_install_ok" -eq 1 ]; then
        find_python_target || true
    fi

    # uv said it installed Python but cannot then see it. Installing a system
    # Python would not help - uv is the thing that has to find it - and would
    # modify the machine for nothing, so stop here with a real diagnostic.
    if [ "$uv_install_ok" -eq 1 ] && [ -z "$PYTHON_PATH" ]; then
        log "uv python install reported success but $PY_TARGET is not discoverable - uv looks broken"
        printf '\n'
        printf '%s\n' "${C_RED}[FAIL]${C_NC} uv installed Python $PY_TARGET but cannot find it afterwards."
        printf '%s\n' "       This usually means the uv installation itself is damaged."
        printf '\n'
        printf '%s\n' "  Check what uv reports:"
        printf '%s\n' "    uv --version"
        printf '%s\n' "    uv python list"
        printf '\n'
        printf '%s\n' "  Reinstalling uv normally fixes it:"
        printf '%s\n' "    curl -LsSf https://astral.sh/uv/install.sh | sh"
        printf '\n'
        exit 1
    fi

    # uv genuinely could not download (blocked host, offline mirror). A real
    # system install still helps, because uv discovers system interpreters.
    if [ -z "$PYTHON_PATH" ]; then
        log "uv could not download Python - falling back to a system install"
        printf '\n'
        printf '%s\n' "${C_YELLOW}[WARN]${C_NC} uv could not download Python $PY_TARGET - trying a system install..."

        if install_python_via_package_manager; then
            refresh_path
            find_python_target || true
        fi
    fi

    if [ -z "$PYTHON_PATH" ]; then
        log "All Python provisioning methods failed"
        printf '\n'
        printf '%s\n' "${C_RED}[FAIL]${C_NC} Could not provide Python $PY_TARGET"
        printf '%s\n' "Install it manually with either:"
        printf '%s\n' "  uv python install $PY_TARGET"
        printf '%s\n' "  https://www.python.org/downloads/"
        printf '%s\n' "Then re-run the installer."
        exit 1
    fi

    log "Python $PY_TARGET ready at: $PYTHON_PATH"
    printf '%s\n' "${C_GREEN}[OK]${C_NC} Python $PY_TARGET ready"
    printf '%s\n' "     $PYTHON_PATH"
fi

# ── Done ───────────────────────────────────────────────────────────────────────

log "Prerequisites check complete"
printf '\n'
printf '%s\n' "======================================"
printf '%s\n' "  [OK] Prerequisites Ready!"
printf '%s\n' "======================================"
printf '\n'
printf '%s\n' "Log saved to: $LOG_FILE"
exit 0
