# CloudByte Plugin Installer

Install the CloudByte plugin end-to-end: prerequisites, plugin install, and verification.

## Step 1 — Detect OS and run prerequisites script

```bash
echo ""
echo "╔══════════════════════════════════════════╗"
echo "║      CloudByte Plugin Installer          ║"
echo "╚══════════════════════════════════════════╝"
echo ""

RAW_BASE="https://raw.githubusercontent.com/CloudByte-AI/claude-telemetry/main/scripts"
TMPDIR_CB=$(mktemp -d)

# Detect OS
if [[ "$OSTYPE" == "msys" || "$OSTYPE" == "cygwin" || "$OS" == "Windows_NT" ]]; then
    DETECTED_OS="windows"
else
    DETECTED_OS="unix"
fi

echo "── Step 1: Prerequisites ($DETECTED_OS) ────────────"
echo ""

if [ "$DETECTED_OS" = "windows" ]; then
    echo "Downloading validate.ps1..."
    curl -fsSL "$RAW_BASE/validate.ps1" -o "$TMPDIR_CB/validate.ps1"
    if [ $? -ne 0 ]; then
        echo "✗ Failed to download validate.ps1"
        echo "  Check your internet connection and try again."
        rm -rf "$TMPDIR_CB"
        exit 1
    fi
    powershell -ExecutionPolicy Bypass -File "$TMPDIR_CB/validate.ps1"
    PREREQ_EXIT=$?
else
    echo "Downloading validate.sh..."
    curl -fsSL "$RAW_BASE/validate.sh" -o "$TMPDIR_CB/validate.sh"
    if [ $? -ne 0 ]; then
        echo "✗ Failed to download validate.sh"
        echo "  Check your internet connection and try again."
        rm -rf "$TMPDIR_CB"
        exit 1
    fi
    bash "$TMPDIR_CB/validate.sh"
    PREREQ_EXIT=$?
fi

rm -rf "$TMPDIR_CB"

if [ $PREREQ_EXIT -ne 0 ]; then
    echo ""
    echo "✗ Prerequisites failed (exit: $PREREQ_EXIT)"
    echo "  Check logs at: ~/.cloudbyte/logs/setup/"
    echo "  Fix the issue above and re-run /cloudbyte-claude-plugin-install"
    exit 1
fi

echo ""
echo "✓ Prerequisites ready"
```

## Step 2 — Add marketplace

```bash
echo ""
echo "── Step 2: Adding Marketplace ──────────────"
echo ""

release_lock_unix() {
    echo "Attempting to release file lock..."
    lsof 2>/dev/null | grep -i "claude-telemetry" | awk '{print $2}' | sort -u | while read pid; do
        echo "  Killing PID $pid"
        kill -9 "$pid" 2>/dev/null
    done
    sleep 1
}

release_lock_windows() {
    powershell -Command "
        Get-WmiObject Win32_Process | Where-Object {
            \$_.CommandLine -like '*claude-telemetry*' -or
            \$_.CommandLine -like '*cloudbyte*'
        } | ForEach-Object {
            Write-Host \"  Killing PID \$(\$_.ProcessId): \$(\$_.Name)\"
            Stop-Process -Id \$_.ProcessId -Force -ErrorAction SilentlyContinue
        }
    "
    sleep 1
}

attempt_marketplace() {
    claude plugin marketplace add https://github.com/CloudByte-AI/claude-telemetry 2>&1
}

MARKETPLACE_OUTPUT=$(attempt_marketplace)
MARKETPLACE_EXIT=$?
echo "$MARKETPLACE_OUTPUT"

if echo "$MARKETPLACE_OUTPUT" | grep -qi "EACCES\|permission denied"; then
    echo ""
    echo "⚠ Permission error - releasing file lock..."
    if [[ "$OSTYPE" == "msys" || "$OSTYPE" == "cygwin" || "$OS" == "Windows_NT" ]]; then
        release_lock_windows
    else
        release_lock_unix
    fi
    echo "Retrying..."
    MARKETPLACE_OUTPUT=$(attempt_marketplace)
    MARKETPLACE_EXIT=$?
    echo "$MARKETPLACE_OUTPUT"
fi

if [ $MARKETPLACE_EXIT -ne 0 ] && ! echo "$MARKETPLACE_OUTPUT" | grep -qi "already\|exists"; then
    echo ""
    echo "✗ Failed to add marketplace (exit: $MARKETPLACE_EXIT)"
    exit 1
fi

echo ""
echo "✓ Marketplace added"
```

## Step 3 — Install plugin

```bash
echo ""
echo "── Step 3: Installing Plugin ────────────────"
echo ""

release_lock_unix() {
    lsof 2>/dev/null | grep -i "claude-telemetry" | awk '{print $2}' | sort -u | while read pid; do
        echo "  Killing PID $pid"
        kill -9 "$pid" 2>/dev/null
    done
    sleep 1
}

release_lock_windows() {
    powershell -Command "
        Get-WmiObject Win32_Process | Where-Object {
            \$_.CommandLine -like '*claude-telemetry*' -or
            \$_.CommandLine -like '*cloudbyte*'
        } | ForEach-Object {
            Write-Host \"  Killing PID \$(\$_.ProcessId): \$(\$_.Name)\"
            Stop-Process -Id \$_.ProcessId -Force -ErrorAction SilentlyContinue
        }
    "
    sleep 1
}

attempt_install() {
    claude plugin install claude-telemetry@claude-telemetry 2>&1
}

INSTALL_OUTPUT=$(attempt_install)
INSTALL_EXIT=$?
echo "$INSTALL_OUTPUT"

if echo "$INSTALL_OUTPUT" | grep -qi "EACCES\|permission denied"; then
    echo ""
    echo "⚠ Permission error - releasing file lock..."
    if [[ "$OSTYPE" == "msys" || "$OSTYPE" == "cygwin" || "$OS" == "Windows_NT" ]]; then
        release_lock_windows
    else
        release_lock_unix
    fi
    echo "Retrying..."
    INSTALL_OUTPUT=$(attempt_install)
    INSTALL_EXIT=$?
    echo "$INSTALL_OUTPUT"
fi

if [ $INSTALL_EXIT -ne 0 ] && ! echo "$INSTALL_OUTPUT" | grep -qi "already\|exists"; then
    echo ""
    echo "✗ Plugin install failed (exit: $INSTALL_EXIT)"
    exit 1
fi

echo ""
echo "✓ Plugin installed"
echo ""
echo "╔══════════════════════════════════════════╗"
echo "║   ✓  CloudByte installed successfully!   ║"
echo "╚══════════════════════════════════════════╝"
```

## Step 4 — Activate and verify

After install completes tell the user:

**Activate the plugin now:**

- **Claude Code CLI:** Run `/reload-plugins`
- **VS Code:** Restart the Claude session (close and reopen Claude panel)

---

**After reload/restart, verify it works:**

1. Ask Claude: `what cloudbyte tools are available?`
   - If Claude lists CloudByte tools → **working ✓**

2. Open in browser: `http://localhost:8765`
   - If the app loads → **fully working ✓**

---

**If the app does not load or plugin is not responding after reload:**

Tell the user:

> It looks like something may not have started correctly. I can read your CloudByte logs directly and diagnose the issue for you.
>
> **Can I read your logs to help debug?**
> Your logs are stored at:
> - Mac/Linux: `~/.cloudbyte/logs/`
> - Windows: `%USERPROFILE%\.cloudbyte\logs\`

If the user says yes, read all log files from that directory:

```bash
# Detect log directory
if [[ "$OSTYPE" == "msys" || "$OSTYPE" == "cygwin" || "$OS" == "Windows_NT" ]]; then
    LOG_DIR="${USERPROFILE}/.cloudbyte/logs"
else
    LOG_DIR="$HOME/.cloudbyte/logs"
fi

echo "=== CloudByte Log Directory: $LOG_DIR ==="
echo ""

# List all log files
find "$LOG_DIR" -type f -name "*.log" 2>/dev/null | sort | while read logfile; do
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "FILE: $logfile"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    tail -100 "$logfile"
    echo ""
done
```

After reading the logs, analyze them and help the user fix the exact issue found. Focus only on errors related to `claude-telemetry` or `cloudbyte`. Common issues to look for and how to fix:

- **Port 8765 already in use** → tell user to run: `kill $(lsof -t -i:8765)` (Mac/Linux) or `Stop-Process -Id (Get-NetTCPConnection -LocalPort 8765).OwningProcess -Force` (Windows)
- **MCP server failed to start** → suggest reconnect from MCP settings → find `claude-telemetry` → click Reconnect
- **Python/uv error in logs** → re-run `/cloudbyte-claude-plugin-install` to redo prerequisites
- **Config or auth error** → read the specific error from logs and guide user through the exact fix
- **Any other error** → quote the exact error line from the log and explain what it means and how to fix it
