#Requires -Version 5.1
<#
.SYNOPSIS
    CloudByte / claude-telemetry plugin installer for Windows.

.DESCRIPTION
    Installs the claude-telemetry plugin into Claude Code:

      Step 1  - Check the Claude Code CLI (prompts, then installs it if missing)
      Step 2  - Check Python and uv (prompts before installing them)
      Step 3  - Run prerequisites validation (scripts/validate.ps1)
      Step 4  - Add the marketplace
      Step 5  - Install the plugin
      Step 6  - Prepare the plugin environment (uv sync)
      Step 7  - Print activation instructions and the summary

    Plugin activation (/reload-plugins or a session restart) cannot be
    automated and remains a manual step.

.PARAMETER Yes
    Answer "install" to every dependency prompt (Claude Code CLI, Python, uv).
    Implies fully unattended dependency installation.

.PARAMETER NonInteractive
    Never prompt. If the Claude Code CLI is missing the script exits with code 2,
    and if Python or uv are missing it exits with code 3, printing manual install
    instructions in both cases (combine with -Yes to install them anyway).

.PARAMETER SkipPrereqs
    Skip Step 3 (validate.ps1). Use only when Python and uv are known good.

.PARAMETER UseLocalValidate
    Use scripts/validate.ps1 from this checkout instead of downloading it.
    Automatic when the file exists next to this script.

.PARAMETER OpenDashboard
    Open the dashboard URL in the default browser at the end.

.PARAMETER MarketplaceUrl
    Marketplace repository URL. Defaults to the CloudByte-AI repo.

.PARAMETER RawBase
    Base raw.githubusercontent URL that validate.ps1 is fetched from. Point this
    at another branch to test changes before they land on main.

.PARAMETER PluginRef
    Plugin reference in <plugin>@<marketplace> form.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1

.EXAMPLE
    irm https://raw.githubusercontent.com/CloudByte-AI/claude-telemetry/main/scripts/install.ps1 | iex

.NOTES
    Exit codes:
      0  success
      1  unexpected failure
      2  Claude Code CLI missing and not installed (declined, non-interactive,
         or the automatic install failed)
      3  Python/uv missing and not installed (declined or non-interactive)
      4  prerequisites validation failed
      5  marketplace add failed
      6  plugin install failed
#>

[CmdletBinding()]
param(
    [switch] $Yes,
    [switch] $NonInteractive,
    [switch] $SkipPrereqs,
    [switch] $UseLocalValidate,
    [switch] $OpenDashboard,
    [string] $MarketplaceUrl = "https://github.com/CloudByte-AI/claude-telemetry",
    [string] $PluginRef      = "claude-telemetry@claude-telemetry",
    [string] $DashboardUrl   = "http://localhost:8765",
    [string] $RawBase        = "https://raw.githubusercontent.com/CloudByte-AI/claude-telemetry/main/scripts"
)

$ErrorActionPreference = "Stop"

# ── Paths and logging ──────────────────────────────────────────────────────────

$USER_HOME      = $env:USERPROFILE
$CLOUDBYTE_DIR  = Join-Path $USER_HOME ".cloudbyte"
$SETUP_LOG_DIR  = Join-Path $CLOUDBYTE_DIR "logs\setup"
$RAW_BASE       = $RawBase.TrimEnd("/")

if ($env:CLAUDE_CONFIG_DIR) { $CLAUDE_DIR = $env:CLAUDE_CONFIG_DIR }
else                        { $CLAUDE_DIR = Join-Path $USER_HOME ".claude" }

New-Item -ItemType Directory -Force -Path $SETUP_LOG_DIR | Out-Null
$LOG_FILE = Join-Path $SETUP_LOG_DIR ("install-" + (Get-Date -Format "yyyy-MM-dd") + ".log")

function log {
    param([string] $Message)
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -Path $LOG_FILE -Value "[$ts] $Message" -Encoding utf8
}

function Say     { param([string]$m) Write-Host $m;                          log $m }
function SayOk   { param([string]$m) Write-Host "[OK] $m"   -ForegroundColor Green;  log "OK: $m" }
function SayWarn { param([string]$m) Write-Host "[WARN] $m" -ForegroundColor Yellow; log "WARN: $m" }
function SayFail { param([string]$m) Write-Host "[FAIL] $m" -ForegroundColor Red;    log "FAIL: $m" }

function Header {
    param([string] $Title)
    $pad = "-" * [Math]::Max(3, 46 - $Title.Length)
    Write-Host ""
    Write-Host ("-- " + $Title + " " + $pad) -ForegroundColor Cyan
    Write-Host ""
    log "=== $Title ==="
}

function Fail-Exit {
    param([string] $Message, [int] $Code)
    Write-Host ""
    SayFail $Message
    Write-Host "  Full logs      : $SETUP_LOG_DIR"
    Write-Host "  Troubleshooting: https://github.com/CloudByte-AI/claude-telemetry/issues"
    Write-Host ""
    exit $Code
}

# ── Helpers ────────────────────────────────────────────────────────────────────

function Refresh-Path {
    # Pull in PATH changes made by installers running in other processes, plus
    # the usual per-user install locations that need no logoff. The current
    # process PATH is kept first so session-local entries are never dropped.
    $current = $env:PATH
    $machine = [System.Environment]::GetEnvironmentVariable("PATH", "Machine")
    $user    = [System.Environment]::GetEnvironmentVariable("PATH", "User")
    $extra   = @(
        "$env:USERPROFILE\.local\bin"
        "$env:USERPROFILE\.cargo\bin"
        "$env:LOCALAPPDATA\Programs\Python\Python312"
        "$env:LOCALAPPDATA\Programs\Python\Python312\Scripts"
        "$env:LOCALAPPDATA\Programs\Python\Python311"
        "$env:LOCALAPPDATA\Programs\Python\Python311\Scripts"
        "$env:LOCALAPPDATA\Programs\Python\Python310"
        "$env:LOCALAPPDATA\Programs\Python\Python310\Scripts"
    )

    $seen  = New-Object System.Collections.Generic.HashSet[string] ([StringComparer]::OrdinalIgnoreCase)
    $parts = New-Object System.Collections.Generic.List[string]
    foreach ($entry in (@($current, $machine, $user) -join ";").Split(";") + $extra) {
        $e = $entry.Trim()
        if ($e -and $seen.Add($e)) { [void] $parts.Add($e) }
    }
    $env:PATH = ($parts -join ";")
}

function Test-Command {
    param([string] $Name)
    return [bool] (Get-Command $Name -ErrorAction SilentlyContinue)
}

function Test-PythonPresent {
    foreach ($cmd in @("python3", "python")) {
        if (-not (Test-Command $cmd)) { continue }
        try {
            $ver = & $cmd --version 2>$null
            if ("$ver" -match "^Python [0-9]") { return $true }
        } catch { }
    }
    return $false
}

function Get-HostShell {
    if ($PSVersionTable.PSEdition -eq "Core") { return "pwsh" }
    return "powershell"
}

# Shared decision for "dependency X is missing - install it?".
# Returns "auto" (install now), "manual" (user will do it), or
# "blocked" (running non-interactively without -Yes).
function Get-DependencyChoice {
    param([string] $Subject)

    if ($Yes)            { return "auto" }
    if ($NonInteractive) { return "blocked" }

    Write-Host "  OPTIONS:"
    Write-Host "    1) Install $Subject now automatically"
    Write-Host "    2) Install it manually and re-run this script"
    Write-Host ""

    # Bounded so a redirected/absent stdin (irm | iex in a non-console
    # host) falls through to the manual path instead of spinning.
    $choice = ""
    for ($try = 0; $try -lt 3 -and $choice -ne "1" -and $choice -ne "2"; $try++) {
        try   { $choice = ("" + (Read-Host "  Choose an option (1 or 2)")).Trim() }
        catch { $choice = "2"; break }
    }
    if ($choice -eq "1") { return "auto" }
    if ($choice -ne "2") { SayWarn "No answer received - assuming manual installation." }
    return "manual"
}

function Find-ClaudeExe {
    $candidates = @(
        "$env:USERPROFILE\.local\bin\claude.exe"
        "$env:LOCALAPPDATA\Programs\claude\claude.exe"
        "$env:APPDATA\npm\claude.cmd"
    )
    foreach ($c in $candidates) {
        if (Test-Path $c) { return $c }
    }
    return $null
}

function Install-ClaudeCode {
    # 1) Official native installer - no Node.js required.
    Say "Installing Claude Code via the official installer..."
    Write-Host ""
    $shell = Get-HostShell
    try {
        # Out-Host keeps the installer's progress visible without letting it
        # leak into this function's return value.
        & $shell -ExecutionPolicy Bypass -NoProfile -Command "irm https://claude.ai/install.ps1 | iex" 2>&1 | Out-Host
        log "Native installer exit: $LASTEXITCODE"
    }
    catch {
        SayWarn "Native installer failed: $_"
    }

    Refresh-Path
    if (Test-Command "claude") {
        SayOk "Claude Code installed"
        return $true
    }

    # The installer may have placed the launcher somewhere not yet on PATH.
    $exe = Find-ClaudeExe
    if ($exe) {
        $env:PATH = (Split-Path $exe) + ";" + $env:PATH
        if (Test-Command "claude") {
            SayOk "Claude Code installed - found at $exe"
            return $true
        }
    }

    # 2) npm fallback, only when Node is already available.
    if (Test-Command "npm") {
        Write-Host ""
        SayWarn "Native install did not produce a usable claude command - trying npm..."
        $out = Invoke-Native "npm" @("install", "-g", "@anthropic-ai/claude-code")
        if ($out.Trim()) { Write-Host $out.Trim() }
        Refresh-Path
        if (Test-Command "claude") {
            SayOk "Claude Code installed via npm"
            return $true
        }
    }

    return $false
}

function Show-DepsManualInstructions {
    Write-Host ""
    Write-Host "  Manual installation:"
    Write-Host ""
    Write-Host "    Python 3.12:"
    Write-Host "      winget install Python.Python.3.12"
    Write-Host "      (or download from https://www.python.org/downloads/)"
    Write-Host ""
    Write-Host "    uv:"
    Write-Host "      irm https://astral.sh/uv/install.ps1 | iex"
    Write-Host "      (or: pip install uv)"
    Write-Host ""
    Write-Host "  Then open a new terminal and re-run this script. The dependency"
    Write-Host "  check will pass and installation continues from Step 3."
    Write-Host ""
}

function Show-ClaudeManualInstructions {
    Write-Host ""
    Write-Host "  Install Claude Code with either:"
    Write-Host "    irm https://claude.ai/install.ps1 | iex"
    Write-Host "    npm install -g @anthropic-ai/claude-code"
    Write-Host ""
    Write-Host "  Then open a new terminal and re-run this script."
    Write-Host ""
}

# Run a native command and capture stdout+stderr as plain text.
# ErrorActionPreference is forced to Continue so PowerShell 5.1 does not turn
# stderr lines into terminating NativeCommandErrors. Sets $script:LastNativeExit.
function Invoke-Native {
    param(
        [string]   $Exe,
        [string[]] $Arguments = @()
    )
    log "RUN: $Exe $($Arguments -join ' ')"
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $global:LASTEXITCODE = 0
    try {
        # Flatten ErrorRecords to their plain message so captured stderr does
        # not come back wrapped in PowerShell's "At line:N char:N" decoration.
        $output = & $Exe @Arguments 2>&1 |
            ForEach-Object {
                if ($_ -is [System.Management.Automation.ErrorRecord]) { $_.ToString() } else { "$_" }
            } | Out-String
        $script:LastNativeExit = $LASTEXITCODE
    }
    catch {
        $output = "$_"
        $script:LastNativeExit = 127
    }
    finally {
        $ErrorActionPreference = $prevEap
    }
    log "EXIT $script:LastNativeExit :: $($output.Trim())"
    return $output
}

function Test-LockError {
    param([string] $Text)
    return ($Text -match "EACCES|EPERM|permission denied|being used by another process|Access to the path")
}

function Get-AncestorPids {
    # Never kill ourselves or anything we are running inside of - this script
    # lives in a path containing "claude-telemetry", so a naive command-line
    # match would otherwise target the current shell.
    $ids = @()
    $cur = $PID
    for ($i = 0; $i -lt 12 -and $cur -gt 0; $i++) {
        $ids += [int] $cur
        $proc = Get-CimInstance Win32_Process -Filter "ProcessId = $cur" -ErrorAction SilentlyContinue
        if (-not $proc) { break }
        $cur = [int] $proc.ParentProcessId
    }
    return $ids   # callers wrap in @() - do not comma-wrap, it nests the array
}

function Release-FileLock {
    Say "Releasing file locks held by plugin processes..."
    $protected = @(Get-AncestorPids)
    $targets   = @("python.exe", "pythonw.exe", "uv.exe", "uvx.exe", "node.exe")

    try {
        $procs = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
            Where-Object {
                $targets -contains $_.Name -and
                $_.CommandLine -and
                ($_.CommandLine -like "*claude-telemetry*" -or $_.CommandLine -like "*cloudbyte*") -and
                $protected -notcontains [int] $_.ProcessId
            })
        foreach ($p in $procs) {
            Say "  Stopping PID $($p.ProcessId) ($($p.Name))"
            Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
        }
        if (-not $procs) { Say "  No matching processes found" }
    } catch {
        SayWarn "Could not enumerate processes: $_"
    }
    Start-Sleep -Seconds 2
}

# Run a claude CLI command, retrying once after releasing file locks.
# Treats "already exists" style output as success.
function Invoke-ClaudeStep {
    param(
        [string[]] $Arguments,
        [string]   $What
    )
    $out  = Invoke-Native "claude" $Arguments
    $exit = $script:LastNativeExit
    if ($out.Trim()) { Write-Host $out.Trim() }

    if (Test-LockError $out) {
        Write-Host ""
        SayWarn "Permission / file lock error while $What"
        Release-FileLock
        Say "Retrying..."
        $out  = Invoke-Native "claude" $Arguments
        $exit = $script:LastNativeExit
        if ($out.Trim()) { Write-Host $out.Trim() }
    }

    if ($exit -ne 0 -and ($out -notmatch "(?i)already|exists")) {
        return $false
    }
    return $true
}

function Get-LatestPluginDir {
    $base = Join-Path $CLAUDE_DIR "plugins\cache\claude-telemetry\claude-telemetry"
    if (-not (Test-Path $base)) { return $null }

    $dirs = Get-ChildItem -Path $base -Directory -ErrorAction SilentlyContinue
    if (-not $dirs) { return $null }

    $ranked = $dirs | ForEach-Object {
        $parsed = $null
        $clean  = $_.Name -replace '^[vV]', ''
        if (-not [version]::TryParse($clean, [ref] $parsed)) { $parsed = [version] "0.0.0" }
        [pscustomobject] @{ Dir = $_; Version = $parsed; Name = $_.Name }
    } | Sort-Object Version, Name

    return $ranked[-1].Dir.FullName
}

# ── Banner ─────────────────────────────────────────────────────────────────────

Write-Host ""
Write-Host "======================================================"
Write-Host "         CloudByte Plugin Installer (Windows)"
Write-Host "======================================================"
Write-Host ""
Write-Host "  Log file: $LOG_FILE"
log "=== CloudByte plugin installer started ==="
log "Marketplace: $MarketplaceUrl | Plugin: $PluginRef"

# ── Step 1: Claude Code CLI ────────────────────────────────────────────────────

Header "Step 1: Checking Claude Code CLI"

if (-not (Test-Command "claude")) {
    Refresh-Path
}

if (-not (Test-Command "claude")) {
    SayFail "Claude Code CLI not found on PATH"
    Write-Host ""
    Write-Host "  The plugin installs through the Claude Code CLI, so it has to be"
    Write-Host "  present before the marketplace and plugin steps can run."
    Write-Host ""

    switch (Get-DependencyChoice "Claude Code") {
        "blocked" {
            Write-Host "  Running non-interactively and -Yes was not supplied."
            Show-ClaudeManualInstructions
            log "Claude CLI missing, non-interactive without -Yes - aborting"
            exit 2
        }
        "manual" {
            Show-ClaudeManualInstructions
            log "User chose manual Claude CLI installation"
            exit 2
        }
        "auto" {
            Write-Host ""
            if (-not (Install-ClaudeCode)) {
                Write-Host ""
                SayFail "Could not install the Claude Code CLI automatically."
                Show-ClaudeManualInstructions
                log "Automatic Claude CLI install failed - aborting"
                exit 2
            }
        }
    }
}

$claudeVersion = (Invoke-Native "claude" @("--version")).Trim()
if (-not $claudeVersion) { $claudeVersion = "version unknown" }
SayOk "Claude Code CLI ready - $claudeVersion"

# ── Step 2: Python and uv ──────────────────────────────────────────────────────

Header "Step 2: Checking Python and uv"

$pythonOk = Test-PythonPresent
$uvOk     = Test-Command "uv"

if ($pythonOk) { SayOk "Python found" }  else { SayFail "Python not found" }
if ($uvOk)     { SayOk "uv found" }      else { SayFail "uv not found" }

$installDeps = $false

if (-not $pythonOk -or -not $uvOk) {
    Write-Host ""
    Write-Host "  Python and uv are both required by the CloudByte plugin."
    Write-Host ""

    switch (Get-DependencyChoice "Python and uv") {
        "blocked" {
            Write-Host "  Running non-interactively and -Yes was not supplied."
            Show-DepsManualInstructions
            log "Deps missing, non-interactive without -Yes - aborting"
            exit 3
        }
        "manual" {
            Show-DepsManualInstructions
            log "User chose manual dependency install"
            exit 3
        }
        "auto" {
            # validate.ps1 in Step 3 performs the actual Python/uv install.
            $installDeps = $true
            log "Automatic dependency install selected"
        }
    }
}
else {
    Write-Host ""
    Say "All dependencies present."
}

# ── Step 3: Prerequisites validation ───────────────────────────────────────────

if ($SkipPrereqs -and -not $installDeps) {
    Header "Step 3: Prerequisites (skipped)"
    SayWarn "-SkipPrereqs supplied - not running validate.ps1"
}
else {
    Header "Step 3: Running Prerequisites Validation"

    # $PSScriptRoot is empty when the script is piped into iex - download then.
    $localValidate = $null
    if ($PSScriptRoot) { $localValidate = Join-Path $PSScriptRoot "validate.ps1" }
    $validatePath  = $null
    $tempDir       = $null

    if ($localValidate -and ($UseLocalValidate -or (Test-Path $localValidate))) {
        $validatePath = $localValidate
        Say "Using local validate.ps1: $validatePath"
    }
    else {
        $tempDir = Join-Path $env:TEMP ("cloudbyte-install-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
        New-Item -ItemType Directory -Force -Path $tempDir | Out-Null
        $validatePath = Join-Path $tempDir "validate.ps1"
        Say "Downloading validate.ps1..."
        try {
            [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
            Invoke-WebRequest -Uri "$RAW_BASE/validate.ps1" -OutFile $validatePath -UseBasicParsing
        }
        catch {
            log "Download failed: $_"
            Remove-Item $tempDir -Recurse -Force -ErrorAction SilentlyContinue
            Fail-Exit "Failed to download validate.ps1 - check your internet connection." 4
        }
    }

    $shell = Get-HostShell
    & $shell -ExecutionPolicy Bypass -NoProfile -File $validatePath
    $prereqExit = $LASTEXITCODE

    if ($tempDir) { Remove-Item $tempDir -Recurse -Force -ErrorAction SilentlyContinue }

    if ($prereqExit -ne 0) {
        Fail-Exit "Prerequisites failed (exit: $prereqExit) - fix the issue above and re-run this script." 4
    }

    # validate.ps1 ran in a child process; pick up any PATH it created.
    Refresh-Path

    Write-Host ""
    SayOk "Prerequisites ready"

    if (-not (Test-PythonPresent)) {
        SayWarn "Python still not visible on this session's PATH - open a new terminal and re-run."
    }
    if (-not (Test-Command "uv")) {
        SayWarn "uv still not visible on this session's PATH - open a new terminal and re-run."
    }
}

# ── Step 4: Add marketplace ────────────────────────────────────────────────────

Header "Step 4: Adding Marketplace"

$mpArgs = @("plugin", "marketplace", "add", $MarketplaceUrl)
if (-not (Invoke-ClaudeStep $mpArgs "adding the marketplace")) {
    Fail-Exit "Failed to add marketplace." 5
}
Write-Host ""
SayOk "Marketplace added"

# ── Step 5: Install plugin ─────────────────────────────────────────────────────

Header "Step 5: Installing Plugin"

$instArgs = @("plugin", "install", $PluginRef)
if (-not (Invoke-ClaudeStep $instArgs "installing the plugin")) {
    Fail-Exit "Plugin install failed." 6
}
Write-Host ""
SayOk "Plugin installed"

# ── Step 6: Plugin environment ─────────────────────────────────────────────────

Header "Step 6: Preparing Plugin Environment"

$pluginDir = Get-LatestPluginDir

if (-not $pluginDir) {
    SayWarn "Plugin cache directory not found - environment will be built on first run."
}
elseif (-not (Test-Command "uv")) {
    SayWarn "uv is not on this session's PATH - skipping 'uv sync'."
    Write-Host "       Open a new terminal and run:"
    Write-Host "         uv sync --frozen --python 3.12 --directory `"$pluginDir`""
}
else {
    Say "Plugin directory: $pluginDir"
    Say "Syncing dependencies (this can take a minute on first run)..."

    $syncArgs = @("sync", "--frozen", "--python", "3.12", "--directory", $pluginDir)

    # An inherited VIRTUAL_ENV from the caller's shell makes uv warn and target
    # the wrong environment - hide it for the duration of the sync.
    $savedVenv = $env:VIRTUAL_ENV
    Remove-Item Env:VIRTUAL_ENV -ErrorAction SilentlyContinue
    try {
        $syncOut = Invoke-Native "uv" $syncArgs
        if ($syncOut.Trim()) { Write-Host $syncOut.Trim() }

        # A live Claude session running the MCP server holds .venv open, which
        # surfaces as "Access is denied" while uv rebuilds it.
        if ($script:LastNativeExit -ne 0 -and (Test-LockError $syncOut)) {
            Write-Host ""
            SayWarn "The plugin virtualenv is locked by a running Claude/MCP process."

            $doKill = $false
            if ($Yes)                   { $doKill = $true }
            elseif (-not $NonInteractive) {
                Write-Host ""
                Write-Host "  Those processes belong to Claude sessions that are still open."
                Write-Host "  They have to restart anyway to pick up the plugin."
                Write-Host ""
                $ans = (Read-Host "  Stop them and retry the sync? [Y/n]").Trim()
                $doKill = ($ans -eq "" -or $ans -match "^(y|yes)$")
            }

            if ($doKill) {
                Release-FileLock
                Say "Retrying sync..."
                $syncOut = Invoke-Native "uv" $syncArgs
                if ($syncOut.Trim()) { Write-Host $syncOut.Trim() }
            }
        }
    }
    finally {
        if ($savedVenv) { $env:VIRTUAL_ENV = $savedVenv }
    }

    if ($script:LastNativeExit -eq 0) {
        SayOk "Plugin environment ready"
    }
    else {
        SayWarn "Environment setup incomplete - the MCP server may need a reconnect on first start."
        Write-Host "       You can finish it later with:"
        Write-Host "         uv sync --frozen --python 3.12 --directory `"$pluginDir`""
    }
}

# ── Step 7: Activation, dashboard, summary ─────────────────────────────────────

Header "Step 7: Activate the Plugin (manual)"

Write-Host "  The plugin is installed but not yet active in running sessions."
Write-Host ""
Write-Host "  Quickest path:"
Write-Host "    In Claude Code, type:  /reload-plugins"
Write-Host ""
Write-Host "  If tools do not appear or you see an MCP error, restart instead:"
Write-Host "    Claude Code CLI : Ctrl+C, then  claude --resume <session-id>"
Write-Host "    VS Code/Desktop : close the Claude panel, reopen it, resume the session"
Write-Host ""
Write-Host "  Note: /reload-plugins only affects the current session. Any other"
Write-Host "        open Claude sessions need their own restart."
Write-Host ""

if ($OpenDashboard) {
    Say "Opening dashboard..."
    Start-Process $DashboardUrl -ErrorAction SilentlyContinue
}

Write-Host ""
Write-Host "======================================================"
Write-Host "            [OK] CloudByte is Ready!"
Write-Host "======================================================"
Write-Host ""
Write-Host "  Dashboard  ->  $DashboardUrl"
Write-Host "  Logs       ->  $CLOUDBYTE_DIR\logs\"
Write-Host ""
Write-Host "  In the dashboard you will find:"
Write-Host "    Sessions     - every Claude session with start/end times"
Write-Host "    Prompts      - every prompt sent, with token counts"
Write-Host "    Observations - technical notes Claude recorded about your work"
Write-Host "    Timeline     - full activity history across sessions"
Write-Host ""
Write-Host "  After activation the plugin records automatically in the background."
Write-Host ""
Write-Host "======================================================"
Write-Host ""

log "=== Installer finished successfully ==="
exit 0
