#!/usr/bin/env bash
#
# CloudByte telemetry plugin installer for macOS and Linux - Claude Code and
# Cursor. POSIX counterpart of scripts/install.ps1, with the same seven steps,
# the same exit codes and the same behavioural guarantees.
#
# DESCRIPTION
#   Installs the telemetry plugin into the supported editors found on the
#   machine. Dependencies are handled automatically - anything missing is
#   installed, anything in the way is reported clearly, and there is never a
#   "do it yourself" branch.
#
#   The one question it asks is which editors to install into. Both supported
#   editors are always offered, even on a machine with neither installed, since
#   either CLI can be provisioned here - what is already present only decides
#   which entries Enter accepts. Everything else is unattended. --target skips
#   the question, and it is skipped automatically when there is no terminal to
#   ask on, so a piped or CI install cannot stall.
#
#     Step 1  - Detect editors, ask which to use, install any missing CLI
#     Step 2  - Ensure uv (installed by Step 3 if missing)
#     Step 3  - Run prerequisites validation (scripts/validate.sh), which
#               installs uv and provisions Python 3.12 through it
#     Step 4  - Add the marketplace to each editor
#     Step 5  - Install the plugin
#     Step 6  - Prepare each plugin environment (uv sync)
#     Step 7  - Print activation instructions and the summary
#
#   Editor differences that the script has to work around:
#
#     Claude Code  CLI is `claude`. The plugin installs from the CLI, so Step 5
#                  is automatic. Cache directories are named by version
#                  (0.1.41), so the newest is chosen by version.
#
#     Cursor       CLI is `cursor-agent`, a separate download from the IDE, so
#                  Step 1 installs it when Cursor is present but its CLI is not.
#                  Only the marketplace can be added from the CLI - the plugin
#                  itself must be added from the IDE (Cursor Settings >
#                  Customize > Browse Marketplace > cursor-telemetry > Add),
#                  so Step 5 links the visual guide, prints the same steps as
#                  text, asks you to confirm once done, then verifies. Cache
#                  directories are named by commit sha, which cannot be
#                  ordered, so the newest is chosen by modification time.
#
#   Differences from the PowerShell version, all of them deliberate:
#
#     * No ExecutionPolicy handling - nothing equivalent exists here. Scripts
#       are invoked as `bash <file>` so no chmod is needed either.
#     * No process-killing to release file locks. Windows holds mandatory locks
#       on a .venv that a running MCP server has open, which is why install.ps1
#       has to stop those processes before `uv sync` can rebuild. POSIX unlinks
#       happily while a file is open, so the whole subsystem is unnecessary.
#       What DOES break a rebuild here is ownership - a cache directory left
#       root-owned by an earlier `sudo` run - so that is detected and explained
#       instead.
#     * The selection prompt reads /dev/tty, not stdin. Under `curl | bash`
#       stdin is the download pipe, so reading it would consume the script.
#       PowerShell has the opposite situation: `irm | iex` leaves stdin alone.
#
#   It only exits non-zero when an automatic install genuinely fails, and then
#   it prints the manual command to run.
#
#   Plugin activation (/reload-plugins or a session restart) cannot be
#   automated and remains a manual step in both editors.
#
# OPTIONS
#   --yes, -y               Do not ask which editors to use - take every one
#                           that was detected. Dependency installation is
#                           automatic either way.
#   --non-interactive       Same as --yes. Both exist so unattended runs and CI
#                           cannot block on the selection prompt.
#   --skip-prereqs          Skip Step 3 (validate.sh). Ignored when uv is
#                           missing, since Step 3 is what installs it.
#   --target <t>            Which editors to install into: auto (default), ask,
#                           claude, cursor, or both.
#                             auto    every editor found on the machine, minus
#                                     anything deselected at the prompt. For
#                                     Cursor "found" means the IDE, not just its
#                                     CLI, since the CLI is installed
#                                     automatically when missing. On a machine
#                                     with neither editor, the Claude Code CLI
#                                     is installed.
#                             ask     always show the selection prompt, even for
#                                     a single editor.
#                             claude | cursor | both
#                                     an explicit choice - the prompt is skipped
#                                     entirely.
#   --cursor-grace-seconds N
#                           Step 5 does not wait on a timer - it asks you to
#                           confirm the manual Cursor step and then verifies it.
#                           This is only the short grace window it keeps
#                           re-checking for after each confirmation, because
#                           clicking Install starts a download that takes a
#                           moment to land. Default 20. Not reaching the plugin
#                           is not an error - it builds its own environment on
#                           first use. (--cursor-wait-seconds is an alias.)
#   --cursor-dir <dir>      Cursor's data directory. Defaults to ~/.cursor.
#   --cursor-cli-install-url <url>
#                           Where the cursor-agent installer is fetched from.
#                           Override for an internal mirror, or to test the
#                           install path without hitting cursor.com.
#   --claude-cli-install-url <url>
#                           Where the Claude Code installer is fetched from.
#                           Same purpose as --cursor-cli-install-url.
#   --use-local-validate    Use scripts/validate.sh from this checkout instead
#                           of downloading it. Automatic when the file exists
#                           next to this script.
#   --open-dashboard        Open the dashboard URL in the default browser at the
#                           end.
#   --marketplace-url <url> Marketplace repository URL. Defaults to the
#                           CloudByte-AI repo.
#   --raw-base <url>        Base raw.githubusercontent URL that validate.sh is
#                           fetched from. Point this at another branch to test
#                           changes before they land on main. bootstrap.sh sets
#                           this automatically.
#   --plugin-ref <ref>      Plugin reference in <plugin>@<marketplace> form.
#   --dashboard-url <url>   Dashboard URL printed in the summary.
#   --plugin-guide-url <url>
#                           Visual (screenshot) walkthrough of Cursor's plugin
#                           install, linked from Step 5. Only mentioned when it
#                           is a real http(s) URL, so blanking it falls back to
#                           the text steps alone. PLUGIN_GUIDE_PAGE (default 6)
#                           and PLUGIN_GUIDE_SECTION set which part of that
#                           guide Step 5 sends the reader to.
#   --help, -h              Print this option list.
#
#   Every option also accepts the --name=value form.
#
# EXAMPLES
#   bash ./scripts/install.sh
#   curl -fsSL https://raw.githubusercontent.com/CloudByte-AI/claude-telemetry/main/scripts/install.sh | bash
#
# EXIT CODES
#   0  success
#   1  unexpected failure
#   2  no usable editor CLI for the requested target, and the automatic install
#      of the missing CLI failed
#   3  reserved (uv failures are reported by validation as code 4)
#   4  prerequisites validation failed
#   5  marketplace add failed for every targeted editor
#   6  plugin install failed for every targeted editor

set -u

# ── Defaults ───────────────────────────────────────────────────────────────────

OPT_YES=0
OPT_NONINTERACTIVE=0
OPT_SKIP_PREREQS=0
OPT_USE_LOCAL_VALIDATE=0
OPT_OPEN_DASHBOARD=0

TARGET="auto"

# The screenshot walkthrough of Cursor's plugin install, offered ahead of the
# text steps in Step 5. Step 5 only mentions it when this looks like an http(s)
# URL, so blanking it falls back cleanly to the text steps alone.
PLUGIN_GUIDE_URL="${PLUGIN_GUIDE_URL:-https://drive.google.com/file/d/11rjIUmtutXRHzGgtogwSPUPEKwrkQ5Td}"

# Where in that guide the manual plugin step begins. Step 5 points readers
# straight there instead of at page 1, since the sign-in pages ahead of it are
# already behind them by then. Keep both in step with the guide's own contents
# page if it is ever re-cut.
PLUGIN_GUIDE_PAGE="${PLUGIN_GUIDE_PAGE:-6}"
PLUGIN_GUIDE_SECTION="${PLUGIN_GUIDE_SECTION:-Add the Plugin in Cursor}"
TARGET_GIVEN=0
CURSOR_GRACE_SECONDS=20
CURSOR_DIR_OPT=""

CURSOR_CLI_INSTALL_URL="https://cursor.com/install"
CLAUDE_CLI_INSTALL_URL="https://claude.ai/install.sh"

MARKETPLACE_URL="https://github.com/CloudByte-AI/claude-telemetry"
PLUGIN_REF="claude-telemetry@claude-telemetry"
DASHBOARD_URL="http://localhost:4723"
RAW_BASE_OPT="https://raw.githubusercontent.com/CloudByte-AI/claude-telemetry/main/scripts"

usage() {
    # $0 is "bash" (and unreadable) when this file was piped in, so fall back to
    # naming the options rather than printing nothing.
    if [ -r "$0" ]; then
        sed -n '/^# CloudByte telemetry plugin installer/,/^#   6  plugin install failed/p' "$0" \
            | sed 's/^#\{1,\} \{0,1\}//'
        return 0
    fi
    cat <<'EOF'
CloudByte telemetry plugin installer for macOS and Linux.

  --yes | --non-interactive     never ask which editors to use
  --skip-prereqs                skip validate.sh (ignored when uv is missing)
  --target auto|ask|claude|cursor|both
  --cursor-grace-seconds N      grace window re-checked after you confirm
  --cursor-dir DIR              Cursor data directory (default ~/.cursor)
  --claude-cli-install-url URL  override the Claude Code installer source
  --cursor-cli-install-url URL  override the cursor-agent installer source
  --use-local-validate          use validate.sh from this checkout
  --open-dashboard              open the dashboard when finished
  --marketplace-url URL         marketplace repository
  --plugin-ref REF              <plugin>@<marketplace>
  --dashboard-url URL           dashboard URL shown in the summary
  --raw-base URL                where validate.sh is fetched from
  --plugin-guide-url URL        visual guide linked from Cursor's manual step
                                (PLUGIN_GUIDE_PAGE / PLUGIN_GUIDE_SECTION set
                                 which part of it Step 5 points at)

Full documentation is in the comment header of scripts/install.sh.
EOF
}

die_usage() {
    printf '%s\n' "[FAIL] $1" >&2
    printf '%s\n' "       Run with --help for the option list." >&2
    exit 1
}

need_value() {
    # $1 = flag name, $2 = how many args are left after the flag
    [ "$2" -ge 1 ] || die_usage "$1 needs a value."
}

while [ $# -gt 0 ]; do
    case "$1" in
        --yes|-y)               OPT_YES=1; shift ;;
        --non-interactive)      OPT_NONINTERACTIVE=1; shift ;;
        --skip-prereqs)         OPT_SKIP_PREREQS=1; shift ;;
        --use-local-validate)   OPT_USE_LOCAL_VALIDATE=1; shift ;;
        --open-dashboard)       OPT_OPEN_DASHBOARD=1; shift ;;
        --help|-h)              usage; exit 0 ;;

        --target)               need_value "$1" $(($# - 1)); TARGET="$2"; TARGET_GIVEN=1; shift 2 ;;
        --target=*)             TARGET="${1#--target=}"; TARGET_GIVEN=1; shift ;;
        --plugin-guide-url)     need_value "$1" $(($# - 1)); PLUGIN_GUIDE_URL="$2"; shift 2 ;;
        --plugin-guide-url=*)   PLUGIN_GUIDE_URL="${1#--plugin-guide-url=}"; shift ;;

        # --cursor-wait-seconds is the pre-confirmation-flow name, kept as a
        # silent alias so anything already scripting it does not break.
        --cursor-grace-seconds|--cursor-wait-seconds)
                                need_value "$1" $(($# - 1)); CURSOR_GRACE_SECONDS="$2"; shift 2 ;;
        --cursor-grace-seconds=*) CURSOR_GRACE_SECONDS="${1#--cursor-grace-seconds=}"; shift ;;
        --cursor-wait-seconds=*) CURSOR_GRACE_SECONDS="${1#--cursor-wait-seconds=}"; shift ;;

        --cursor-dir)           need_value "$1" $(($# - 1)); CURSOR_DIR_OPT="$2"; shift 2 ;;
        --cursor-dir=*)         CURSOR_DIR_OPT="${1#--cursor-dir=}"; shift ;;

        --cursor-cli-install-url)   need_value "$1" $(($# - 1)); CURSOR_CLI_INSTALL_URL="$2"; shift 2 ;;
        --cursor-cli-install-url=*) CURSOR_CLI_INSTALL_URL="${1#--cursor-cli-install-url=}"; shift ;;

        --claude-cli-install-url)   need_value "$1" $(($# - 1)); CLAUDE_CLI_INSTALL_URL="$2"; shift 2 ;;
        --claude-cli-install-url=*) CLAUDE_CLI_INSTALL_URL="${1#--claude-cli-install-url=}"; shift ;;

        --marketplace-url)      need_value "$1" $(($# - 1)); MARKETPLACE_URL="$2"; shift 2 ;;
        --marketplace-url=*)    MARKETPLACE_URL="${1#--marketplace-url=}"; shift ;;

        --plugin-ref)           need_value "$1" $(($# - 1)); PLUGIN_REF="$2"; shift 2 ;;
        --plugin-ref=*)         PLUGIN_REF="${1#--plugin-ref=}"; shift ;;

        --dashboard-url)        need_value "$1" $(($# - 1)); DASHBOARD_URL="$2"; shift 2 ;;
        --dashboard-url=*)      DASHBOARD_URL="${1#--dashboard-url=}"; shift ;;

        --raw-base)             need_value "$1" $(($# - 1)); RAW_BASE_OPT="$2"; shift 2 ;;
        --raw-base=*)           RAW_BASE_OPT="${1#--raw-base=}"; shift ;;

        # Silently tolerated so a stray token forwarded by bootstrap.sh (or a
        # trailing "." copied out of a README) cannot abort the install.
        -*)                     say_unknown="$1"; shift
                                printf '%s\n' "[WARN] Ignoring unknown option: $say_unknown" ;;
        *)                      shift ;;
    esac
done

case "$TARGET" in
    auto|ask|claude|cursor|both) ;;
    *) die_usage "--target must be one of: auto, ask, claude, cursor, both (got '$TARGET')." ;;
esac

case "$CURSOR_GRACE_SECONDS" in
    ''|*[!0-9]*) die_usage "--cursor-grace-seconds must be a whole number of seconds." ;;
esac

# ── OS detection ───────────────────────────────────────────────────────────────

case "$(uname -s 2>/dev/null || echo unknown)" in
    Darwin)                 OS="macos" ;;
    Linux)                  OS="linux" ;;
    MINGW*|MSYS*|CYGWIN*)   OS="windows" ;;
    FreeBSD|NetBSD|OpenBSD|DragonFly) OS="bsd" ;;
    *)                      OS="unknown" ;;
esac

if [ "$OS" = "windows" ]; then
    printf '\n%s\n' "[FAIL] This is the macOS/Linux installer, but Windows was detected." >&2
    printf '%s\n' "       Use the PowerShell installer instead, from a PowerShell prompt:" >&2
    printf '%s\n' "         irm https://raw.githubusercontent.com/CloudByte-AI/claude-telemetry/main/scripts/bootstrap.ps1 | iex" >&2
    printf '\n' >&2
    exit 1
fi

# ── Paths and logging ──────────────────────────────────────────────────────────

USER_HOME="$HOME"
CLOUDBYTE_DIR="$USER_HOME/.cloudbyte"
SETUP_LOG_DIR="$CLOUDBYTE_DIR/logs/setup"
RAW_BASE="${RAW_BASE_OPT%/}"

if [ -n "${CLAUDE_CONFIG_DIR:-}" ]; then
    CLAUDE_DIR="$CLAUDE_CONFIG_DIR"
else
    CLAUDE_DIR="$USER_HOME/.claude"
fi

if [ -n "$CURSOR_DIR_OPT" ]; then
    CURSOR_DIR="$CURSOR_DIR_OPT"
elif [ -n "${CURSOR_DIR:-}" ]; then
    CURSOR_DIR="$CURSOR_DIR"
else
    CURSOR_DIR="$USER_HOME/.cursor"
fi

mkdir -p "$SETUP_LOG_DIR" 2>/dev/null || true
LOG_FILE="$SETUP_LOG_DIR/install-$(date '+%Y-%m-%d').log"

if [ -t 1 ]; then
    C_RED=$'\033[0;31m'; C_GREEN=$'\033[0;32m'; C_YELLOW=$'\033[1;33m'
    C_CYAN=$'\033[0;36m'; C_NC=$'\033[0m'
else
    C_RED=''; C_GREEN=''; C_YELLOW=''; C_CYAN=''; C_NC=''
fi

log() {
    # Appended rather than wired up with `exec > >(tee)`: a tee'd stdout is a
    # pipe, which would defeat the `[ -t 1 ]` colour test above and confuse the
    # prompt. This mirrors how install.ps1 logs.
    printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >> "$LOG_FILE" 2>/dev/null || true
}

say()      { printf '%s\n' "$*";                          log "$*"; }
say_ok()   { printf '%s\n' "${C_GREEN}[OK]${C_NC} $*";    log "OK: $*"; }
say_warn() { printf '%s\n' "${C_YELLOW}[WARN]${C_NC} $*"; log "WARN: $*"; }
say_fail() { printf '%s\n' "${C_RED}[FAIL]${C_NC} $*";    log "FAIL: $*"; }

header() {
    local title="$1" pad="" n
    n=$((46 - ${#title}))
    [ "$n" -lt 3 ] && n=3
    while [ "$n" -gt 0 ]; do pad="$pad-"; n=$((n - 1)); done
    printf '\n%s\n\n' "${C_CYAN}-- $title $pad${C_NC}"
    log "=== $title ==="
}

# A third argument of "expected" marks a stop that is waiting on the user rather
# than a defect: the log path and the issue tracker are noise when the
# instructions to finish are already on screen, and pointing at "report a bug"
# for a missing login sends people to the wrong place.
fail_exit() {
    if [ "${3:-}" = "expected" ]; then
        # No leading blank: the help text above already ends with one, and a
        # second only pushes the closing line further from what it refers to.
        printf '%s\n' "  $1"
        log "STOP (expected): $1"
    else
        printf '\n'
        say_fail "$1"
        printf '%s\n' "  Full logs      : $SETUP_LOG_DIR"
        printf '%s\n' "  Troubleshooting: https://github.com/CloudByte-AI/claude-telemetry/issues"
    fi
    printf '\n'
    exit "$2"
}

# ── Helpers ────────────────────────────────────────────────────────────────────

have() { command -v "$1" >/dev/null 2>&1; }

fetch() {
    if have curl; then
        curl -fsSL "$1" -o "$2"
    elif have wget; then
        wget -q -O "$2" "$1"
    else
        return 1
    fi
}

# GNU stat and BSD stat disagree on every flag, and macOS ships the BSD one.
mtime_of() {
    stat -c %Y "$1" 2>/dev/null || stat -f %m "$1" 2>/dev/null || printf '0'
}

refresh_path() {
    # POSIX has no machine/user PATH registry to re-read, so the equivalent is
    # to add the locations vendor installers actually write to. Each is a
    # per-user directory that needs no logout to become usable - the only reason
    # `claude` or `uv` is missing right after an install is that the rc file
    # which would add it is not re-read by an already-running shell.
    local extra p npmbin
    extra="$HOME/.local/bin
$HOME/.cargo/bin
$HOME/.bun/bin
/opt/homebrew/bin
/usr/local/bin"

    # uv's installer writes this env file; sourcing it is how uv itself tells a
    # running shell about the new PATH entry.
    if [ -f "$HOME/.local/bin/env" ]; then
        # shellcheck disable=SC1091
        . "$HOME/.local/bin/env" >/dev/null 2>&1 || true
    fi

    if have npm; then
        npmbin="$(npm prefix -g 2>/dev/null)/bin"
        [ -d "$npmbin" ] && extra="$extra
$npmbin"
    fi

    # Appended, not prepended: the caller's own PATH order is theirs to decide,
    # and we only need these directories to be reachable at all.
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

python_present() {
    local cmd ver
    for cmd in python3 python; do
        have "$cmd" || continue
        ver="$("$cmd" --version 2>&1)"
        case "$ver" in
            Python\ [0-9]*) return 0 ;;
        esac
    done
    return 1
}

# Run a native command and capture stdout+stderr as plain text.
# Sets NATIVE_OUT and NATIVE_EXIT - bash cannot return a string, so these are
# the equivalent of install.ps1's $script:LastNativeExit.
NATIVE_OUT=""
NATIVE_EXIT=0
run_native() {
    log "RUN: $*"
    NATIVE_OUT="$("$@" 2>&1)"
    NATIVE_EXIT=$?
    log "EXIT $NATIVE_EXIT :: $NATIVE_OUT"
    return 0
}

# Where the terminal is, if there is one.
#
# Under `curl ... | bash` stdin IS the download pipe, so reading it would
# consume the rest of the script. /dev/tty is the controlling terminal no matter
# what stdin was redirected to, which is what lets the selection prompt work in
# a piped install. This is the single biggest difference from install.ps1, where
# the script text arrives over HTTP and stdin is never touched.
TTY_DEV=""
detect_tty() {
    if [ "$OPT_YES" -eq 1 ] || [ "$OPT_NONINTERACTIVE" -eq 1 ]; then return 0; fi
    if [ -n "${CI:-}" ]; then return 0; fi
    if [ -c /dev/tty ] && (: < /dev/tty) 2>/dev/null; then
        TTY_DEV="/dev/tty"
    elif [ -t 0 ]; then
        TTY_DEV="/dev/stdin"
    fi
    return 0
}

can_prompt() { [ -n "$TTY_DEV" ]; }

# Editor records. bash 3.2 (which is what macOS ships) has no associative
# arrays, so fields live in ED_<key>_<field> variables reached through these two
# accessors. Keys and field names are literals from this script, never input.
ed_set() { eval "ED_${1}_${2}=\"\$3\""; }
ed_get() { eval "printf '%s' \"\${ED_${1}_${2}:-}\""; }

# ── Editor selection prompt ────────────────────────────────────────────────────

# Ask which editors should get the plugin.
#
# Every supported editor is listed, not just the ones already on the machine -
# both CLIs can be installed automatically, so "not detected" is not the same as
# "not available", and a fresh machine would otherwise never get a choice.
# Detection decides the DEFAULT (what Enter selects), never the menu.
#
# Always leaves SELECTED_KEYS non-empty: an unusable answer falls back to the
# default rather than leaving the user with nothing installed.
SELECTED_KEYS=""
select_editors() {
    local default_keys="$1"
    local i n count raw tok picked keys bad tries
    local default_label=""

    count=${#CAND_KEYS[@]}

    i=0
    while [ "$i" -lt "$count" ]; do
        case " $default_keys " in
            *" ${CAND_KEYS[$i]} "*)
                if [ -z "$default_label" ]; then default_label="${CAND_NAMES[$i]}"
                else default_label="$default_label, ${CAND_NAMES[$i]}"; fi
                ;;
        esac
        i=$((i + 1))
    done
    [ -n "$default_label" ] || default_label="none"

    printf '\n'
    printf '%s\n' "  Install the plugin for which editors?"
    printf '\n'
    i=0
    while [ "$i" -lt "$count" ]; do
        local mark=" "
        case " $default_keys " in
            *" ${CAND_KEYS[$i]} "*) mark="*" ;;
        esac
        printf '   %s %d) %-12s  %s\n' "$mark" "$((i + 1))" "${CAND_NAMES[$i]}" "${CAND_NOTES[$i]}"
        i=$((i + 1))
    done
    printf '\n'
    printf '%s\n' "  Enter numbers separated by commas (for example: 1,2),"
    printf '%s\n' "  'a' for all, or press Enter for the default (*): $default_label"
    printf '\n'

    tries=0
    while [ "$tries" -lt 3 ]; do
        tries=$((tries + 1))
        raw=""
        printf '  Selection: '
        if ! IFS= read -r raw < "$TTY_DEV"; then
            printf '\n'
            say_warn "No input available - using the default."
            SELECTED_KEYS="$default_keys"
            return 0
        fi

        # Trim surrounding whitespace and any stray CR from a pasted line.
        raw="$(printf '%s' "$raw" | tr -d '\r' | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')"

        if [ -z "$raw" ]; then
            SELECTED_KEYS="$default_keys"
            return 0
        fi

        case "$(printf '%s' "$raw" | tr '[:upper:]' '[:lower:]')" in
            a|all)
                SELECTED_KEYS=""
                i=0
                while [ "$i" -lt "$count" ]; do
                    SELECTED_KEYS="$SELECTED_KEYS ${CAND_KEYS[$i]}"
                    i=$((i + 1))
                done
                SELECTED_KEYS="${SELECTED_KEYS# }"
                return 0
                ;;
        esac

        # Dedupe by key so "1,1" is one editor, not two passes over the same one.
        keys=""
        picked=0
        bad=0
        for tok in $(printf '%s' "$raw" | tr ',' ' '); do
            case "$tok" in
                ''|*[!0-9]*) bad=1; break ;;
            esac
            # 10# forces base 10 - bash reads a leading zero as octal, so a
            # typed "08" would otherwise abort with "value too great for base".
            n=$((10#$tok))
            if [ "$n" -lt 1 ] || [ "$n" -gt "$count" ]; then bad=1; break; fi
            local k="${CAND_KEYS[$((n - 1))]}"
            case " $keys " in
                *" $k "*) ;;
                *) keys="$keys $k"; picked=$((picked + 1)) ;;
            esac
        done

        if [ "$bad" -eq 0 ] && [ "$picked" -gt 0 ]; then
            SELECTED_KEYS="${keys# }"
            return 0
        fi
        printf '%s\n' "  Not a valid selection - use numbers between 1 and $count."
    done

    say_warn "No valid selection after 3 attempts - using the default."
    SELECTED_KEYS="$default_keys"
    return 0
}

# ── Vendor installers ──────────────────────────────────────────────────────────

# Fetch a vendor installer and run it as a FILE.
#
# Deliberately NOT `curl <url> | bash`. A piped installer executes while it
# downloads, so a dropped connection can leave half a script already run - the
# same reason bootstrap.sh writes install.sh to disk first. Running the file is
# all-or-nothing.
#
# stdin comes from the terminal when there is one: `claude install` puts up a
# TUI, and under `curl | bash` bare stdin is the download pipe rather than the
# user. Without this, the vendor installer would read the tail of our own
# script as its input.
run_child_installer() {
    local url="$1" tmp code
    # No ".sh" suffix on the template: BSD mktemp, which is what macOS ships,
    # only substitutes X's at the very END of the template. The file is invoked
    # as `bash <file>`, so its name is irrelevant.
    tmp="$(mktemp "${TMPDIR:-/tmp}/cloudbyte-vendor-XXXXXXXX" 2>/dev/null)" \
        || tmp="${TMPDIR:-/tmp}/cloudbyte-vendor-$$"

    log "Fetching vendor installer: $url"
    if ! fetch "$url" "$tmp"; then
        say_warn "Could not download the installer from $url"
        log "Vendor installer download failed: $url"
        rm -f "$tmp"
        return 1
    fi
    if [ ! -s "$tmp" ]; then
        say_warn "The installer downloaded from $url is empty"
        rm -f "$tmp"
        return 1
    fi

    bash "$tmp" < "${TTY_DEV:-/dev/null}"
    code=$?
    log "Child installer exit: $code"
    rm -f "$tmp"
    return $code
}

find_claude_bin() {
    local c
    for c in \
        "$HOME/.local/bin/claude" \
        "$HOME/.claude/local/claude" \
        "$HOME/.claude/bin/claude" \
        "/opt/homebrew/bin/claude" \
        "/usr/local/bin/claude"
    do
        if [ -x "$c" ]; then printf '%s' "$c"; return 0; fi
    done
    if have npm; then
        c="$(npm prefix -g 2>/dev/null)/bin/claude"
        if [ -x "$c" ]; then printf '%s' "$c"; return 0; fi
    fi
    return 1
}

install_claude_code() {
    local exe

    # The official installer refuses to run under sudo, because it installs into
    # $HOME and under sudo that is root's home - the binary would land where the
    # user's own shell cannot see it. Say so before it fails.
    if [ "$(id -u)" -eq 0 ] && [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != "root" ]; then
        say_warn "This script is running under sudo."
        printf '%s\n' "       The Claude Code installer refuses sudo on purpose - it installs into"
        printf '%s\n' "       your home directory. Re-run this script as your normal user."
        printf '\n'
    fi

    # 1) Official native installer - no Node.js required.
    say "Installing Claude Code via the official installer..."
    printf '\n'
    run_child_installer "$CLAUDE_CLI_INSTALL_URL" || say_warn "Native installer reported a failure"

    refresh_path
    if have claude; then
        say_ok "Claude Code installed"
        return 0
    fi

    # The installer may have placed the launcher somewhere not yet on PATH.
    if exe="$(find_claude_bin)"; then
        PATH="$(dirname "$exe"):$PATH"
        export PATH
        if have claude; then
            say_ok "Claude Code installed - found at $exe"
            return 0
        fi
    fi

    # 2) npm fallback, only when Node is already available.
    if have npm; then
        printf '\n'
        say_warn "Native install did not produce a usable claude command - trying npm..."
        run_native npm install -g @anthropic-ai/claude-code
        [ -n "$NATIVE_OUT" ] && printf '%s\n' "$NATIVE_OUT"
        refresh_path
        if have claude; then
            say_ok "Claude Code installed via npm"
            return 0
        fi
    fi

    return 1
}

# Printed only when the automatic install of Claude Code has already failed -
# never offered as a choice.
show_claude_recovery() {
    printf '\n'
    printf '%s\n' "  Install Claude Code by hand with either:"
    printf '%s\n' "    curl -fsSL $CLAUDE_CLI_INSTALL_URL | bash"
    printf '%s\n' "    npm install -g @anthropic-ai/claude-code"
    printf '\n'
    printf '%s\n' "  Then open a new terminal and re-run this script - it will pick up"
    printf '%s\n' "  from Step 2 automatically."
    printf '\n'
}

find_cursor_agent_bin() {
    # The official installer symlinks ~/.local/bin/cursor-agent (and
    # ~/.local/bin/agent) at the unpacked copy under
    # ~/.local/share/cursor-agent/versions/<version>/.
    local c
    for c in \
        "$HOME/.local/bin/cursor-agent" \
        "$HOME/.local/bin/agent"
    do
        if [ -x "$c" ]; then printf '%s' "$c"; return 0; fi
    done
    for c in "$HOME"/.local/share/cursor-agent/versions/*/cursor-agent; do
        if [ -x "$c" ]; then printf '%s' "$c"; return 0; fi
    done
    return 1
}

install_cursor_agent() {
    local exe
    say "Installing the Cursor CLI via the official installer..."
    printf '\n'

    # Deliberately only called when cursor-agent is absent. The installer
    # replaces the ~/.local/bin symlinks and unpacks a fresh version directory,
    # so running it over a working install would swap it rather than repair it.
    run_child_installer "$CURSOR_CLI_INSTALL_URL" || say_warn "Cursor CLI installer reported a failure"

    # The installer appends to a shell rc file, which an already-running shell
    # never re-reads; refresh_path is what makes the new symlink visible here.
    refresh_path
    if have cursor-agent; then
        say_ok "Cursor CLI installed"
        return 0
    fi

    if exe="$(find_cursor_agent_bin)"; then
        PATH="$(dirname "$exe"):$PATH"
        export PATH
        if have cursor-agent; then
            say_ok "Cursor CLI installed - found at $exe"
            return 0
        fi
    fi

    return 1
}

show_cursor_cli_recovery() {
    printf '\n'
    printf '%s\n' "  Install the Cursor CLI by hand with:"
    printf '%s\n' "    curl -fsS $CURSOR_CLI_INSTALL_URL | bash"
    printf '\n'
    printf '%s\n' "  If that fails, install or update Cursor itself first:"
    printf '%s\n' "    https://cursor.com/download"
    printf '\n'
    printf '%s\n' "  Then open a new terminal and re-run this script."
    printf '\n'
}

# ── Error classification ───────────────────────────────────────────────────────

# An editor CLI can be installed and on PATH yet refuse to talk to the
# marketplace because nobody has signed in. That is not a broken install and
# must not be reported as one - it needs a one-time `login`, which is an
# interactive browser flow this script cannot and should not automate.
is_auth_error() {
    printf '%s' "$1" | grep -Eiq \
        'authentication required|not (logged in|authenticated)|unauthorized|(^|[^0-9])401([^0-9]|$)|please (log ?in|sign ?in)|CURSOR_API_KEY'
}

# Rebuild the command that started this run, so the "re-run" step is something
# the user can paste rather than something they have to remember. RAW_BASE
# already carries the ref this install came from, so a re-run stays on the same
# branch, and the editor selection has to survive into the new command line.
rerun_command() {
    local boot="$RAW_BASE/bootstrap.sh" extra="" ref=""

    # The ref has to be named twice: once in the URL that fetches bootstrap.sh,
    # and once as --ref, because a script piped into bash cannot tell which
    # branch it came from and would otherwise pull install.sh from main.
    # Anything that is not a github raw URL (an internal mirror, --raw-base
    # pointed elsewhere) has no ref to recover, so it is left alone.
    ref="$(printf '%s' "$RAW_BASE" | sed -n 's#^.*githubusercontent[.]com/[^/][^/]*/[^/][^/]*/\(.*\)/scripts$#\1#p')"
    [ -n "$ref" ] && [ "$ref" != "main" ] && extra=" --ref $ref"

    # Carry over the editors actually being installed into - NOT the --target
    # that was typed. The Step 1 menu records its answer in want_claude /
    # want_cursor and never writes back to TARGET, so keying off TARGET hands
    # someone who picked "2. Cursor" from the menu a re-run command with no
    # --target at all, which prompts them all over again. Naming it explicitly
    # also means the re-run skips the menu entirely.
    if   [ "${want_claude:-0}" -eq 1 ] && [ "${want_cursor:-0}" -eq 1 ]; then extra="$extra --target both"
    elif [ "${want_cursor:-0}" -eq 1 ];                                  then extra="$extra --target cursor"
    elif [ "${want_claude:-0}" -eq 1 ];                                  then extra="$extra --target claude"
    fi

    if [ -n "$extra" ]; then
        printf 'curl -fsSL %s | bash -s --%s\n' "$boot" "$extra"
    else
        printf 'curl -fsSL %s | bash\n' "$boot"
    fi
}

# Explain a missing login as three numbered actions, not as a description of the
# problem. This is the one failure that is entirely in the user's hands and not
# a fault in the install, so it must read like an instruction sheet: what to
# type, what will happen, what to do afterwards.
show_cli_auth_help() {
    local key="$1" name="$2" exe="$3" login_cmd account_of
    if [ "$key" = "cursor" ]; then
        login_cmd="cursor-agent login"
        account_of="Cursor"
    else
        login_cmd="$exe login"
        account_of="$name"
    fi

    printf '\n'
    printf '%s\n' "  It looks like you are not signed in to the $name CLI yet."
    printf '%s\n' "  Nothing is broken - the CLI just needs your account before it can"
    printf '%s\n' "  download the plugin. Three steps, about a minute:"
    printf '\n'
    printf '%s\n' "    1. Sign in - run this command:"
    printf '\n'
    printf '%s\n' "         $login_cmd"
    printf '\n'
    printf '%s\n' "    2. A browser window opens - sign in with your $account_of account,"
    printf '%s\n' "       then come back to this terminal."
    printf '\n'
    printf '%s\n' "    3. Run the installer again:"
    printf '\n'
    printf '%s\n' "         $(rerun_command)"
    printf '\n'
    printf '%s\n' "  You only ever do this once on this machine."
    printf '\n'

    if [ "$key" = "cursor" ]; then
        printf '%s\n' "  No browser on this machine? Use an API key from"
        printf '%s\n' "  https://cursor.com/dashboard instead of step 1:"
        printf '\n'
        printf '%s\n' "         export CURSOR_API_KEY=<your-key>"
        printf '\n'
    fi

    log "$key: printed login instructions (login='$login_cmd')"
}

# The POSIX counterpart of install.ps1's Test-LockError. Windows holds mandatory
# locks on an open .venv, so there it means "a running MCP server has the file";
# here an open file can always be replaced, so the only way a rebuild is refused
# is ownership or a read-only mount - usually a cache directory left root-owned
# by an earlier `sudo` run.
is_perm_error() {
    printf '%s' "$1" | grep -Eiq \
        'permission denied|EACCES|EPERM|operation not permitted|read-only file system|not writable'
}

show_permission_help() {
    local dir="$1"
    printf '\n'
    printf '%s\n' "  Something under this path is not writable by your user:"
    printf '%s\n' "    $dir"
    printf '\n'
    printf '%s\n' "  This is almost always the result of running an installer with sudo once:"
    printf '%s\n' "  the files end up owned by root. Hand them back with:"
    printf '\n'
    printf '%s\n' "    sudo chown -R \"\$(id -u):\$(id -g)\" \"$dir\""
    printf '\n'
    printf '%s\n' "  Then re-run this script. Do NOT re-run it with sudo - the editors and"
    printf '%s\n' "  their plugins are per-user installs and expect your own ownership."
    printf '\n'
}

# Run an editor CLI command. Treats "already exists" style output as success.
#
# Unlike install.ps1 there is no retry-after-releasing-locks pass: nothing on
# POSIX holds a file open in a way that blocks replacing it, so a permission
# error here is an ownership problem that a retry cannot fix. It is explained
# instead.
LAST_CLI_OUTPUT=""
run_cli_step() {
    local exe="$1" what="$2"
    shift 2

    run_native "$exe" "$@"
    LAST_CLI_OUTPUT="$NATIVE_OUT"
    # A missing login is reported by show_cli_auth_help in plain language.
    # Echoing the CLI's own "Authentication required. Run 'agent login', pass
    # --api-key/--auth-token, ..." on top of that buries the instructions in
    # flags nobody needs. run_native has already written the raw text to the
    # log, so nothing is lost by keeping it off the console.
    if [ -n "$NATIVE_OUT" ] && ! is_auth_error "$NATIVE_OUT"; then
        printf '%s\n' "$NATIVE_OUT"
    fi

    if [ "$NATIVE_EXIT" -ne 0 ] && is_perm_error "$NATIVE_OUT"; then
        printf '\n'
        say_warn "Permission error while $what"
    fi

    if [ "$NATIVE_EXIT" -ne 0 ] && ! printf '%s' "$NATIVE_OUT" | grep -Eiq 'already|exists'; then
        return 1
    fi
    return 0
}

# ── Plugin checkout discovery ──────────────────────────────────────────────────

# 1.2.3 -> a zero-padded, lexically sortable key. Only ever called on names that
# already matched the all-numeric test below, so printf cannot be fed a word.
version_sort_key() {
    local v="$1" a b c d
    v="${v#v}"; v="${v#V}"
    IFS=. read -r a b c d <<EOF
$v
EOF
    printf '%06d%06d%06d%06d' "${a:-0}" "${b:-0}" "${c:-0}" "${d:-0}"
}

# Pick the current plugin checkout under a cache root, into PLUGIN_DIR.
#
# Claude Code names these directories by version ("0.1.41"); Cursor names them
# by commit sha ("2026.07.23-e383d2b"), which carries no ordering. So: order by
# version when every name is a version, and fall back to modification time only
# when at least one is not. The fallback is deliberately NOT the default -
# creating a .venv inside a directory bumps that directory's mtime, so mtime
# alone would rank an older version above a newer one purely because it was
# synced later.
#
# The glob skips dotted names, which also skips the .tmp-* directory Cursor's
# own installer creates mid-download.
PLUGIN_DIR=""
pick_plugin_dir() {
    local root="$1" d name all_version=1 found=0 best=""
    PLUGIN_DIR=""
    [ -d "$root" ] || return 1

    for d in "$root"/*/; do
        [ -d "$d" ] || continue
        d="${d%/}"
        found=1
        name="$(basename "$d")"
        printf '%s' "$name" | grep -Eq '^[vV]?[0-9]+(\.[0-9]+){0,3}$' || all_version=0
    done
    [ "$found" -eq 1 ] || return 1

    if [ "$all_version" -eq 1 ]; then
        best="$(
            for d in "$root"/*/; do
                [ -d "$d" ] || continue
                d="${d%/}"
                printf '%s\t%s\n' "$(version_sort_key "$(basename "$d")")" "$d"
            done | LC_ALL=C sort | tail -n 1 | cut -f2-
        )"
    else
        best="$(
            for d in "$root"/*/; do
                [ -d "$d" ] || continue
                d="${d%/}"
                printf '%s\t%s\n' "$(mtime_of "$d")" "$d"
            done | LC_ALL=C sort -n | tail -n 1 | cut -f2-
        )"
    fi

    [ -n "$best" ] || return 1
    PLUGIN_DIR="$best"
    return 0
}

# Condition 1 of the uv sync check: is there a real environment in there?
# A bare .venv directory is not enough - an interrupted build leaves one behind
# with no interpreter, and uv sync has to run again to finish it.
env_built() {
    local venv="$1/.venv" py
    [ -f "$venv/pyvenv.cfg" ] || return 1
    for py in "bin/python" "bin/python3" "bin/python3.12" "Scripts/python.exe"; do
        [ -x "$venv/$py" ] && return 0
    done
    return 1
}

# Condition 2: is the environment older than what it was built from? pyvenv.cfg
# is written when the environment is created, so it is the honest "built at"
# timestamp; uv.lock and pyproject.toml are the inputs. An upgrade rewrites the
# inputs but leaves an existing .venv in place, which is exactly the case a
# plain existence check would miss.
env_stale() {
    local dir="$1" marker="$1/.venv/pyvenv.cfg" built src p srcmt
    [ -f "$marker" ] || return 0
    built="$(mtime_of "$marker")"
    for src in uv.lock pyproject.toml; do
        p="$dir/$src"
        [ -f "$p" ] || continue
        srcmt="$(mtime_of "$p")"
        if [ "${srcmt:-0}" -gt "${built:-0}" ]; then
            log "Stale env: $src is newer than .venv ($srcmt > $built)"
            return 0
        fi
    done
    return 1
}

# Ask the user to confirm a manual IDE step, then verify it actually happened.
#
# This deliberately does NOT poll blindly for a fixed timeout. A fixed wait is
# wrong in both directions: unattended it burns the entire timeout on a click
# nobody is going to make, and attended it either finishes in five seconds and
# keeps waiting anyway, or needs longer than the timeout allows. Asking puts the
# pace in the user's hands and costs nothing.
#
# A SHORT grace poll still runs after the confirmation, because the IDE applies
# the change asynchronously - here that matters more than on the uninstall side,
# since clicking Install starts a download that takes a moment to land. That is
# the only thing the seconds value now controls.
#
#   $1  name of a predicate function returning 0 once the step is done
#   $2  argument passed to that predicate
#   $3  grace seconds to keep re-checking after each confirmation
# Returns 0 when the step is confirmed done, 1 when skipped or never detected.
confirm_manual_step() {
    local check="$1" arg="$2" grace="$3" raw tries waited

    # Already in the desired state - nothing to ask about.
    "$check" "$arg" && return 0

    if ! can_prompt; then
        say_warn "Running unattended - not waiting for the manual step above."
        return 1
    fi

    tries=0
    while [ "$tries" -lt 3 ]; do
        tries=$((tries + 1))
        printf '  Press Enter once you have done this (or type s to skip): '
        if ! IFS= read -r raw < "$TTY_DEV"; then
            printf '\n'
            say_warn "No input available - not waiting."
            return 1
        fi
        raw="$(printf '%s' "$raw" | tr -d '\r' | tr '[:upper:]' '[:lower:]' \
               | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')"
        case "$raw" in
            s|skip|n|no|q|quit) say "  Skipped."; return 1 ;;
        esac

        waited=0
        while : ; do
            "$check" "$arg" && { [ "$waited" -ge 2 ] && printf '\n'; return 0; }
            [ "$waited" -ge "$grace" ] && break
            sleep 1
            waited=$((waited + 1))
            if   [ "$waited" -eq 2 ]; then printf '  Checking'
            elif [ "$waited" -gt 2 ]; then printf '.'
            fi
        done
        [ "$waited" -ge 2 ] && printf '\n'

        if [ "$tries" -lt 3 ]; then
            say_warn "Not there yet. If Cursor is still installing it, give it a moment and press Enter again."
        fi
    done
    return 1
}

open_url() {
    case "$OS" in
        macos) have open && open "$1" >/dev/null 2>&1 & ;;
        *)     have xdg-open && xdg-open "$1" >/dev/null 2>&1 & ;;
    esac
    return 0
}

# ── Banner ─────────────────────────────────────────────────────────────────────

detect_tty

printf '\n'
printf '%s\n' "======================================================"
printf '%s\n' "      CloudByte Plugin Installer ($OS)"
printf '%s\n' "======================================================"
printf '\n'
printf '%s\n' "  Log file: $LOG_FILE"
log "=== CloudByte plugin installer started ==="
log "OS: $OS | Marketplace: $MARKETPLACE_URL | Plugin: $PLUGIN_REF"
log "Target=$TARGET given=$TARGET_GIVEN tty=${TTY_DEV:-none}"

if [ "$OS" = "bsd" ] || [ "$OS" = "unknown" ]; then
    printf '\n'
    say_warn "Unrecognised platform ($(uname -s 2>/dev/null)) - continuing as Linux."
    printf '%s\n' "       Vendor installers may not publish a build for it."
fi

# ── Step 1: Detect editors ─────────────────────────────────────────────────────

header "Step 1: Detecting Editors"

if ! have claude || ! have cursor-agent; then
    refresh_path
fi

claude_present=0
cursor_present=0
have claude       && claude_present=1
have cursor-agent && cursor_present=1

# Cursor's CLI is a separate download from the IDE, so a machine can have Cursor
# without cursor-agent. Detect the IDE too, so auto mode installs the CLI for it
# instead of silently skipping the editor the user actually uses.
detect_cursor_ide() {
    local p
    [ -d "$CURSOR_DIR" ] && return 0
    have cursor && return 0
    case "$OS" in
        macos)
            for p in "/Applications/Cursor.app" "$HOME/Applications/Cursor.app"; do
                [ -d "$p" ] && return 0
            done
            ;;
        *)
            for p in \
                "/opt/Cursor" "/opt/cursor" \
                "/usr/share/applications/cursor.desktop" \
                "$HOME/.local/share/applications/cursor.desktop" \
                "/snap/bin/cursor" \
                "/var/lib/flatpak/exports/bin/co.anysphere.cursor" \
                "$HOME/.local/share/flatpak/exports/bin/co.anysphere.cursor"
            do
                [ -e "$p" ] && return 0
            done
            # AppImage installs have no fixed home; these are where they land.
            for p in "$HOME"/Applications/[Cc]ursor*.AppImage \
                     "$HOME"/[Aa]pps/[Cc]ursor*.AppImage \
                     "$HOME"/Downloads/[Cc]ursor*.AppImage
            do
                [ -e "$p" ] && return 0
            done
            ;;
    esac
    return 1
}

cursor_ide_present=0
detect_cursor_ide && cursor_ide_present=1

if [ "$claude_present" -eq 1 ]; then say_ok "Claude Code CLI found (claude)";  else say "Claude Code CLI not found"; fi
if [ "$cursor_present" -eq 1 ]; then say_ok "Cursor CLI found (cursor-agent)"; else say "Cursor CLI not found"; fi
if [ "$cursor_present" -eq 0 ] && [ "$cursor_ide_present" -eq 1 ]; then
    say "Cursor itself is installed - its CLI will be added"
fi

want_claude=0
want_cursor=0
case "$TARGET" in
    claude) want_claude=1; want_cursor=0 ;;
    cursor) want_claude=0; want_cursor=1 ;;
    both)   want_claude=1; want_cursor=1 ;;
    *)
        # auto / ask. Detection sets the default only - the menu below always
        # offers both editors, because either CLI can be installed from here.
        want_claude=$claude_present
        if [ "$cursor_present" -eq 1 ] || [ "$cursor_ide_present" -eq 1 ]; then want_cursor=1; fi
        if [ "$want_claude" -eq 0 ] && [ "$want_cursor" -eq 0 ]; then want_claude=1; fi
        ;;
esac
log "Target=$TARGET -> default claude=$want_claude cursor=$want_cursor"

# Every supported editor is a candidate regardless of what is installed: the
# script can provision either CLI, so a fresh machine still gets a real choice.
# Detection only annotates the list and picks what Enter selects.
cursor_note="not installed - needs the Cursor IDE"
if   [ "$cursor_present" -eq 1 ];     then cursor_note="installed"
elif [ "$cursor_ide_present" -eq 1 ]; then cursor_note="Cursor found - its CLI will be installed"
fi

claude_note="not installed - will be installed for you"
[ "$claude_present" -eq 1 ] && claude_note="installed"

CAND_KEYS=("claude" "cursor")
CAND_NAMES=("Claude Code" "Cursor")
CAND_NOTES=("$claude_note" "$cursor_note")

default_keys=""
[ "$want_claude" -eq 1 ] && default_keys="$default_keys claude"
[ "$want_cursor" -eq 1 ] && default_keys="$default_keys cursor"
default_keys="${default_keys# }"

# An explicit --target is an answer already given, so do not ask again.
explicit_target=0
if [ "$TARGET_GIVEN" -eq 1 ] && [ "$TARGET" != "ask" ]; then explicit_target=1; fi

if [ "$explicit_target" -eq 0 ] && can_prompt; then
    select_editors "$default_keys"
    want_claude=0
    want_cursor=0
    chosen_names=""
    for k in $SELECTED_KEYS; do
        case "$k" in
            claude) want_claude=1; chosen_names="$chosen_names, Claude Code" ;;
            cursor) want_cursor=1; chosen_names="$chosen_names, Cursor" ;;
        esac
    done
    printf '\n'
    say_ok "Selected: ${chosen_names#, }"
    log "User selected editors: $SELECTED_KEYS"
elif [ "$explicit_target" -eq 0 ]; then
    say "No terminal attached - using detected editors: $default_keys"
    log "Prompt skipped (non-interactive), using $default_keys"
fi

if [ "$want_claude" -eq 1 ] && [ "$claude_present" -eq 0 ]; then
    printf '\n'
    say_warn "Installing the Claude Code CLI"
    printf '\n'
    if install_claude_code; then
        claude_present=1
    else
        printf '\n'
        say_fail "Could not install the Claude Code CLI automatically."
        show_claude_recovery
        want_claude=0
    fi
fi

if [ "$want_cursor" -eq 1 ] && [ "$cursor_present" -eq 0 ]; then
    printf '\n'
    say_warn "Installing the Cursor CLI (cursor-agent)"
    printf '\n'
    if install_cursor_agent; then
        cursor_present=1
    else
        printf '\n'
        say_fail "Could not install the Cursor CLI automatically."
        show_cursor_cli_recovery
        want_cursor=0
    fi
fi

if [ "$want_claude" -eq 0 ] && [ "$want_cursor" -eq 0 ]; then
    log "No usable editor CLI for --target $TARGET - aborting"
    # Say which target failed rather than "no editor found" - with --target
    # cursor on a Claude-only machine, an editor IS present, just not the
    # requested one.
    if [ "$TARGET" = "auto" ]; then
        fail_exit "No usable editor CLI. Install Claude Code or Cursor and re-run." 2
    fi
    fail_exit "No usable editor CLI for --target $TARGET. Re-run without --target to use whatever is installed." 2
fi

# Editors to install into. can_install_plugin is the real difference between
# them: Claude Code installs a plugin from its CLI, Cursor only from its IDE.
EDITORS=""
if [ "$want_claude" -eq 1 ]; then
    EDITORS="$EDITORS claude"
    ed_set claude name               "Claude Code"
    ed_set claude exe                "claude"
    ed_set claude cache_root         "$CLAUDE_DIR/plugins/cache/claude-telemetry/claude-telemetry"
    ed_set claude plugin_ref         "$PLUGIN_REF"
    ed_set claude can_install_plugin 1
    ed_set claude marketplace_ok     0
    ed_set claude auth_required      0
    ed_set claude plugin_ok          0
    ed_set claude plugin_dir         ""
    ed_set claude sync_ok            0
fi
if [ "$want_cursor" -eq 1 ]; then
    EDITORS="$EDITORS cursor"
    ed_set cursor name               "Cursor"
    ed_set cursor exe                "cursor-agent"
    ed_set cursor cache_root         "$CURSOR_DIR/plugins/cache/cursor-telemetry/cursor-telemetry"
    ed_set cursor plugin_ref         "cursor-telemetry@cursor-telemetry"
    ed_set cursor can_install_plugin 0
    ed_set cursor marketplace_ok     0
    ed_set cursor auth_required      0
    ed_set cursor plugin_ok          0
    ed_set cursor plugin_dir         ""
    ed_set cursor sync_ok            0
fi
EDITORS="${EDITORS# }"

printf '\n'
for k in $EDITORS; do
    run_native "$(ed_get "$k" exe)" --version
    ver="$(printf '%s' "$NATIVE_OUT" | head -n 1)"
    [ -n "$ver" ] || ver="version unknown"
    say_ok "$(ed_get "$k" name) ready - $ver"
done

# ── Step 2: uv (and, through it, Python) ───────────────────────────────────────

header "Step 2: Checking uv"

# uv is the only hard dependency. Python 3.12 is provisioned BY uv in Step 3,
# into uv's own directory - the user's existing python, whatever its version, is
# neither required nor modified.
if have uv; then say_ok "uv found"; else say_warn "uv not found"; fi

if python_present; then
    sys_py=""
    for cmd in python3 python; do
        have "$cmd" || continue
        run_native "$cmd" --version
        case "$NATIVE_OUT" in
            Python\ [0-9]*) sys_py="$(printf '%s' "$NATIVE_OUT" | head -n 1 | sed 's/^Python //')"; break ;;
        esac
    done
    [ -n "$sys_py" ] && say "Your default python is $sys_py - it will not be changed"
fi

# Set when uv has to be installed, which makes Step 3 mandatory: validate.sh is
# what installs uv, so --skip-prereqs cannot be honoured in that case.
install_deps=0

if ! have uv; then
    printf '\n'
    printf '%s\n' "  uv is required by the CloudByte plugin. It also supplies the"
    printf '%s\n' "  Python 3.12 the plugin runs on, without altering your own."
    printf '\n'
    say "It will be installed automatically in Step 3."
    install_deps=1
    log "uv missing - Step 3 will install it (forced, --skip-prereqs ignored)"
else
    printf '\n'
    say "uv is present - Step 3 will make sure Python 3.12 is available to it."
fi

# ── Step 3: Prerequisites validation ───────────────────────────────────────────

if [ "$OPT_SKIP_PREREQS" -eq 1 ] && [ "$install_deps" -eq 0 ]; then
    header "Step 3: Prerequisites (skipped)"
    say_warn "--skip-prereqs supplied - not running validate.sh"
else
    header "Step 3: Running Prerequisites Validation"

    if [ "$OPT_SKIP_PREREQS" -eq 1 ]; then
        say_warn "--skip-prereqs ignored - validation is what installs uv"
    fi

    # The script directory is unknown when this file was piped into bash, in
    # which case validate.sh has to be downloaded.
    script_dir=""
    if [ -n "${BASH_SOURCE:-}" ] && [ -f "${BASH_SOURCE:-}" ]; then
        script_dir="$(cd "$(dirname "${BASH_SOURCE}")" 2>/dev/null && pwd)" || script_dir=""
    fi

    local_validate=""
    [ -n "$script_dir" ] && local_validate="$script_dir/validate.sh"
    validate_path=""
    temp_dir=""

    if [ -n "$local_validate" ] && { [ "$OPT_USE_LOCAL_VALIDATE" -eq 1 ] || [ -f "$local_validate" ]; }; then
        validate_path="$local_validate"
        say "Using local validate.sh: $validate_path"
    else
        temp_dir="$(mktemp -d "${TMPDIR:-/tmp}/cloudbyte-install-XXXXXXXX" 2>/dev/null)" \
            || temp_dir="${TMPDIR:-/tmp}/cloudbyte-install-$$"
        mkdir -p "$temp_dir"
        validate_path="$temp_dir/validate.sh"
        say "Downloading validate.sh..."
        if ! fetch "$RAW_BASE/validate.sh" "$validate_path"; then
            log "Download failed: $RAW_BASE/validate.sh"
            rm -rf "$temp_dir"
            fail_exit "Failed to download validate.sh - check your internet connection." 4
        fi
    fi

    bash "$validate_path" < "${TTY_DEV:-/dev/null}"
    prereq_exit=$?

    [ -n "$temp_dir" ] && rm -rf "$temp_dir"

    if [ "$prereq_exit" -ne 0 ]; then
        fail_exit "Prerequisites failed (exit: $prereq_exit) - fix the issue above and re-run this script." 4
    fi

    # validate.sh ran in a child process; pick up any PATH it created.
    refresh_path

    printf '\n'
    say_ok "Prerequisites ready"

    # Python deliberately is not checked here: it lives inside uv's own
    # directory and is never expected on PATH.
    if ! have uv; then
        say_warn "uv still not visible on this session's PATH - open a new terminal and re-run."
    fi
fi

# ── Step 4: Add marketplace ────────────────────────────────────────────────────

header "Step 4: Adding Marketplace"

marketplace_any=0
auth_blocked=0
editor_count=0

for k in $EDITORS; do
    editor_count=$((editor_count + 1))
    name="$(ed_get "$k" name)"
    exe="$(ed_get "$k" exe)"
    say "$name: $exe plugin marketplace add $MARKETPLACE_URL"
    if run_cli_step "$exe" "adding the marketplace to $name" plugin marketplace add "$MARKETPLACE_URL"; then
        ed_set "$k" marketplace_ok 1
        marketplace_any=$((marketplace_any + 1))
        say_ok "$name: marketplace added"
    elif is_auth_error "$LAST_CLI_OUTPUT"; then
        ed_set "$k" auth_required 1
        auth_blocked=$((auth_blocked + 1))
        # Deliberately a warning, not a failure: the install stops, but nothing
        # went wrong and there is nothing to debug - one login and a re-run
        # finishes it. A red [FAIL] here reads as "your machine is broken".
        say_warn "$name: sign-in needed before the plugin can be downloaded"
        show_cli_auth_help "$k" "$name" "$exe"
        log "$k: marketplace add blocked by missing login"
    else
        say_fail "$name: marketplace add failed"
        if is_perm_error "$LAST_CLI_OUTPUT"; then
            show_permission_help "$(dirname "$(dirname "$(ed_get "$k" cache_root)")")"
        fi
    fi
    printf '\n'
done

# Only fatal when no editor got the marketplace - one editor failing must not
# discard a working install into the other.
if [ "$marketplace_any" -eq 0 ]; then
    if [ "$auth_blocked" -eq "$editor_count" ]; then
        fail_exit "Stopped here - do the 3 steps above and the install will finish." 5 expected
    fi
    fail_exit "Failed to add the marketplace to any editor." 5
fi

# ── Step 5: Install plugin ─────────────────────────────────────────────────────

header "Step 5: Installing Plugin"

cli_capable=0
cli_capable_ok=0

for k in $EDITORS; do
    name="$(ed_get "$k" name)"
    exe="$(ed_get "$k" exe)"

    if [ "$(ed_get "$k" marketplace_ok)" != "1" ]; then
        say_warn "$name: skipping - marketplace was not added"
        continue
    fi

    if [ "$(ed_get "$k" can_install_plugin)" = "1" ]; then
        cli_capable=$((cli_capable + 1))
        ref="$(ed_get "$k" plugin_ref)"
        say "$name: $exe plugin install $ref"
        if run_cli_step "$exe" "installing the plugin in $name" plugin install "$ref"; then
            ed_set "$k" plugin_ok 1
            cli_capable_ok=$((cli_capable_ok + 1))
            say_ok "$name: plugin installed"
        else
            say_fail "$name: plugin install failed"
            if is_perm_error "$LAST_CLI_OUTPUT"; then
                show_permission_help "$(dirname "$(dirname "$(ed_get "$k" cache_root)")")"
            fi
        fi
        printf '\n'
        continue
    fi

    # Cursor: the CLI has no plugin install verb, so the last mile is the IDE.
    # Warn when the IDE is missing entirely - otherwise --target cursor on a
    # machine without Cursor waits for a plugin that can never appear.
    if [ "$cursor_ide_present" -eq 0 ]; then
        say_warn "Cursor itself was not found on this machine."
        printf '%s\n' "       The CLI is installed and the marketplace is registered, but the"
        printf '%s\n' "       steps below need the Cursor IDE: https://cursor.com/download"
        printf '\n'
    fi

    printf '%s\n' "  $name installs plugins from the IDE, not the CLI."
    printf '%s\n' "  The marketplace is registered - one short task left in Cursor."
    printf '\n'

    # The screenshot walkthrough is easier to follow than any amount of
    # prose, so it goes first when there is one. Gated on a real URL: an
    # unset PLUGIN_GUIDE_URL must not print "see <nothing>" above the steps.
    if printf '%s' "$PLUGIN_GUIDE_URL" | grep -Eq '^https?://'; then
        printf '%s\n' "  Easiest way - the visual guide, with a screenshot per step:"
        printf '\n'
        printf '%s\n' "    $PLUGIN_GUIDE_URL"
        printf '\n'
        # The guide covers the whole install, and by Step 5 the sign-in is
        # already behind us - so send the reader straight to the part that
        # matches where they are. Page number tracks the guide's own contents
        # page; keep it in step if the guide is ever re-cut.
        printf '%s\n' "    Already signed in? Start at page $PLUGIN_GUIDE_PAGE, $PLUGIN_GUIDE_SECTION -"
        printf '%s\n' "    the pages before it only cover the sign-in step."
        printf '\n'
        printf '%s\n' "  Rather not open a browser? The same thing in text:"
        log "cursor: printed manual IDE steps (visual guide: $PLUGIN_GUIDE_URL)"
    else
        printf '%s\n' "  Do this in Cursor:"
        log "cursor: printed manual IDE steps (visual guide: not configured)"
    fi

    printf '\n'
    printf '%s\n' "    1. Open Cursor"
    printf '%s\n' "    2. Cursor Settings  >  Customize  >  Browse Marketplace"
    printf '%s\n' "    3. Search for  cursor-telemetry  and click Add"
    printf '%s\n' "    4. Go to the Plugins section and check cursor-telemetry is"
    printf '%s\n' "       listed there as installed"
    printf '%s\n' "    5. Start a new agent session, then just start prompting -"
    printf '%s\n' "       from that point on your work is being recorded"
    printf '\n'

    # Ask, then verify - not a blind timer. See confirm_manual_step.
    # pick_plugin_dir doubles as the predicate: it returns 0 once a checkout
    # exists and leaves the path in PLUGIN_DIR for the lines below.
    if confirm_manual_step pick_plugin_dir "$(ed_get "$k" cache_root)" "$CURSOR_GRACE_SECONDS"; then
        ed_set "$k" plugin_dir "$PLUGIN_DIR"
        ed_set "$k" plugin_ok 1
        say_ok "$name: plugin installed"
        say "  $PLUGIN_DIR"
    else
        say_warn "$name: plugin not installed yet - do the steps above."
        printf '%s\n' "       Nothing else is needed afterwards: the plugin builds its own"
        printf '%s\n' "       environment the first time Cursor runs it."
    fi
    printf '\n'
done

# Fatal only when every editor that CAN install from its CLI failed. Cursor
# waiting on its IDE step is an expected outcome, not a failure.
if [ "$cli_capable" -gt 0 ] && [ "$cli_capable_ok" -eq 0 ]; then
    fail_exit "Plugin install failed in every editor that supports CLI installation." 6
fi

# ── Step 6: Plugin environment ─────────────────────────────────────────────────

header "Step 6: Installing Plugin Dependencies"

# Sync one plugin checkout. Returns 0 when the environment is usable, whether it
# was just built or was already good.
sync_plugin_env() {
    local name="$1" dir="$2"
    local built=0 stale=0 saved_venv

    # The two conditions that decide whether a sync is needed at all. Neither is
    # sufficient alone: a .venv can exist without an interpreter (interrupted
    # build), and a complete .venv can be older than the uv.lock it was built
    # from (plugin upgraded in place).
    env_built "$dir" && built=1
    env_stale "$dir" && stale=1

    if [ "$built" -eq 1 ] && [ "$stale" -eq 0 ]; then
        say_ok "$name: dependencies already installed and up to date"
        say    "  Location: $dir"
        return 0
    fi
    if [ "$built" -eq 1 ]; then
        say "$name: dependencies changed since the last install - updating them"
    else
        say "$name: installing dependencies for the first time"
    fi

    say "  Location: $dir"
    say "  This downloads Python packages and can take a minute the first time..."

    # An inherited VIRTUAL_ENV from the caller's shell makes uv warn and target
    # the wrong environment - hide it for the duration of the sync.
    saved_venv="${VIRTUAL_ENV:-}"
    unset VIRTUAL_ENV

    run_native uv sync --frozen --python 3.12 --directory "$dir"
    [ -n "$NATIVE_OUT" ] && printf '%s\n' "$NATIVE_OUT"

    # No lock-release retry here, unlike install.ps1: a running MCP server
    # cannot block uv from replacing this .venv on POSIX. A refusal means the
    # directory is not ours to write, which killing processes would not fix.
    if [ "$NATIVE_EXIT" -ne 0 ] && is_perm_error "$NATIVE_OUT"; then
        show_permission_help "$dir"
    fi

    [ -n "$saved_venv" ] && export VIRTUAL_ENV="$saved_venv"

    if [ "$NATIVE_EXIT" -eq 0 ]; then
        say_ok "$name: dependencies installed"
        return 0
    fi

    say_warn "$name: dependencies not fully installed - the plugin will retry on first use."
    printf '%s\n' "       To finish it now, run:"
    printf '%s\n' "         uv sync --frozen --python 3.12 --directory \"$dir\""
    return 1
}

uv_available=0
have uv && uv_available=1
if [ "$uv_available" -eq 0 ]; then
    say_warn "uv is not on this terminal's PATH - skipping the dependency install."
    printf '%s\n' "       Open a new terminal and re-run this script to finish."
fi

for k in $EDITORS; do
    name="$(ed_get "$k" name)"
    dir="$(ed_get "$k" plugin_dir)"

    if [ -z "$dir" ]; then
        if pick_plugin_dir "$(ed_get "$k" cache_root)"; then
            dir="$PLUGIN_DIR"
            ed_set "$k" plugin_dir "$dir"
        fi
    fi

    if [ -z "$dir" ]; then
        say_warn "$name: plugin files not found yet - dependencies will install on first use."
        printf '%s\n' "         Looked in: $(ed_get "$k" cache_root)"
        printf '\n'
        continue
    fi
    if [ "$uv_available" -eq 0 ]; then
        printf '%s\n' "         $name: uv sync --frozen --python 3.12 --directory \"$dir\""
        continue
    fi

    if sync_plugin_env "$name" "$dir"; then
        ed_set "$k" sync_ok 1
    fi
    printf '\n'
done

# ── Step 7: Activation, dashboard, summary ─────────────────────────────────────

header "Step 7: Activate the Plugin (manual)"

printf '%s\n' "  The plugin is installed but not yet active in running sessions."
printf '\n'

for k in $EDITORS; do
    if [ "$k" = "claude" ]; then
        printf '%s\n' "  Claude Code"
        printf '%s\n' "    Quickest path : type  /reload-plugins"
        printf '%s\n' "    If tools do not appear, or you see an MCP error, restart instead:"
        printf '%s\n' "      CLI            : Ctrl+C, then  claude --resume <session-id>"
        printf '%s\n' "      VS Code/Desktop: close the Claude panel, reopen it, resume"
        printf '\n'
    else
        printf '%s\n' "  Cursor"
        if [ "$(ed_get "$k" plugin_ok)" = "1" ]; then
            printf '%s\n' "    Start a new agent session and begin prompting. If the tools do"
            printf '%s\n' "    not show up, reload the window (Command Palette > Reload Window)"
            printf '%s\n' "    or quit and reopen Cursor."
        else
            printf '%s\n' "    Cursor Settings > Customize > Browse Marketplace, add"
            printf '%s\n' "    cursor-telemetry, then start a new agent session."
            printf '%s\n' "    No terminal step is needed afterwards."
        fi
        printf '\n'
    fi
done

printf '%s\n' "  Note: reloading only affects the current window/session. Any other"
printf '%s\n' "        open sessions need their own restart."
printf '\n'

if [ "$OPT_OPEN_DASHBOARD" -eq 1 ]; then
    say "Opening dashboard..."
    open_url "$DASHBOARD_URL"
fi

printf '\n'
printf '%s\n' "======================================================"
printf '%s\n' "            ${C_GREEN}[OK]${C_NC} CloudByte is Ready!"
printf '%s\n' "======================================================"
printf '\n'

for k in $EDITORS; do
    if   [ "$(ed_get "$k" plugin_ok)" = "1" ] && [ "$(ed_get "$k" sync_ok)" = "1" ]; then
        state="installed and ready to use"
    elif [ "$(ed_get "$k" plugin_ok)" = "1" ]; then
        state="installed - dependencies install the first time you use it"
    elif [ "$(ed_get "$k" marketplace_ok)" = "1" ]; then
        state="marketplace added - finish the install in the IDE"
    elif [ "$(ed_get "$k" auth_required)" = "1" ]; then
        state="sign in first: run '$(ed_get "$k" exe) login', then run the installer again"
    else
        state="not installed"
    fi
    printf '  %-12s ->  %s\n' "$(ed_get "$k" name)" "$state"
done

printf '\n'
printf '%s\n' "  Dashboard  ->  $DASHBOARD_URL"
printf '%s\n' "  Logs       ->  $CLOUDBYTE_DIR/logs/"
printf '\n'
printf '%s\n' "  In the dashboard you will find:"
printf '%s\n' "    Sessions     - every Claude session with start/end times"
printf '%s\n' "    Prompts      - every prompt sent, with token counts"
printf '%s\n' "    Observations - technical notes Claude recorded about your work"
printf '%s\n' "    Timeline     - full activity history across sessions"
printf '\n'
printf '%s\n' "  After activation the plugin records automatically in the background."
printf '\n'
printf '%s\n' "======================================================"
printf '\n'

log "=== Installer finished successfully ==="
exit 0
