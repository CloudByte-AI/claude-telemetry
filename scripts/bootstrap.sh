#!/usr/bin/env bash
#
# CloudByte plugin installer - one-liner bootstrap (macOS / Linux).
#
# POSIX counterpart of scripts/bootstrap.ps1. Run on a fresh machine. This is
# the whole installation - the installer asks nothing it does not have to,
# installs every missing dependency itself, and sets up every supported editor
# it finds (Claude Code and Cursor):
#
#     curl -fsSL https://raw.githubusercontent.com/CloudByte-AI/claude-telemetry/main/scripts/bootstrap.sh | bash
#
# Options are rarely needed. Unlike the PowerShell side - where a plain `| iex`
# cannot take arguments at all and a scriptblock wrapper is required - bash can
# forward arguments directly with `-s --`:
#
#     curl -fsSL https://raw.githubusercontent.com/CloudByte-AI/claude-telemetry/main/scripts/bootstrap.sh | bash -s -- --target cursor
#
# Note that Cursor cannot install a plugin from its CLI: the installer
# registers the marketplace, prints the IDE steps (Settings > Plugins >
# cursor-telemetry > Install) and then waits for the plugin to appear.
#
# Any arguments are forwarded verbatim to install.sh.
#
# The same bootstrap also fetches the UNINSTALLER, which needs identical ref
# pinning and download-then-execute handling:
#
#     curl -fsSL https://raw.githubusercontent.com/CloudByte-AI/claude-telemetry/main/scripts/bootstrap.sh | bash -s -- --script uninstall.sh
#
# Installing from a branch other than main - note the ref appears TWICE, once
# in the URL you fetch and once as --ref. This file cannot detect which branch
# it was downloaded from, so without --ref it would pull install.sh from main:
#
#     curl -fsSL https://raw.githubusercontent.com/CloudByte-AI/claude-telemetry/development/scripts/bootstrap.sh | bash -s -- --ref development
#
# Why this file exists at all, given that `exit` in a piped bash script is
# harmless (bash runs as a child process, unlike PowerShell's iex which
# evaluates in the calling session):
#
#   1. `curl ... | bash` executes the script AS IT DOWNLOADS. A connection that
#      drops mid-transfer leaves bash having already run the first half of a
#      truncated script. Downloading install.sh to a file first and only then
#      executing it makes the whole thing all-or-nothing.
#   2. A piped script cannot know which ref it came from, so --ref is the only
#      way to keep install.sh and validate.sh on the same branch.
#   3. It gives arguments one documented place to be forwarded from.
#   4. It retries the download. Some networks reset connections to
#      raw.githubusercontent.com now and then; one reset used to abort the
#      whole install before its first step.
#
# This file is itself piped into bash, so everything that does work lives in
# main(), called on the very last line. A download cut short anywhere before
# that line leaves bash with definitions only, and nothing runs.
#
# Environment variables are an alternative to arguments:
#
#     CLOUDBYTE_INSTALL_ARGS="--open-dashboard"   # switches for install.sh
#     CLOUDBYTE_REF="development"                 # branch/tag/sha to install from
#     CLOUDBYTE_SCRIPT="uninstall.sh"             # which script to run
#     CLOUDBYTE_INSTALL_URL="http://..."          # full override of the script URL
#     CLOUDBYTE_FETCH_ATTEMPTS=4                  # download attempts (also used by install.sh/validate.sh)
#     CLOUDBYTE_FETCH_DELAY=2                     # seconds before the first retry, doubled each time
#
# The installer's own exit code becomes this script's exit code.

set -u

REPO="CloudByte-AI/claude-telemetry"

if [ -t 1 ]; then
    C_RED=$'\033[0;31m'; C_NC=$'\033[0m'
else
    C_RED=''; C_NC=''
fi

# Download $1 to $2, retrying network failures with exponential backoff.
# Kept identical in bootstrap.sh, install.sh and validate.sh: each is a
# standalone downloaded file, so there is nothing to share it from.
#
# Returns 0 on success, 22 when the server answered "not found" (a ref or
# path that does not exist - retrying cannot fix that), 1 otherwise.
#
# The loop is ours rather than curl's: `curl --retry` does not retry
# "Connection reset by peer", and `--retry-all-errors`, which does, needs
# curl 7.71+ - older curls (Ubuntu 20.04 ships 7.68) reject the whole command.
fetch() {
    local url="$1" dest="$2" attempt=1 status code
    local attempts="${CLOUDBYTE_FETCH_ATTEMPTS:-4}" delay="${CLOUDBYTE_FETCH_DELAY:-2}"
    case "$attempts" in ''|*[!0-9]*|0) attempts=4 ;; esac
    case "$delay" in ''|*[!0-9]*) delay=2 ;; esac

    while :; do
        if command -v curl >/dev/null 2>&1; then
            # -f so an HTTP error is a failure rather than a file holding
            # GitHub's error page. --connect-timeout fails a dead connection
            # fast; --speed-limit/--speed-time abort a transfer that has
            # stalled, without capping a download that is slow but moving.
            status="$(curl -fsSL --connect-timeout 15 --speed-limit 1024 --speed-time 30 \
                -w '%{http_code}' -o "$dest" "$url")"
            code=$?
            [ "$code" -eq 0 ] && return 0
            # 4xx is final, except timeouts and rate limits, which are not.
            case "$status" in
                408|429) ;;
                4[0-9][0-9]) return 22 ;;
            esac
        elif command -v wget >/dev/null 2>&1; then
            # -T is the one timeout flag GNU and BusyBox wget share. wget does
            # not report the HTTP status, so a 404 is retried like any failure.
            wget -q -T 30 -O "$dest" "$url"
            code=$?
            [ "$code" -eq 0 ] && return 0
        else
            echo "${C_RED}[FAIL]${C_NC} Neither curl nor wget is available - cannot download $url" >&2
            return 1
        fi

        [ "$attempt" -ge "$attempts" ] && return 1
        attempt=$((attempt + 1))
        echo "  Download failed - retrying in ${delay}s (attempt $attempt of $attempts)..." >&2
        sleep "$delay"
        delay=$((delay * 2))
    done
}

main() {
    local REF="" SCRIPT_NAME="" WHAT INSTALL_URL RAW_BASE DEST
    local INSTALL_ARGS=()

    # Only --ref and --script are consumed here; everything else is the target
    # script's business and is forwarded untouched. Both `--x y` and `--x=y`
    # forms are accepted.
    while [ $# -gt 0 ]; do
        case "$1" in
            --ref)
                if [ $# -lt 2 ]; then
                    echo "${C_RED}[FAIL]${C_NC} --ref needs a value (branch, tag or sha)." >&2
                    exit 1
                fi
                REF="$2"
                shift 2
                ;;
            --ref=*)
                REF="${1#--ref=}"
                shift
                ;;
            --script)
                if [ $# -lt 2 ]; then
                    echo "${C_RED}[FAIL]${C_NC} --script needs a value (install.sh or uninstall.sh)." >&2
                    exit 1
                fi
                SCRIPT_NAME="$2"
                shift 2
                ;;
            --script=*)
                SCRIPT_NAME="${1#--script=}"
                shift
                ;;
            *)
                INSTALL_ARGS+=("$1")
                shift
                ;;
        esac
    done

    if [ -z "$REF" ]; then
        REF="${CLOUDBYTE_REF:-main}"
    fi

    if [ -z "$SCRIPT_NAME" ]; then
        SCRIPT_NAME="${CLOUDBYTE_SCRIPT:-install.sh}"
    fi

    case "$SCRIPT_NAME" in
        install.sh|uninstall.sh) ;;
        *)
            echo "${C_RED}[FAIL]${C_NC} --script must be install.sh or uninstall.sh (got '$SCRIPT_NAME')." >&2
            exit 1
            ;;
    esac

    case "$SCRIPT_NAME" in
        uninstall*) WHAT="uninstaller" ;;
        *)          WHAT="installer" ;;
    esac

    if [ -n "${CLOUDBYTE_INSTALL_URL:-}" ]; then
        INSTALL_URL="$CLOUDBYTE_INSTALL_URL"
        RAW_BASE="${INSTALL_URL%/*}"
    else
        RAW_BASE="https://raw.githubusercontent.com/$REPO/$REF/scripts"
        INSTALL_URL="$RAW_BASE/$SCRIPT_NAME"
    fi

    # CLOUDBYTE_INSTALL_ARGS is deliberately word-split: it is a string of
    # switches, not a single argument. Values containing spaces are not
    # supported here, same as on the PowerShell side.
    if [ -n "${CLOUDBYTE_INSTALL_ARGS:-}" ]; then
        # shellcheck disable=SC2206
        INSTALL_ARGS+=(${CLOUDBYTE_INSTALL_ARGS})
    fi

    # No ".sh" suffix on the template: BSD mktemp, which is what macOS ships,
    # only substitutes X's at the very END of the template. The file is invoked
    # as `bash <file>`, so its name is irrelevant.
    DEST="$(mktemp "${TMPDIR:-/tmp}/cloudbyte-install-XXXXXXXX" 2>/dev/null)" || {
        DEST="${TMPDIR:-/tmp}/cloudbyte-install-$$"
    }

    # The trap runs after main() returns, so it cannot read main's locals.
    BOOTSTRAP_DEST="$DEST"
    trap 'rm -f "$BOOTSTRAP_DEST"' EXIT INT TERM

    echo ""
    echo "Fetching CloudByte $WHAT..."
    echo "  $INSTALL_URL"

    fetch "$INSTALL_URL" "$DEST"
    case $? in
        0) ;;
        22)
            echo ""
            echo "${C_RED}[FAIL]${C_NC} The $WHAT was not found at the address above." >&2
            echo "       Check that the ref '$REF' exists and contains scripts/$SCRIPT_NAME." >&2
            echo "" >&2
            exit 1
            ;;
        *)
            echo ""
            echo "${C_RED}[FAIL]${C_NC} Could not download the $WHAT - the connection to GitHub kept failing." >&2
            echo "       This is usually a temporary network problem. Wait a minute and run" >&2
            echo "       the same command again. If it keeps failing, check whether a VPN," >&2
            echo "       proxy or firewall is blocking raw.githubusercontent.com." >&2
            echo "" >&2
            exit 1
            ;;
    esac

    if [ ! -s "$DEST" ]; then
        echo ""
        echo "${C_RED}[FAIL]${C_NC} The downloaded $WHAT is empty." >&2
        echo "       Check that the ref '$REF' exists and contains scripts/$SCRIPT_NAME." >&2
        echo "" >&2
        exit 1
    fi

    # Fetch validate.sh from the same ref install.sh came from. Injected rather
    # than defaulted inside install.sh so the two halves can never come from
    # different branches. Skipped if the caller already said where to look, and
    # skipped entirely for the uninstaller, which runs no validation and would
    # reject it.
    if [ "$SCRIPT_NAME" = "install.sh" ]; then
        local NEEDS_RAW_BASE=1 arg
        for arg in ${INSTALL_ARGS[@]+"${INSTALL_ARGS[@]}"}; do
            case "$arg" in
                --raw-base|--raw-base=*) NEEDS_RAW_BASE=0 ;;
            esac
        done
        if [ "$NEEDS_RAW_BASE" -eq 1 ]; then
            INSTALL_ARGS+=(--raw-base "$RAW_BASE")
        fi
    fi

    # Invoked through `bash <file>` rather than made executable and run
    # directly: no chmod is needed, and it does not matter if /tmp is mounted
    # noexec.
    #
    # stdin is passed through untouched. Under `curl | bash` stdin is the
    # download pipe, which is exactly why install.sh reads its prompt from
    # /dev/tty instead.
    bash "$DEST" ${INSTALL_ARGS[@]+"${INSTALL_ARGS[@]}"}
    exit $?
}

# Must stay the last line - see the note at the top of this file.
main "$@"
