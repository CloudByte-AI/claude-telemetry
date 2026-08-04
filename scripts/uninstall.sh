#!/usr/bin/env bash
#
# CloudByte telemetry plugin uninstaller for macOS and Linux - Claude Code and
# Cursor. POSIX counterpart of scripts/uninstall.ps1, with the same five steps
# and the same exit codes.
#
# DESCRIPTION
#   Removes the telemetry plugin from the editors that have it, and optionally
#   removes the marketplace entry and the captured data.
#
#     Step 1  - Detect which editors have the plugin, ask which to remove from
#     Step 2  - Uninstall the plugin
#     Step 3  - Ask whether to remove the marketplace entry
#     Step 4  - Ask whether to delete the captured data (~/.cloudbyte)
#     Step 5  - Summary
#
#   Nothing is removed without being asked for, and every prompt defaults to the
#   conservative answer. The two destructive answers - removing the marketplace
#   and deleting the data - are No by default.
#
#   Editor differences, the mirror image of the installer's:
#
#     Claude Code  The CLI can uninstall, so Step 2 is automatic:
#                    claude plugin uninstall claude-telemetry@claude-telemetry -y
#                  -y is mandatory, not cosmetic: the CLI refuses to run when
#                  stdout is not a TTY, and this script captures its output.
#
#     Cursor       The CLI has no uninstall verb, exactly as it has no install
#                  verb. Step 2 prints the IDE steps (Settings > Plugins >
#                  cursor-telemetry > Uninstall) and asks you to confirm once
#                  you have done them. It does not sit on a timer, and it does
#                  not second-guess the answer against the cache directory - see
#                  confirm_manual_step for why neither is right here.
#
#   About deleting the data. ~/.cloudbyte holds the SQLite database with every
#   captured session, prompt and observation, plus logs and session state. The
#   delete is permanent and there is no backup step, so the prompt says so
#   plainly and defaults to No.
#
#   Differences from the PowerShell version, all deliberate:
#
#     * Stopping processes is not required for the delete to SUCCEED here.
#       Windows refuses to unlink a file with an open handle; POSIX unlinks it
#       happily and lets the last reader close it. Processes are still stopped
#       first, for a different reason: a live worker recreates worker.pid,
#       active_sessions/ and log files while the delete is walking the tree, so
#       without stopping it the directory can come back half-populated. The
#       delete is therefore verified afterwards and retried once.
#     * The prompts read /dev/tty, not stdin. Under `curl | bash` stdin is the
#       download pipe, so reading it would consume the script.
#     * No taskkill/netstat. lsof and fuser find what holds the directory and
#       the port; both are optional and the script degrades to a pkill-style
#       command-line match when neither exists.
#
#   The plugin cache directories left behind under ~/.claude/plugins/cache/...
#   are not touched. Claude Code prunes those itself.
#
# OPTIONS
#   --target auto|ask|claude|cursor|both
#                           Which editors to remove from. auto (default) takes
#                           every editor that actually has the plugin. An
#                           explicit value skips the selection prompt.
#   --yes | --non-interactive
#                           Never prompt. Uninstalls from every detected editor
#                           and, because silence must not be read as consent for
#                           a destructive action, KEEPS both the marketplace
#                           entry and the data.
#   --remove-marketplace    Remove the marketplace entry without asking.
#   --keep-marketplace      Keep the marketplace entry without asking.
#   --delete-data           Delete ~/.cloudbyte without asking. Permanent.
#   --keep-data             Keep ~/.cloudbyte without asking.
#   --cursor-dir DIR        Cursor's data directory. Defaults to ~/.cursor.
#   --marketplace-url URL   Reported in Step 3.
#   --plugin-ref REF        <plugin>@<marketplace> form.
#   --help, -h              Print this option list.
#
#   Every option also accepts the --name=value form.
#
# EXAMPLES
#   bash ./scripts/uninstall.sh
#   curl -fsSL https://raw.githubusercontent.com/CloudByte-AI/claude-telemetry/main/scripts/bootstrap.sh | bash -s -- --script uninstall.sh
#   bash ./scripts/uninstall.sh --yes --remove-marketplace --delete-data
#
# EXIT CODES
#   0  success (including "nothing was installed")
#   1  unexpected failure
#   2  the plugin is not installed in any editor matching --target
#   5  marketplace removal failed for every targeted editor
#   6  plugin uninstall failed for every editor that supports CLI removal
#   7  the data directory could not be fully deleted

set -u

# ── Defaults ───────────────────────────────────────────────────────────────────

OPT_YES=0
OPT_REMOVE_MARKETPLACE=0
OPT_KEEP_MARKETPLACE=0
OPT_DELETE_DATA=0
OPT_KEEP_DATA=0

TARGET="auto"
TARGET_GIVEN=0
CURSOR_DIR_OPT=""
DASHBOARD_PORT=4723

MARKETPLACE_URL="https://github.com/CloudByte-AI/claude-telemetry"
PLUGIN_REF="claude-telemetry@claude-telemetry"

usage() {
    if [ -r "$0" ]; then
        sed -n '/^# CloudByte telemetry plugin uninstaller/,/^#   7  the data directory/p' "$0" \
            | sed 's/^#\{1,\} \{0,1\}//'
        return 0
    fi
    cat <<'EOF'
CloudByte telemetry plugin uninstaller for macOS and Linux.

  --target auto|ask|claude|cursor|both
  --yes | --non-interactive     never prompt; keeps marketplace and data
  --remove-marketplace          remove the marketplace entry
  --keep-marketplace            keep the marketplace entry
  --delete-data                 delete ~/.cloudbyte permanently
  --keep-data                   keep ~/.cloudbyte
  --cursor-dir DIR              Cursor data directory (default ~/.cursor)
  --marketplace-url URL         reported in Step 3
  --plugin-ref REF              <plugin>@<marketplace>

Full documentation is in the comment header of scripts/uninstall.sh.
EOF
}

die_usage() {
    printf '%s\n' "[FAIL] $1" >&2
    printf '%s\n' "       Run with --help for the option list." >&2
    exit 1
}

need_value() { [ "$2" -ge 1 ] || die_usage "$1 needs a value."; }

while [ $# -gt 0 ]; do
    case "$1" in
        --yes|-y|--non-interactive) OPT_YES=1; shift ;;
        --remove-marketplace)       OPT_REMOVE_MARKETPLACE=1; shift ;;
        --keep-marketplace)         OPT_KEEP_MARKETPLACE=1; shift ;;
        --delete-data)              OPT_DELETE_DATA=1; shift ;;
        --keep-data)                OPT_KEEP_DATA=1; shift ;;
        --help|-h)                  usage; exit 0 ;;

        --target)                   need_value "$1" $(($# - 1)); TARGET="$2"; TARGET_GIVEN=1; shift 2 ;;
        --target=*)                 TARGET="${1#--target=}"; TARGET_GIVEN=1; shift ;;


        --cursor-dir)               need_value "$1" $(($# - 1)); CURSOR_DIR_OPT="$2"; shift 2 ;;
        --cursor-dir=*)             CURSOR_DIR_OPT="${1#--cursor-dir=}"; shift ;;

        --marketplace-url)          need_value "$1" $(($# - 1)); MARKETPLACE_URL="$2"; shift 2 ;;
        --marketplace-url=*)        MARKETPLACE_URL="${1#--marketplace-url=}"; shift ;;

        --plugin-ref)               need_value "$1" $(($# - 1)); PLUGIN_REF="$2"; shift 2 ;;
        --plugin-ref=*)             PLUGIN_REF="${1#--plugin-ref=}"; shift ;;

        -*)                         unknown_opt="$1"; shift
                                    printf '%s\n' "[WARN] Ignoring unknown option: $unknown_opt" ;;
        *)                          shift ;;
    esac
done

case "$TARGET" in
    auto|ask|claude|cursor|both) ;;
    *) die_usage "--target must be one of: auto, ask, claude, cursor, both (got '$TARGET')." ;;
esac
if [ "$OPT_REMOVE_MARKETPLACE" -eq 1 ] && [ "$OPT_KEEP_MARKETPLACE" -eq 1 ]; then
    die_usage "--remove-marketplace and --keep-marketplace contradict each other."
fi
if [ "$OPT_DELETE_DATA" -eq 1 ] && [ "$OPT_KEEP_DATA" -eq 1 ]; then
    die_usage "--delete-data and --keep-data contradict each other."
fi

# ── OS detection ───────────────────────────────────────────────────────────────

case "$(uname -s 2>/dev/null || echo unknown)" in
    Darwin)                 OS="macos" ;;
    Linux)                  OS="linux" ;;
    MINGW*|MSYS*|CYGWIN*)   OS="windows" ;;
    FreeBSD|NetBSD|OpenBSD|DragonFly) OS="bsd" ;;
    *)                      OS="unknown" ;;
esac

if [ "$OS" = "windows" ]; then
    printf '\n%s\n' "[FAIL] This is the macOS/Linux uninstaller, but Windows was detected." >&2
    printf '%s\n' "       Use the PowerShell uninstaller instead, from a PowerShell prompt:" >&2
    printf '%s\n' "         & ([scriptblock]::Create((irm https://raw.githubusercontent.com/CloudByte-AI/claude-telemetry/main/scripts/bootstrap.ps1))) -Script uninstall.ps1" >&2
    printf '\n' >&2
    exit 1
fi

# ── Paths and logging ──────────────────────────────────────────────────────────

USER_HOME="$HOME"
CLOUDBYTE_DIR="$USER_HOME/.cloudbyte"

if [ -n "${CLAUDE_CONFIG_DIR:-}" ]; then CLAUDE_DIR="$CLAUDE_CONFIG_DIR"
else                                     CLAUDE_DIR="$USER_HOME/.claude"; fi

if   [ -n "$CURSOR_DIR_OPT" ];  then CURSOR_DIR="$CURSOR_DIR_OPT"
elif [ -n "${CURSOR_DIR:-}" ];  then CURSOR_DIR="$CURSOR_DIR"
else                                 CURSOR_DIR="$USER_HOME/.cursor"; fi

CLAUDE_CACHE="$CLAUDE_DIR/plugins/cache/claude-telemetry/claude-telemetry"
CURSOR_CACHE="$CURSOR_DIR/plugins/cache/cursor-telemetry/cursor-telemetry"

# The log lives inside the directory this script may be about to delete, so it is
# moved to a temp location once deletion has been agreed to. Until then the
# normal location is used, so an aborted run leaves its trail in the usual place.
LOG_DIR="$CLOUDBYTE_DIR/logs/setup"
mkdir -p "$LOG_DIR" 2>/dev/null || true
LOG_FILE="$LOG_DIR/uninstall-$(date '+%Y-%m-%d').log"

if [ -t 1 ]; then
    C_RED=$'\033[0;31m'; C_GREEN=$'\033[0;32m'; C_YELLOW=$'\033[1;33m'
    C_CYAN=$'\033[0;36m'; C_NC=$'\033[0m'
else
    C_RED=''; C_GREEN=''; C_YELLOW=''; C_CYAN=''; C_NC=''
fi

log() {
    printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >> "$LOG_FILE" 2>/dev/null || true
}

say()      { printf '%s\n' "$*";                          log "$*"; }
say_ok()   { printf '%s\n' "${C_GREEN}[OK]${C_NC} $*";    log "OK: $*"; }
say_warn() { printf '%s\n' "${C_YELLOW}[WARN]${C_NC} $*"; log "WARN: $*"; }
say_fail() { printf '%s\n' "${C_RED}[FAIL]${C_NC} $*";    log "FAIL: $*"; }

header() {
    local title="$1" pad="" n
    n=$((46 - ${#title})); [ "$n" -lt 3 ] && n=3
    while [ "$n" -gt 0 ]; do pad="$pad-"; n=$((n - 1)); done
    printf '\n%s\n\n' "${C_CYAN}-- $title $pad${C_NC}"
    log "=== $title ==="
}

fail_exit() {
    printf '\n'
    say_fail "$1"
    printf '%s\n' "  Full logs      : $LOG_DIR"
    printf '%s\n' "  Troubleshooting: https://github.com/CloudByte-AI/claude-telemetry/issues"
    printf '\n'
    exit "$2"
}

# ── Helpers ────────────────────────────────────────────────────────────────────

have() { command -v "$1" >/dev/null 2>&1; }

refresh_path() {
    local extra p
    extra="$HOME/.local/bin
$HOME/.cargo/bin
$HOME/.bun/bin
/opt/homebrew/bin
/usr/local/bin"
    if [ -f "$HOME/.local/bin/env" ]; then
        # shellcheck disable=SC1091
        . "$HOME/.local/bin/env" >/dev/null 2>&1 || true
    fi
    while IFS= read -r p; do
        [ -n "$p" ] || continue
        [ -d "$p" ] || continue
        case ":$PATH:" in *":$p:"*) ;; *) PATH="$PATH:$p" ;; esac
    done <<EOF
$extra
EOF
    export PATH
}

NATIVE_OUT=""
NATIVE_EXIT=0
run_native() {
    log "RUN: $*"
    NATIVE_OUT="$("$@" 2>&1)"
    NATIVE_EXIT=$?
    log "EXIT $NATIVE_EXIT :: $NATIVE_OUT"
    return 0
}

is_auth_error() {
    printf '%s' "$1" | grep -Eiq \
        'authentication required|not (logged in|authenticated)|unauthorized|(^|[^0-9])401([^0-9]|$)|please (log ?in|sign ?in)|CURSOR_API_KEY'
}

# "not installed" is the desired end state, so a CLI that says so has succeeded.
# The mirror of the installer treating "already exists" as success.
is_already_gone() {
    printf '%s' "$1" | grep -Eiq \
        'not installed|not found|no such plugin|is not currently installed|does not exist|no plugin'
}

# Where the terminal is, if any. Under `curl | bash` stdin is the download pipe,
# so /dev/tty is the only thing safe to read.
TTY_DEV=""
detect_tty() {
    [ "$OPT_YES" -eq 1 ] && return 0
    [ -n "${CI:-}" ] && return 0
    if [ -c /dev/tty ] && (: < /dev/tty) 2>/dev/null; then
        TTY_DEV="/dev/tty"
    elif [ -t 0 ]; then
        TTY_DEV="/dev/stdin"
    fi
    return 0
}
can_prompt() { [ -n "$TTY_DEV" ]; }

# Yes/No with an explicit default, used for every non-interactive path.
# $1 question, $2 default (0 = no, 1 = yes). Answer lands in ANSWER.
ANSWER=0
ask_yes_no() {
    local question="$1" default="$2" hint raw tries
    ANSWER="$default"
    can_prompt || return 0

    if [ "$default" -eq 1 ]; then hint="[Y/n]"; else hint="[y/N]"; fi
    tries=0
    while [ "$tries" -lt 3 ]; do
        tries=$((tries + 1))
        printf '  %s %s ' "$question" "$hint"
        if ! IFS= read -r raw < "$TTY_DEV"; then printf '\n'; return 0; fi
        raw="$(printf '%s' "$raw" | tr -d '\r' | tr '[:upper:]' '[:lower:]' \
               | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')"
        case "$raw" in
            '')      return 0 ;;
            y|yes)   ANSWER=1; return 0 ;;
            n|no)    ANSWER=0; return 0 ;;
            *)       printf '%s\n' "  Please answer y or n." ;;
        esac
    done
    return 0
}

# Same menu shape as the installer, but only editors that actually HAVE the
# plugin are listed - there is nothing to choose about an editor with nothing
# installed.
SELECTED_KEYS=""
select_editors() {
    local default_keys="$1"
    local i n count raw tok picked keys bad tries default_label=""

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
    printf '%s\n' "  Remove the plugin from which editors?"
    printf '\n'
    i=0
    while [ "$i" -lt "$count" ]; do
        local mark=" "
        case " $default_keys " in *" ${CAND_KEYS[$i]} "*) mark="*" ;; esac
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
        printf '  Selection: '
        if ! IFS= read -r raw < "$TTY_DEV"; then
            printf '\n'; say_warn "No input available - using the default."
            SELECTED_KEYS="$default_keys"; return 0
        fi
        raw="$(printf '%s' "$raw" | tr -d '\r' | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')"

        if [ -z "$raw" ]; then SELECTED_KEYS="$default_keys"; return 0; fi
        case "$(printf '%s' "$raw" | tr '[:upper:]' '[:lower:]')" in
            a|all)
                SELECTED_KEYS=""; i=0
                while [ "$i" -lt "$count" ]; do
                    SELECTED_KEYS="$SELECTED_KEYS ${CAND_KEYS[$i]}"; i=$((i + 1))
                done
                SELECTED_KEYS="${SELECTED_KEYS# }"; return 0 ;;
        esac

        keys=""; picked=0; bad=0
        for tok in $(printf '%s' "$raw" | tr ',' ' '); do
            case "$tok" in ''|*[!0-9]*) bad=1; break ;; esac
            n=$((10#$tok))
            if [ "$n" -lt 1 ] || [ "$n" -gt "$count" ]; then bad=1; break; fi
            local k="${CAND_KEYS[$((n - 1))]}"
            case " $keys " in *" $k "*) ;; *) keys="$keys $k"; picked=$((picked + 1)) ;; esac
        done
        if [ "$bad" -eq 0 ] && [ "$picked" -gt 0 ]; then
            SELECTED_KEYS="${keys# }"; return 0
        fi
        printf '%s\n' "  Not a valid selection - use numbers between 1 and $count."
    done

    say_warn "No valid selection after 3 attempts - using the default."
    SELECTED_KEYS="$default_keys"
    return 0
}

ed_set() { eval "ED_${1}_${2}=\"\$3\""; }
ed_get() { eval "printf '%s' \"\${ED_${1}_${2}:-}\""; }

# Is there a plugin checkout under this cache root? Any version directory counts
# - the question is only "is something installed", not "which version".
has_plugin_cache() {
    local root="$1" d
    [ -d "$root" ] || return 1
    for d in "$root"/*/; do
        [ -d "$d" ] && return 0
    done
    return 1
}

# Ask the user to confirm they have done a manual IDE step.
#
# Two things this deliberately does NOT do.
#
# It does not sit on a timer. A fixed wait is wrong in both directions:
# unattended it burns the whole timeout on a click nobody is going to make, and
# attended it either finishes in five seconds and keeps waiting anyway, or needs
# longer than the timeout allows. Asking puts the pace in the user's hands.
#
# It does not then check the cache directory to "verify" the answer. Cursor is
# free to leave that directory in place until it restarts or prunes, so its
# presence is not evidence that the user failed to uninstall - treating it as
# evidence would report "still installed" to somebody who did exactly what was
# asked. Nothing downstream branches on the result either: it decides a summary
# line, not an action. The user's confirmation is the authority.
#
# The one check that IS sound is the opposite direction: if the directory is
# already gone, the step is unambiguously done and there is nothing to ask.
#
#   $1  cache root to short-circuit on
# Returns 0 when confirmed (or already gone), 1 when skipped or unattended.
confirm_manual_step() {
    local root="$1" raw

    has_plugin_cache "$root" || return 0

    if ! can_prompt; then
        say_warn "Running unattended - cannot confirm the manual step above."
        return 1
    fi

    printf '  Press Enter once you have done this (or type s to skip): '
    if ! IFS= read -r raw < "$TTY_DEV"; then
        printf '\n'
        say_warn "No input available - skipping."
        return 1
    fi
    raw="$(printf '%s' "$raw" | tr -d '\r' | tr '[:upper:]' '[:lower:]' \
           | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')"
    case "$raw" in
        s|skip|n|no|q|quit) say "  Skipped."; return 1 ;;
    esac
    return 0
}

# ── Stopping what holds the data open ──────────────────────────────────────────

# Never signal ourselves or anything we are running inside of - this script can
# live in a path containing "claude-telemetry", so a naive command-line match
# would otherwise target the current shell.
ancestor_pids() {
    local cur=$$ i=0 out=""
    while [ "$i" -lt 12 ] && [ "$cur" -gt 1 ]; do
        out="$out $cur"
        cur="$(ps -o ppid= -p "$cur" 2>/dev/null | tr -d ' ')"
        case "$cur" in ''|*[!0-9]*) break ;; esac
        i=$((i + 1))
    done
    printf '%s' "${out# }"
}

PROTECTED_PIDS=""
is_protected() {
    case " $PROTECTED_PIDS " in *" $1 "*) return 0 ;; esac
    return 1
}

STOPPED_COUNT=0
stop_one_pid() {
    local p="$1" why="$2" name
    case "$p" in ''|*[!0-9]*) return 1 ;; esac
    [ "$p" -le 1 ] && return 1
    is_protected "$p" && return 1
    kill -0 "$p" 2>/dev/null || return 1

    name="$(ps -o comm= -p "$p" 2>/dev/null | sed 's/^[[:space:]]*//')"
    say "  Stopping PID $p (${name:-unknown}) - $why"
    # TERM first so the worker can remove its own PID file and close the DB
    # cleanly; KILL only if it ignores that.
    kill -TERM "$p" 2>/dev/null
    local waited=0
    while [ "$waited" -lt 5 ]; do
        kill -0 "$p" 2>/dev/null || break
        sleep 1
        waited=$((waited + 1))
    done
    if kill -0 "$p" 2>/dev/null; then
        log "PID $p ignored SIGTERM - sending SIGKILL"
        kill -KILL "$p" 2>/dev/null
    fi
    STOPPED_COUNT=$((STOPPED_COUNT + 1))
    return 0
}

# Strategy 1: the PID file the worker is supposed to write. Tried first and
# trusted least - it is frequently absent even while the worker runs.
stop_worker_by_pid_file() {
    local pidfile="$CLOUDBYTE_DIR/worker.pid" wpid
    [ -f "$pidfile" ] || { log "No worker.pid present"; return 0; }
    # Parsed without a JSON tool: grep the one field that matters.
    wpid="$(tr -d ' \n' < "$pidfile" 2>/dev/null | sed -n 's/.*"pid":\([0-9]\{1,\}\).*/\1/p')"
    if [ -n "$wpid" ]; then
        stop_one_pid "$wpid" "worker.pid" || true
    else
        say_warn "worker.pid could not be parsed - falling back to the port scan"
    fi
    return 0
}

# Strategy 2: whatever is listening on the dashboard port. This is the strategy
# that actually finds the worker in practice.
stop_worker_by_port() {
    local pids="" p
    if have lsof; then
        pids="$(lsof -nP -iTCP:"$DASHBOARD_PORT" -sTCP:LISTEN -t 2>/dev/null)"
    elif have fuser; then
        pids="$(fuser -n tcp "$DASHBOARD_PORT" 2>/dev/null | tr -d ':')"
    elif have ss; then
        pids="$(ss -lptnH "sport = :$DASHBOARD_PORT" 2>/dev/null \
                | grep -oE 'pid=[0-9]+' | cut -d= -f2)"
    fi
    for p in $pids; do
        stop_one_pid "$p" "listening on $DASHBOARD_PORT" || true
    done
    return 0
}

# Strategy 3: anything whose command line points at the plugin or its data.
# pgrep -f is used when available; the ps fallback covers minimal images.
stop_plugin_processes() {
    local pids="" p pat='claude-telemetry|cursor-telemetry|cloudbyte|start_mcp'
    if have pgrep; then
        pids="$(pgrep -f "$pat" 2>/dev/null)"
    else
        pids="$(ps -eo pid=,args= 2>/dev/null | grep -Ei "$pat" | grep -v grep | awk '{print $1}')"
    fi
    for p in $pids; do
        stop_one_pid "$p" "holds the plugin or its data open" || true
    done
    return 0
}

# Strategy 4: anything with a file open inside the directory itself. Only lsof
# can answer this, and it is the most direct question of the four.
stop_holders_of_dir() {
    local pids="" p
    have lsof || return 0
    pids="$(lsof -t +D "$CLOUDBYTE_DIR" 2>/dev/null)"
    for p in $pids; do
        stop_one_pid "$p" "has a file open under .cloudbyte" || true
    done
    return 0
}

stop_everything() {
    say "Closing anything still using that data..."
    PROTECTED_PIDS="$(ancestor_pids)"
    log "Protected pids: $PROTECTED_PIDS"
    STOPPED_COUNT=0

    stop_worker_by_pid_file
    stop_worker_by_port
    stop_plugin_processes
    stop_holders_of_dir

    if [ "$STOPPED_COUNT" -eq 0 ]; then say "  Nothing was running"
    else                                say "  Stopped $STOPPED_COUNT process(es)"; fi
    sleep 1
    return 0
}

dir_size_human() {
    du -sh "$1" 2>/dev/null | awk '{print $1}' || printf 'unknown'
}

# ── Banner ─────────────────────────────────────────────────────────────────────

detect_tty

printf '\n'
printf '%s\n' "======================================================"
printf '%s\n' "     CloudByte Plugin Uninstaller ($OS)"
printf '%s\n' "======================================================"
printf '\n'
printf '%s\n' "  Log file: $LOG_FILE"
log "=== CloudByte plugin uninstaller started ==="
log "OS=$OS Target=$TARGET Plugin=$PLUGIN_REF tty=${TTY_DEV:-none}"

# ── Step 1: Detect what is installed ───────────────────────────────────────────

header "Step 1: Detecting Installed Plugins"

if ! have claude || ! have cursor-agent; then
    refresh_path
fi

claude_cli=0; have claude       && claude_cli=1
cursor_cli=0; have cursor-agent && cursor_cli=1

# Two independent signals, because either can be true alone: the CLI listing is
# authoritative about registration, the cache directory is evidence on disk that
# survives a half-finished uninstall.
claude_listed=0
if [ "$claude_cli" -eq 1 ]; then
    run_native claude plugin list
    printf '%s' "$NATIVE_OUT" | grep -q "claude-telemetry" && claude_listed=1
fi
claude_cached=0; has_plugin_cache "$CLAUDE_CACHE" && claude_cached=1
cursor_cached=0; has_plugin_cache "$CURSOR_CACHE" && cursor_cached=1

claude_has=0
[ "$claude_listed" -eq 1 ] || [ "$claude_cached" -eq 1 ] && claude_has=1
cursor_has=$cursor_cached

if [ "$claude_has" -eq 1 ]; then
    if [ "$claude_listed" -eq 1 ]; then how="installed"
    else how="not fully removed - its files are still on disk"; fi
    say_ok "Claude Code: $how"
else
    say "Claude Code: not installed"
fi
if [ "$cursor_has" -eq 1 ]; then say_ok "Cursor: installed"
else                             say "Cursor: not installed"; fi

if [ "$claude_cli" -eq 0 ] && [ "$claude_has" -eq 1 ]; then
    say_warn "The claude CLI is not on PATH, so its plugin cannot be uninstalled automatically."
fi

want_claude=0
want_cursor=0
case "$TARGET" in
    claude) want_claude=1 ;;
    cursor) want_cursor=1 ;;
    both)   want_claude=1; want_cursor=1 ;;
    *)      want_claude=$claude_has; want_cursor=$cursor_has ;;
esac
log "Target=$TARGET -> default claude=$want_claude cursor=$want_cursor"

# Nothing installed anywhere is a successful no-op, not a failure.
if [ "$claude_has" -eq 0 ] && [ "$cursor_has" -eq 0 ]; then
    printf '\n'
    say_ok "The plugin is not installed in either editor - nothing to remove."
    printf '\n'
    printf '%s\n' "  If you only want to delete the data it captured, remove this folder:"
    printf '%s\n' "    rm -rf \"$CLOUDBYTE_DIR\""
    printf '\n'
    log "Nothing installed - exiting 0"
    exit 0
fi

# Only editors that actually have the plugin are offered.
CAND_KEYS=(); CAND_NAMES=(); CAND_NOTES=()
if [ "$claude_has" -eq 1 ]; then
    if [ "$claude_cli" -eq 1 ]; then note="installed"
    else                             note="installed - but the claude CLI is missing"; fi
    CAND_KEYS+=("claude"); CAND_NAMES+=("Claude Code"); CAND_NOTES+=("$note")
fi
if [ "$cursor_has" -eq 1 ]; then
    CAND_KEYS+=("cursor"); CAND_NAMES+=("Cursor"); CAND_NOTES+=("installed - needs a manual step in the IDE")
fi

default_keys=""
[ "$want_claude" -eq 1 ] && [ "$claude_has" -eq 1 ] && default_keys="$default_keys claude"
[ "$want_cursor" -eq 1 ] && [ "$cursor_has" -eq 1 ] && default_keys="$default_keys cursor"
default_keys="${default_keys# }"

explicit_target=0
if [ "$TARGET_GIVEN" -eq 1 ] && [ "$TARGET" != "ask" ]; then explicit_target=1; fi

# Only worth asking when there is more than one thing to choose between.
if [ "$explicit_target" -eq 0 ] && [ "${#CAND_KEYS[@]}" -gt 1 ] && can_prompt; then
    select_editors "$default_keys"
    want_claude=0; want_cursor=0; chosen_names=""
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
    want_claude=$claude_has
    want_cursor=$cursor_has
    removing_names=""
    for k in $default_keys; do
        case "$k" in
            claude) removing_names="$removing_names, Claude Code" ;;
            cursor) removing_names="$removing_names, Cursor" ;;
        esac
    done
    say "Removing from: ${removing_names#, }"
fi

# Asking for an editor that has nothing installed is a no-op for that editor.
if [ "$want_claude" -eq 1 ] && [ "$claude_has" -eq 0 ]; then
    say_warn "Claude Code does not have the plugin - skipping it."
    want_claude=0
fi
if [ "$want_cursor" -eq 1 ] && [ "$cursor_has" -eq 0 ]; then
    say_warn "Cursor does not have the plugin - skipping it."
    want_cursor=0
fi

if [ "$want_claude" -eq 0 ] && [ "$want_cursor" -eq 0 ]; then
    log "No targeted editor has the plugin - aborting"
    fail_exit "The plugin is not installed in any editor matching --target $TARGET." 2
fi

EDITORS=""
if [ "$want_claude" -eq 1 ]; then
    EDITORS="$EDITORS claude"
    ed_set claude name              "Claude Code"
    ed_set claude exe               "claude"
    ed_set claude cli_available     "$claude_cli"
    ed_set claude cache_root        "$CLAUDE_CACHE"
    ed_set claude plugin_ref        "$PLUGIN_REF"
    ed_set claude marketplace_name  "claude-telemetry"
    ed_set claude can_uninstall_cli 1
    ed_set claude plugin_removed    0
    ed_set claude marketplace_gone  0
    ed_set claude auth_required     0
fi
if [ "$want_cursor" -eq 1 ]; then
    EDITORS="$EDITORS cursor"
    ed_set cursor name              "Cursor"
    ed_set cursor exe               "cursor-agent"
    ed_set cursor cli_available     "$cursor_cli"
    ed_set cursor cache_root        "$CURSOR_CACHE"
    ed_set cursor plugin_ref        "cursor-telemetry@cursor-telemetry"
    ed_set cursor marketplace_name  "cursor-telemetry"
    ed_set cursor can_uninstall_cli 0
    ed_set cursor plugin_removed    0
    ed_set cursor marketplace_gone  0
    ed_set cursor auth_required     0
fi
EDITORS="${EDITORS# }"

# ── Step 2: Uninstall the plugin ───────────────────────────────────────────────

header "Step 2: Removing the Plugin"

cli_capable=0
cli_succeeded=0

for k in $EDITORS; do
    name="$(ed_get "$k" name)"
    exe="$(ed_get "$k" exe)"

    if [ "$(ed_get "$k" can_uninstall_cli)" = "1" ]; then
        if [ "$(ed_get "$k" cli_available)" != "1" ]; then
            say_fail "$name: the '$exe' CLI is not on PATH - cannot uninstall automatically."
            printf '%s\n' "       Open a terminal where '$exe' works and re-run, or remove it"
            printf '%s\n' "       from inside the editor."
            printf '\n'
            continue
        fi

        cli_capable=$((cli_capable + 1))
        ref="$(ed_get "$k" plugin_ref)"
        # -y is required, not optional: the CLI refuses to run when stdout is not
        # a TTY, and run_native captures stdout.
        say "$name: $exe plugin uninstall $ref -y"
        run_native "$exe" plugin uninstall "$ref" -y
        [ -n "$NATIVE_OUT" ] && printf '%s\n' "$NATIVE_OUT"

        if [ "$NATIVE_EXIT" -eq 0 ] || is_already_gone "$NATIVE_OUT"; then
            ed_set "$k" plugin_removed 1
            cli_succeeded=$((cli_succeeded + 1))
            say_ok "$name: plugin removed"
        elif is_auth_error "$NATIVE_OUT"; then
            ed_set "$k" auth_required 1
            say_fail "$name: not signed in - cannot reach the plugin registry"
            printf '%s\n' "       Run '$exe login' and re-run this script."
        else
            say_fail "$name: plugin uninstall failed"
        fi
        printf '\n'
        continue
    fi

    # Cursor: no uninstall verb, so the last mile is the IDE - the exact mirror
    # of the installer having to send people to the IDE to install it.
    printf '%s\n' "  $name removes plugins from the IDE, not the CLI."
    printf '%s\n' "  Do this in Cursor:"
    printf '\n'
    printf '%s\n' "    1. Open Cursor"
    printf '%s\n' "    2. Settings  >  Plugins  >  cursor-telemetry"
    printf '%s\n' "    3. Click Uninstall (or Remove / Disable)"
    printf '\n'

    if confirm_manual_step "$(ed_get "$k" cache_root)"; then
        ed_set "$k" plugin_removed 1
        say_ok "$name: plugin removal confirmed"
    else
        say_warn "$name: not confirmed - do the 3 steps above."
        printf '%s\n' "       The rest of this script will continue; nothing here depends on"
        printf '%s\n' "       it having happened yet."
    fi
    printf '\n'
done

# Fatal only when every editor that CAN be uninstalled from its CLI failed.
if [ "$cli_capable" -gt 0 ] && [ "$cli_succeeded" -eq 0 ]; then
    fail_exit "Plugin uninstall failed in every editor that supports CLI removal." 6
fi

# ── Step 3: Marketplace ────────────────────────────────────────────────────────

header "Step 3: Marketplace Entry"

printf '%s\n' "  The marketplace entry is what lets you re-install the plugin later:"
printf '%s\n' "    $MARKETPLACE_URL"
printf '\n'
printf '%s\n' "  Keeping it is harmless - it is a registry entry, not the plugin."
printf '\n'

if   [ "$OPT_REMOVE_MARKETPLACE" -eq 1 ]; then do_marketplace=1
elif [ "$OPT_KEEP_MARKETPLACE" -eq 1 ];   then do_marketplace=0
else
    # Default No: keeping a registry entry costs nothing, removing it costs a
    # re-add if the user only meant to remove the plugin.
    ask_yes_no "Remove the marketplace entry as well?" 0
    do_marketplace=$ANSWER
fi
log "Remove marketplace: $do_marketplace"

mp_attempted=0
mp_succeeded=0
MARKETPLACE_FAILED=0

if [ "$do_marketplace" -eq 1 ]; then
    printf '\n'
    for k in $EDITORS; do
        name="$(ed_get "$k" name)"
        exe="$(ed_get "$k" exe)"
        if [ "$(ed_get "$k" cli_available)" != "1" ]; then
            say_warn "$name: CLI not available - marketplace entry left in place."
            continue
        fi
        mp_attempted=$((mp_attempted + 1))
        mpname="$(ed_get "$k" marketplace_name)"
        say "$name: $exe plugin marketplace remove $mpname"
        run_native "$exe" plugin marketplace remove "$mpname"
        [ -n "$NATIVE_OUT" ] && printf '%s\n' "$NATIVE_OUT"

        if [ "$NATIVE_EXIT" -eq 0 ] || is_already_gone "$NATIVE_OUT"; then
            ed_set "$k" marketplace_gone 1
            mp_succeeded=$((mp_succeeded + 1))
            say_ok "$name: marketplace entry removed"
        else
            say_fail "$name: could not remove the marketplace entry"
        fi
        printf '\n'
    done

    if [ "$mp_attempted" -gt 0 ] && [ "$mp_succeeded" -eq 0 ]; then
        say_warn "The marketplace entry could not be removed from any editor."
        MARKETPLACE_FAILED=1
    fi
else
    say "Leaving the marketplace entry in place."
fi

# ── Step 4: Captured data ──────────────────────────────────────────────────────

header "Step 4: Captured Data"

data_exists=0
[ -d "$CLOUDBYTE_DIR" ] && data_exists=1
do_delete=0

if [ "$data_exists" -eq 0 ]; then
    say "No data directory at $CLOUDBYTE_DIR - nothing to delete."
else
    printf '%s\n' "  Location: $CLOUDBYTE_DIR"
    printf '%s\n' "  Size    : $(dir_size_human "$CLOUDBYTE_DIR")"
    printf '\n'
    printf '%s\n' "  This directory holds everything the plugin has captured:"
    printf '%s\n' "    - the database of all sessions, prompts, tokens and observations"
    printf '%s\n' "    - logs and per-session state"
    printf '\n'
    printf '%s\n' "${C_YELLOW}  Deleting it removes that data PERMANENTLY. There is no backup and${C_NC}"
    printf '%s\n' "${C_YELLOW}  no undo.${C_NC}"
    printf '\n'

    # If the other editor still has the plugin, the data is still in use: the
    # database is a single store shared by both integrations.
    still=""
    if [ "$claude_has" -eq 1 ] && [ "$want_claude" -eq 0 ]; then still="Claude Code"; fi
    if [ "$cursor_has" -eq 1 ] && [ "$want_cursor" -eq 0 ]; then
        if [ -n "$still" ]; then still="$still and Cursor"; else still="Cursor"; fi
    fi
    if [ -n "$still" ]; then
        say_warn "$still still has the plugin installed."
        printf '%s\n' "       Both editors share this one database, so deleting it now would"
        printf '%s\n' "       wipe the history that installation is still writing to."
        printf '\n'
    fi

    if   [ "$OPT_DELETE_DATA" -eq 1 ]; then do_delete=1
    elif [ "$OPT_KEEP_DATA" -eq 1 ];   then do_delete=0
    else
        ask_yes_no "Delete the captured data permanently?" 0
        do_delete=$ANSWER
    fi
fi
log "Delete data: $do_delete"

data_deleted=0
data_partial=0

if [ "$do_delete" -eq 1 ]; then
    # Guard against ever resolving to something that is not the data directory.
    # An empty HOME would otherwise make this "/.cloudbyte".
    if [ -z "$USER_HOME" ] || [ "$CLOUDBYTE_DIR" != "$USER_HOME/.cloudbyte" ] \
       || [ "$CLOUDBYTE_DIR" = "/" ] || [ "$CLOUDBYTE_DIR" = "/.cloudbyte" ]; then
        say_fail "Refusing to delete: '$CLOUDBYTE_DIR' is not the expected data directory."
        log "Delete guard tripped: dir='$CLOUDBYTE_DIR' home='$USER_HOME'"
        data_partial=1
    else
        printf '\n'
        # Move the log out of the directory being deleted, so the record of the
        # deletion survives it.
        new_log_dir="${TMPDIR:-/tmp}/cloudbyte-uninstall-logs"
        mkdir -p "$new_log_dir" 2>/dev/null || true
        LOG_DIR="$new_log_dir"
        LOG_FILE="$new_log_dir/uninstall-$(date '+%Y-%m-%d').log"
        log "Log continued here after moving out of the deleted directory"

        # Not needed for the unlink to succeed - POSIX has no mandatory locks -
        # but a live worker recreates worker.pid, active_sessions/ and log files
        # while rm is walking the tree, which leaves the directory half alive.
        stop_everything

        say "Deleting $CLOUDBYTE_DIR ..."
        attempts=0
        while [ "$attempts" -lt 3 ]; do
            attempts=$((attempts + 1))
            rm -rf "$CLOUDBYTE_DIR" 2>>"$LOG_FILE"
            [ -d "$CLOUDBYTE_DIR" ] || break
            if [ "$attempts" -lt 3 ]; then
                say_warn "The directory came back or could not be removed - retrying..."
                stop_everything
            fi
        done

        if [ ! -d "$CLOUDBYTE_DIR" ]; then
            data_deleted=1
            say_ok "Captured data deleted"
        else
            data_partial=1
            say_fail "Could not fully delete $CLOUDBYTE_DIR after $attempts attempts."
            printf '\n'
            printf '%s\n' "  Either a process keeps recreating it, or part of it is not yours to"
            printf '%s\n' "  remove. Close every Claude Code and Cursor window, then:"
            printf '%s\n' "    rm -rf \"$CLOUDBYTE_DIR\""
            printf '\n'
            printf '%s\n' "  If that reports 'Permission denied', an earlier sudo run left files"
            printf '%s\n' "  owned by root:"
            printf '%s\n' "    sudo chown -R \"\$(id -u):\$(id -g)\" \"$CLOUDBYTE_DIR\" && rm -rf \"$CLOUDBYTE_DIR\""
            printf '\n'
        fi
    fi
elif [ "$data_exists" -eq 1 ]; then
    say "Keeping the captured data."
    printf '\n'
    printf '%s\n' "  Remove it later by re-running this script and answering yes, or"
    printf '%s\n' "  delete the folder yourself:"
    printf '%s\n' "    rm -rf \"$CLOUDBYTE_DIR\""
fi

# ── Step 5: Summary ────────────────────────────────────────────────────────────

header "Step 5: Summary"

for k in $EDITORS; do
    name="$(ed_get "$k" name)"
    exe="$(ed_get "$k" exe)"
    if   [ "$(ed_get "$k" plugin_removed)" = "1" ]; then state="plugin removed"
    elif [ "$(ed_get "$k" auth_required)" = "1" ];  then state="not signed in - run '$exe login' and re-run"
    elif [ "$(ed_get "$k" cli_available)" != "1" ] && [ "$(ed_get "$k" can_uninstall_cli)" = "1" ]; then
        state="CLI missing - remove it from the editor"
    else state="not confirmed - finish the step in the IDE"
    fi
    if [ "$do_marketplace" -eq 1 ]; then
        if [ "$(ed_get "$k" marketplace_gone)" = "1" ]; then state="$state, marketplace removed"
        else                                                 state="$state, marketplace kept"; fi
    fi
    printf '  %-12s ->  %s\n' "$name" "$state"
done

printf '\n'
if   [ "$data_deleted" -eq 1 ]; then printf '%s\n' "  Captured data ->  deleted"
elif [ "$data_partial" -eq 1 ]; then printf '%s\n' "  Captured data ->  NOT fully deleted - see above"
elif [ "$data_exists" -eq 0 ];  then printf '%s\n' "  Captured data ->  none found"
else                                 printf '%s\n' "  Captured data ->  kept at $CLOUDBYTE_DIR"
fi

printf '\n'
printf '%s\n' "  A restart of each editor finishes the removal: any session that is"
printf '%s\n' "  open right now still has the old plugin loaded in memory."
printf '\n'

# The plugin cache is deliberately left alone - Claude Code prunes its own
# version directories, and deleting a checkout it still tracks causes more
# problems than the disk space it frees.

if [ "$data_partial" -eq 1 ]; then
    log "=== Uninstaller finished with data deletion problems ==="
    printf '%s\n\n' "======================================================"
    exit 7
fi

if [ "$MARKETPLACE_FAILED" -eq 1 ]; then
    log "=== Uninstaller finished; marketplace removal failed ==="
    printf '%s\n\n' "======================================================"
    exit 5
fi

printf '%s\n' "======================================================"
printf '%s\n' "            ${C_GREEN}[OK]${C_NC} CloudByte Removed"
printf '%s\n' "======================================================"
printf '\n'

log "=== Uninstaller finished successfully ==="
exit 0
