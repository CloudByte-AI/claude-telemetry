# CloudByte Plugin Installer

Install the CloudByte plugin end-to-end: prerequisites, plugin install, auto-reload, and launch app in browser — fully automated, no prompts.

## GLOBAL RULES — Follow these throughout every step

- **Never ask the user for permission to run any command or bash script** — just run it
- **Never ask "should I open the browser?", "shall I proceed?", "can I read your logs?"** — just do it
- **Never pause between steps to confirm** — execute Steps 1, 2, 3 sequentially without stopping
- **Only stop and wait at Step 4** — Claude physically cannot run `/reload-plugins`, user must do it manually
- **The moment user sends ANY message after Step 4** ("done", "reloaded", "ok", "yes", or anything at all) — immediately execute Step 5 and open the browser without asking

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
elif [[ "$OSTYPE" == "darwin"* ]]; then
    DETECTED_OS="macos"
else
    DETECTED_OS="linux"
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
```

## Step 4 — Activate the plugin (manual — Claude cannot do this automatically)

Claude cannot run `/reload-plugins` or restart sessions via bash. This is the ONLY step requiring user action.

Get the current session ID first:

```bash
# Get current session ID for resume command
echo "Current session ID:"
echo $CLAUDE_SESSION_ID 2>/dev/null || cat ~/.claude/.session_id 2>/dev/null || echo "(check your terminal title or claude --list-sessions)"
```

Then tell the user exactly this message — word for word:

---

> ✅ **Plugin installed successfully!**
>
> ---
>
> ⚡ **One step to activate it — then I'll show you something cool.**
>
> **Try the quick way first:**
> Type `/reload-plugins` in this terminal
>
> **⚠️ Important:** `/reload-plugins` is not always reliable for newly installed plugins.
> If you see any MCP error or CloudByte tools don't appear after reload — don't worry, just restart this session:
>
> **Full restart — same session (recommended):**
>
> *Claude Code CLI:*
> Press `Ctrl+C` to exit, then resume this exact session:
> ```
> claude --resume <your-session-id>
> ```
> *(Your session ID was shown above — copy it before pressing Ctrl+C)*
>
> *VS Code / Desktop:*
> Close the Claude panel → reopen it → Claude will offer to resume your recent session → select it
>
> ---
>
> ⚠️ **Already have other Claude sessions open?**
> Those sessions won't see the plugin until you restart them too.
> `/reload-plugins` only affects the current session — each open session needs its own restart to activate the plugin.
>
> ---
>
> 💬 **Once restarted or reloaded — just type `done`**
> I'll open the CloudByte dashboard and you'll see everything Claude has been tracking behind the scenes. Trust me, it's worth the restart. 🚀

---

Do NOT say anything else after this. Do NOT ask any follow up questions. Just wait.

The moment user sends ANY message — check what they said:

- **If user mentions MCP failed / tools not showing / error** → tell them:

> MCP didn't connect in this session — this is completely normal after a quick reload.
>
> Please do a **full restart of this same session**:
>
> *CLI:* Press `Ctrl+C` → then run:
> ```
> claude --resume <session-id>
> ```
>
> *VS Code:* Close Claude panel → reopen → resume the same session
>
> Come back and type `done` after restarting — it will work. 🚀

- **If user says done / ok / restarted / anything positive** → immediately execute Step 5.

## Step 5 — Auto open browser the moment user replies (no prompt, no asking)

The instant user sends any positive message after Step 4, tell the user:

> ✅ **Open the CloudByte dashboard now:**
> **http://localhost:8765**
>
> Here's what you'll see:
> - **Sessions tab** — every Claude session tracked with start/end time
> - **Prompts tab** — every prompt you sent with token counts
> - **Observations tab** — technical notes Claude recorded about your work
> - **Timeline** — full activity history across all sessions
>
> The plugin is now active and recording automatically in the background. Every session from this point will appear in the dashboard.

## Step 6 — Print final summary

```bash
echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║        ✓  CloudByte is Ready!                        ║"
echo "╚══════════════════════════════════════════════════════╝"
echo ""
echo "  Dashboard  →  http://localhost:8765"
echo "  Logs       →  ~/.cloudbyte/logs/"
echo ""
echo "  Plugin is active and tracking your Claude sessions."
echo "  Every prompt, token, and observation is now recorded."
echo ""
echo "══════════════════════════════════════════════════════"
```

---

## If anything fails — auto diagnose from logs

If any step above exits with a non-zero code, immediately read the logs without asking:

```bash
# Detect log directory
if [[ "$OSTYPE" == "msys" || "$OSTYPE" == "cygwin" || "$OS" == "Windows_NT" ]]; then
    LOG_DIR="${USERPROFILE}/.cloudbyte/logs"
else
    LOG_DIR="$HOME/.cloudbyte/logs"
fi

echo "=== Reading CloudByte logs for diagnosis ==="
echo ""

find "$LOG_DIR" -type f -name "*.log" 2>/dev/null | sort | while read logfile; do
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "FILE: $logfile"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    tail -100 "$logfile"
    echo ""
done
```

After reading logs, diagnose and fix automatically. Common fixes:

- **Port 8765 in use** → run: `kill $(lsof -t -i:8765)` (Mac/Linux) or `Stop-Process -Id (Get-NetTCPConnection -LocalPort 8765).OwningProcess -Force` (Windows), then re-open browser
- **MCP server failed** → re-run `/cloudbyte-claude-plugin-install`
- **Python/uv error** → re-run prerequisites step only
- **Config error** → quote exact error and fix inline