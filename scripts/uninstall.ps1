#Requires -Version 5.1
<#
.SYNOPSIS
    CloudByte telemetry plugin uninstaller for Windows - Claude Code and Cursor.

.DESCRIPTION
    Removes the telemetry plugin from the editors that have it, and optionally
    removes the marketplace entry and the captured data.

      Step 1  - Detect which editors have the plugin, ask which to remove from
      Step 2  - Uninstall the plugin
      Step 3  - Ask whether to remove the marketplace entry
      Step 4  - Ask whether to delete the captured data (~/.cloudbyte)
      Step 5  - Summary

    Nothing is removed without being asked for, and every prompt defaults to the
    conservative answer. The two destructive answers - removing the marketplace
    and deleting the data - are No by default.

    Editor differences, the mirror image of the installer's:

      Claude Code  The CLI can uninstall, so Step 2 is automatic:
                     claude plugin uninstall claude-telemetry@claude-telemetry -y
                   -y is mandatory, not cosmetic: the CLI refuses to run when
                   stdout is not a TTY, and this script captures its output.

      Cursor       The CLI has no uninstall verb, exactly as it has no install
                   verb. Step 2 prints the IDE steps (Settings > Plugins >
                   cursor-telemetry > Uninstall) and asks you to confirm once
                   you have done them. It does not sit on a timer, and it does
                   not second-guess the answer against the cache directory - see
                   Confirm-ManualStep for why neither is right here.

    About deleting the data. ~/.cloudbyte holds the SQLite database with every
    captured session, prompt and observation, plus logs and session state. The
    delete is permanent and there is no backup step, so the prompt says so
    plainly and defaults to No.

    Two things make the delete non-trivial on Windows:

      * The database and the plugin virtualenv are held open by live processes -
        the dashboard/worker on port 4723, the MCP servers, and hook processes.
        Windows refuses to delete a file with an open handle, so those processes
        are stopped first. On POSIX this is not required for the delete to
        succeed; the Bash version explains why it stops them anyway.
      * The worker writes ~/.cloudbyte/worker.pid, but that file is not reliably
        present even when the worker is running, so it is only the first of three
        strategies: PID file, then port 4723, then a command-line match. This
        mirrors src/workers/kill_worker.py, which has the same fallback chain
        for the same reason.

    The plugin cache directories left behind under
    ~/.claude/plugins/cache/... are not touched. Claude Code prunes those
    itself, and second-guessing it risks deleting a checkout it still tracks.

.PARAMETER Target
    Which editors to remove from: auto (default), ask, claude, cursor, or both.
    auto takes every editor that actually has the plugin. An explicit value
    skips the selection prompt.

.PARAMETER Yes
    Never prompt. Uninstalls the plugin from every detected editor and, because
    silence must not be read as consent for a destructive action, KEEPS both the
    marketplace entry and the data. Use -RemoveMarketplace and -DeleteData to
    opt into those explicitly.

.PARAMETER NonInteractive
    Same as -Yes.

.PARAMETER RemoveMarketplace
    Remove the marketplace entry without asking.

.PARAMETER KeepMarketplace
    Keep the marketplace entry without asking.

.PARAMETER DeleteData
    Delete ~/.cloudbyte without asking. This is permanent.

.PARAMETER KeepData
    Keep ~/.cloudbyte without asking.

.PARAMETER CursorDir
    Cursor's data directory. Defaults to ~/.cursor.

.PARAMETER MarketplaceUrl
    Marketplace repository URL, used only to report what would be removed.

.PARAMETER PluginRef
    Plugin reference in <plugin>@<marketplace> form.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\scripts\uninstall.ps1

.EXAMPLE
    & ([scriptblock]::Create((irm https://raw.githubusercontent.com/CloudByte-AI/claude-telemetry/main/scripts/bootstrap.ps1))) -Script uninstall.ps1

.EXAMPLE
    # Fully unattended, remove everything including the data
    .\scripts\uninstall.ps1 -Yes -RemoveMarketplace -DeleteData

.NOTES
    Exit codes:
      0  success (including "nothing was installed")
      1  unexpected failure
      2  the plugin is not installed in any editor matching -Target
      5  marketplace removal failed for every targeted editor
      6  plugin uninstall failed for every editor that supports CLI removal
      7  the data directory could not be fully deleted
#>

[CmdletBinding()]
param(
    [switch] $Yes,
    [switch] $NonInteractive,

    [switch] $RemoveMarketplace,
    [switch] $KeepMarketplace,
    [switch] $DeleteData,
    [switch] $KeepData,

    [ValidateSet("auto", "ask", "claude", "cursor", "both")]
    [string] $Target             = "auto",
    [string] $CursorDir          = "",

    [string] $MarketplaceUrl = "https://github.com/CloudByte-AI/claude-telemetry",
    [string] $PluginRef      = "claude-telemetry@claude-telemetry"
)

$ErrorActionPreference = "Stop"

if ($RemoveMarketplace -and $KeepMarketplace) {
    Write-Host "[FAIL] -RemoveMarketplace and -KeepMarketplace contradict each other." -ForegroundColor Red
    exit 1
}
if ($DeleteData -and $KeepData) {
    Write-Host "[FAIL] -DeleteData and -KeepData contradict each other." -ForegroundColor Red
    exit 1
}

# ── Paths and logging ──────────────────────────────────────────────────────────

$USER_HOME      = $env:USERPROFILE
$CLOUDBYTE_DIR  = Join-Path $USER_HOME ".cloudbyte"
$SETUP_LOG_DIR  = Join-Path $CLOUDBYTE_DIR "logs\setup"
$DASHBOARD_PORT = 4723

if ($env:CLAUDE_CONFIG_DIR) { $CLAUDE_DIR = $env:CLAUDE_CONFIG_DIR }
else                        { $CLAUDE_DIR = Join-Path $USER_HOME ".claude" }

if ($CursorDir)             { $CURSOR_DIR = $CursorDir }
elseif ($env:CURSOR_DIR)    { $CURSOR_DIR = $env:CURSOR_DIR }
else                        { $CURSOR_DIR = Join-Path $USER_HOME ".cursor" }

# The log lives inside the directory this script may be about to delete, so it
# is written to TEMP once deletion has been agreed to. Until then, the normal
# location is used so an aborted run leaves its trail in the usual place.
$LOG_DIR = $SETUP_LOG_DIR
New-Item -ItemType Directory -Force -Path $LOG_DIR -ErrorAction SilentlyContinue | Out-Null
$LOG_FILE = Join-Path $LOG_DIR ("uninstall-" + (Get-Date -Format "yyyy-MM-dd") + ".log")

function log {
    param([string] $Message)
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -Path $LOG_FILE -Value "[$ts] $Message" -Encoding utf8 -ErrorAction SilentlyContinue
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
    Write-Host "  Full logs      : $LOG_DIR"
    Write-Host "  Troubleshooting: https://github.com/CloudByte-AI/claude-telemetry/issues"
    Write-Host ""
    exit $Code
}

# ── Helpers ────────────────────────────────────────────────────────────────────

function Refresh-Path {
    $current = $env:PATH
    $machine = [System.Environment]::GetEnvironmentVariable("PATH", "Machine")
    $user    = [System.Environment]::GetEnvironmentVariable("PATH", "User")
    $extra   = @(
        "$env:USERPROFILE\.local\bin"
        "$env:USERPROFILE\.cargo\bin"
        "$env:LOCALAPPDATA\cursor-agent"
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

function Test-AuthError {
    param([string] $Text)
    return ($Text -match "(?i)authentication required|not (logged in|authenticated)|unauthorized|\b401\b|please (log ?in|sign ?in)|CURSOR_API_KEY")
}

# "not installed" is the desired end state, so a CLI that says so has succeeded.
# This is the mirror of the installer treating "already exists" as success.
function Test-AlreadyGone {
    param([string] $Text)
    return ($Text -match "(?i)not installed|not found|no such plugin|is not currently installed|does not exist|no plugin")
}

function Test-CanPrompt {
    if ($Yes -or $NonInteractive) { return $false }
    try {
        if ([Console]::IsInputRedirected) { return $false }
    }
    catch {
        return $false
    }
    if (-not [Environment]::UserInteractive) { return $false }
    return $true
}

# Yes/No question with an explicit default used for every non-interactive path.
function Ask-YesNo {
    param(
        [string] $Question,
        [bool]   $Default = $false
    )
    if (-not (Test-CanPrompt)) { return $Default }

    $hint = if ($Default) { "[Y/n]" } else { "[y/N]" }
    for ($try = 0; $try -lt 3; $try++) {
        $raw = ""
        try { $raw = ("" + (Read-Host "  $Question $hint")).Trim() }
        catch { return $Default }

        if ($raw -eq "") { return $Default }
        if ($raw -match "^(y|yes)$") { return $true }
        if ($raw -match "^(n|no)$")  { return $false }
        Write-Host "  Please answer y or n."
    }
    return $Default
}

# Same menu shape as the installer, but only editors that actually HAVE the
# plugin are listed - there is nothing to choose about an editor with nothing
# installed.
function Select-Editors {
    param(
        [array]    $Candidates,
        [string[]] $DefaultKeys
    )

    $defaults     = @($Candidates | Where-Object { $DefaultKeys -contains $_.Key })
    $defaultLabel = (@($defaults | ForEach-Object { $_.Name }) -join ', ')
    if (-not $defaultLabel) { $defaultLabel = "none" }

    Write-Host ""
    Write-Host "  Remove the plugin from which editors?"
    Write-Host ""
    for ($i = 0; $i -lt $Candidates.Count; $i++) {
        $c    = $Candidates[$i]
        $mark = if ($DefaultKeys -contains $c.Key) { "*" } else { " " }
        Write-Host ("   {0} {1}) {2,-12}  {3}" -f $mark, ($i + 1), $c.Name, $c.Note)
    }
    Write-Host ""
    Write-Host "  Enter numbers separated by commas (for example: 1,2),"
    Write-Host "  'a' for all, or press Enter for the default (*): $defaultLabel"
    Write-Host ""

    for ($try = 0; $try -lt 3; $try++) {
        $raw = ""
        try { $raw = ("" + (Read-Host "  Selection")).Trim() }
        catch {
            SayWarn "No input available - using the default."
            return $defaults
        }

        if ($raw -eq "")             { return $defaults }
        if ($raw -match "^(a|all)$") { return $Candidates }

        $keys   = New-Object System.Collections.Generic.List[string]
        $picked = New-Object System.Collections.Generic.List[object]
        $bad    = $false
        foreach ($tok in @($raw -split "[,\s]+" | Where-Object { $_ })) {
            $n = 0
            if (-not [int]::TryParse($tok, [ref] $n) -or $n -lt 1 -or $n -gt $Candidates.Count) {
                $bad = $true
                break
            }
            $c = $Candidates[$n - 1]
            if (-not $keys.Contains($c.Key)) { [void] $keys.Add($c.Key); [void] $picked.Add($c) }
        }

        if (-not $bad -and $picked.Count -gt 0) { return $picked.ToArray() }
        Write-Host "  Not a valid selection - use numbers between 1 and $($Candidates.Count)."
    }

    SayWarn "No valid selection after 3 attempts - using the default."
    return $defaults
}

# Is there a plugin checkout under this cache root? Any version directory counts
# - the question is only "is something installed", not "which version".
function Test-PluginCache {
    param([string] $Root)
    if (-not (Test-Path $Root)) { return $false }
    return [bool] @(Get-ChildItem -Path $Root -Directory -ErrorAction SilentlyContinue).Count
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
function Confirm-ManualStep {
    param([string] $CacheRoot)

    if (-not (Test-PluginCache $CacheRoot)) { return $true }

    if (-not (Test-CanPrompt)) {
        SayWarn "Running unattended - cannot confirm the manual step above."
        return $false
    }

    $raw = ""
    try { $raw = ("" + (Read-Host "  Press Enter once you have done this (or type s to skip)")).Trim() }
    catch {
        SayWarn "No input available - skipping."
        return $false
    }

    if ($raw -match "^(s|skip|n|no|q|quit)$") {
        Say "  Skipped."
        return $false
    }
    return $true
}

# ── Stopping what holds the data open ──────────────────────────────────────────

function Get-AncestorPids {
    # Never kill ourselves or anything we are running inside of - this script
    # can live in a path containing "claude-telemetry", so a naive command-line
    # match would otherwise target the current shell.
    $ids = @()
    $cur = $PID
    for ($i = 0; $i -lt 12 -and $cur -gt 0; $i++) {
        $ids += [int] $cur
        $proc = Get-CimInstance Win32_Process -Filter "ProcessId = $cur" -ErrorAction SilentlyContinue
        if (-not $proc) { break }
        $cur = [int] $proc.ParentProcessId
    }
    return $ids
}

function Stop-OnePid {
    param([int] $ProcessId, [string] $Why)
    try {
        $p = Get-Process -Id $ProcessId -ErrorAction Stop
        Say "  Stopping PID $ProcessId ($($p.ProcessName)) - $Why"
        Stop-Process -Id $ProcessId -Force -ErrorAction Stop
        return $true
    }
    catch {
        log "Could not stop PID $ProcessId : $_"
        return $false
    }
}

# Strategy 1: the PID file the worker is supposed to write.
function Stop-WorkerByPidFile {
    param([int[]] $Protected)
    $pidFile = Join-Path $CLOUDBYTE_DIR "worker.pid"
    if (-not (Test-Path $pidFile)) {
        log "No worker.pid present"
        return 0
    }
    $stopped = 0
    try {
        $data = Get-Content $pidFile -Raw | ConvertFrom-Json
        $wpid = [int] $data.pid
        if ($wpid -gt 0 -and $Protected -notcontains $wpid) {
            if (Stop-OnePid $wpid "worker.pid") { $stopped++ }
        }
    }
    catch {
        SayWarn "worker.pid could not be read ($($_.Exception.Message)) - falling back to the port scan"
    }
    return $stopped
}

# Strategy 2: whatever is listening on the dashboard port. This is the strategy
# that actually finds the worker in practice, because worker.pid is not reliably
# written even when the worker is running.
function Stop-WorkerByPort {
    param([int[]] $Protected)
    $stopped = 0
    $pids    = @()

    try {
        $conns = Get-NetTCPConnection -LocalPort $DASHBOARD_PORT -State Listen -ErrorAction Stop
        $pids  = @($conns | ForEach-Object { [int] $_.OwningProcess })
    }
    catch {
        # Get-NetTCPConnection is absent on older/Core-less hosts; netstat is not.
        foreach ($line in (netstat -ano 2>$null)) {
            if ($line -match ":$DASHBOARD_PORT\s" -and $line -match "LISTENING") {
                $parts = $line -split "\s+" | Where-Object { $_ }
                $last  = $parts[-1]
                if ($last -match "^\d+$") { $pids += [int] $last }
            }
        }
    }

    foreach ($p in ($pids | Select-Object -Unique)) {
        if ($Protected -contains $p) { continue }
        if (Stop-OnePid $p "listening on $DASHBOARD_PORT") { $stopped++ }
    }
    return $stopped
}

# Strategy 3: anything whose command line points at the plugin or its data.
function Stop-PluginProcesses {
    param([int[]] $Protected)
    $stopped = 0
    $targets = @("python.exe", "pythonw.exe", "uv.exe", "uvx.exe", "uvicorn.exe", "node.exe")
    try {
        $procs = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
            Where-Object {
                $targets -contains $_.Name -and
                $_.CommandLine -and
                ($_.CommandLine -like "*claude-telemetry*" -or
                 $_.CommandLine -like "*cursor-telemetry*" -or
                 $_.CommandLine -like "*cloudbyte*" -or
                 $_.CommandLine -like "*start_mcp*") -and
                $Protected -notcontains [int] $_.ProcessId
            })
        foreach ($p in $procs) {
            if (Stop-OnePid ([int] $p.ProcessId) "holds the plugin or its data open") { $stopped++ }
        }
    }
    catch {
        SayWarn "Could not enumerate processes: $_"
    }
    return $stopped
}

function Stop-Everything {
    Say "Closing anything still using that data..."
    $protected = @(Get-AncestorPids)
    $n  = 0
    $n += Stop-WorkerByPidFile $protected
    $n += Stop-WorkerByPort    $protected
    $n += Stop-PluginProcesses $protected

    if ($n -eq 0) { Say "  Nothing was running" }
    else          { Say "  Stopped $n process(es)" }

    # Handles are released asynchronously; give Windows a moment before deleting.
    Start-Sleep -Seconds 2
    return $n
}

function Format-Size {
    param([long] $Bytes)
    if ($Bytes -ge 1GB) { return ("{0:N1} GB" -f ($Bytes / 1GB)) }
    if ($Bytes -ge 1MB) { return ("{0:N0} MB" -f ($Bytes / 1MB)) }
    if ($Bytes -ge 1KB) { return ("{0:N0} KB" -f ($Bytes / 1KB)) }
    return "$Bytes bytes"
}

function Get-DirSize {
    param([string] $Path)
    try {
        $sum = (Get-ChildItem -Path $Path -Recurse -File -Force -ErrorAction SilentlyContinue |
                Measure-Object -Property Length -Sum).Sum
        if (-not $sum) { return 0 }
        return [long] $sum
    }
    catch { return 0 }
}

# ── Banner ─────────────────────────────────────────────────────────────────────

Write-Host ""
Write-Host "======================================================"
Write-Host "       CloudByte Plugin Uninstaller (Windows)"
Write-Host "======================================================"
Write-Host ""
Write-Host "  Log file: $LOG_FILE"
log "=== CloudByte plugin uninstaller started ==="
log "Target=$Target Plugin=$PluginRef"

# ── Step 1: Detect what is installed ───────────────────────────────────────────

Header "Step 1: Detecting Installed Plugins"

if (-not (Test-Command "claude") -or -not (Test-Command "cursor-agent")) {
    Refresh-Path
}

$claudeCli = Test-Command "claude"
$cursorCli = Test-Command "cursor-agent"

$CLAUDE_CACHE = Join-Path $CLAUDE_DIR "plugins\cache\claude-telemetry\claude-telemetry"
$CURSOR_CACHE = Join-Path $CURSOR_DIR "plugins\cache\cursor-telemetry\cursor-telemetry"

# Two independent signals, because either can be true alone: the CLI listing is
# authoritative about registration, the cache directory is evidence on disk that
# survives a half-finished uninstall.
$claudeListed = $false
if ($claudeCli) {
    $listOut = Invoke-Native "claude" @("plugin", "list")
    $claudeListed = ($listOut -match "claude-telemetry")
}
$claudeCached = Test-PluginCache $CLAUDE_CACHE
$cursorCached = Test-PluginCache $CURSOR_CACHE

$claudeHas = $claudeListed -or $claudeCached
$cursorHas = $cursorCached

if ($claudeHas) {
    $how = if ($claudeListed) { "installed" } else { "not fully removed - its files are still on disk" }
    SayOk "Claude Code: $how"
} else {
    Say "Claude Code: not installed"
}
if ($cursorHas) { SayOk "Cursor: installed" }
else            { Say "Cursor: not installed" }

if (-not $claudeCli -and $claudeHas) {
    SayWarn "The claude CLI is not on PATH, so its plugin cannot be uninstalled automatically."
}

switch ($Target) {
    "claude" { $wantClaude = $true;  $wantCursor = $false }
    "cursor" { $wantClaude = $false; $wantCursor = $true }
    "both"   { $wantClaude = $true;  $wantCursor = $true }
    default  {
        $wantClaude = $claudeHas
        $wantCursor = $cursorHas
    }
}
log "Target=$Target -> default claude=$wantClaude cursor=$wantCursor"

# Nothing installed anywhere is a successful no-op, not a failure.
if (-not $claudeHas -and -not $cursorHas) {
    Write-Host ""
    SayOk "The plugin is not installed in either editor - nothing to remove."
    Write-Host ""
    Write-Host "  If you only want to delete the data it captured, remove this folder:"
    Write-Host "    Remove-Item -LiteralPath `"$CLOUDBYTE_DIR`" -Recurse -Force"
    Write-Host ""
    log "Nothing installed - exiting 0"
    exit 0
}

# Only editors that actually have the plugin are offered.
$candidates = @()
if ($claudeHas) {
    $note = if ($claudeCli) { "installed" } else { "installed - but the claude CLI is missing" }
    $candidates += [pscustomobject] @{ Key = "claude"; Name = "Claude Code"; Note = $note }
}
if ($cursorHas) {
    $candidates += [pscustomobject] @{ Key = "cursor"; Name = "Cursor"; Note = "installed - needs a manual step in the IDE" }
}

$defaultKeys = @()
if ($wantClaude -and $claudeHas) { $defaultKeys += "claude" }
if ($wantCursor -and $cursorHas) { $defaultKeys += "cursor" }

$explicitTarget = $PSBoundParameters.ContainsKey("Target") -and $Target -ne "ask"

# Only worth asking when there is more than one thing to choose between.
if (-not $explicitTarget -and $candidates.Count -gt 1 -and (Test-CanPrompt)) {
    $chosen     = @(Select-Editors -Candidates $candidates -DefaultKeys $defaultKeys)
    $chosenKeys = @($chosen | ForEach-Object { $_.Key })
    $wantClaude = $chosenKeys -contains "claude"
    $wantCursor = $chosenKeys -contains "cursor"
    Write-Host ""
    SayOk "Selected: $(@($chosen | ForEach-Object { $_.Name }) -join ', ')"
    log "User selected editors: $($chosenKeys -join ',')"
}
elseif (-not $explicitTarget) {
    $wantClaude = $claudeHas
    $wantCursor = $cursorHas
    # Display names, not the internal keys - "claude cursor" means nothing to a user.
    $removingNames = @($defaultKeys | ForEach-Object {
        if ($_ -eq "claude") { "Claude Code" } else { "Cursor" }
    }) -join ', '
    Say "Removing from: $removingNames"
}

# Asking for an editor that has nothing installed is a no-op for that editor.
if ($wantClaude -and -not $claudeHas) {
    SayWarn "Claude Code does not have the plugin - skipping it."
    $wantClaude = $false
}
if ($wantCursor -and -not $cursorHas) {
    SayWarn "Cursor does not have the plugin - skipping it."
    $wantCursor = $false
}

if (-not $wantClaude -and -not $wantCursor) {
    log "No targeted editor has the plugin - aborting"
    Fail-Exit "The plugin is not installed in any editor matching -Target $Target." 2
}

$EDITORS = @()
if ($wantClaude) {
    $EDITORS += [pscustomobject] @{
        Key             = "claude"
        Name            = "Claude Code"
        Exe             = "claude"
        CliAvailable    = $claudeCli
        CacheRoot       = $CLAUDE_CACHE
        PluginRef       = $PluginRef
        MarketplaceName = "claude-telemetry"
        CanUninstallCli = $true
        PluginRemoved   = $false
        MarketplaceGone = $false
        AuthRequired    = $false
    }
}
if ($wantCursor) {
    $EDITORS += [pscustomobject] @{
        Key             = "cursor"
        Name            = "Cursor"
        Exe             = "cursor-agent"
        CliAvailable    = $cursorCli
        CacheRoot       = $CURSOR_CACHE
        PluginRef       = "cursor-telemetry@cursor-telemetry"
        MarketplaceName = "cursor-telemetry"
        CanUninstallCli = $false
        PluginRemoved   = $false
        MarketplaceGone = $false
        AuthRequired    = $false
    }
}

# ── Step 2: Uninstall the plugin ───────────────────────────────────────────────

Header "Step 2: Removing the Plugin"

$cliCapable   = 0
$cliSucceeded = 0

foreach ($e in $EDITORS) {
    if ($e.CanUninstallCli) {
        if (-not $e.CliAvailable) {
            SayFail "$($e.Name): the '$($e.Exe)' CLI is not on PATH - cannot uninstall automatically."
            Write-Host "       Open a terminal where '$($e.Exe)' works and re-run, or remove it"
            Write-Host "       from inside the editor."
            Write-Host ""
            continue
        }

        $cliCapable++
        # -y is required, not optional: the CLI refuses to run when stdout is not
        # a TTY, and Invoke-Native captures stdout.
        $unArgs = @("plugin", "uninstall", $e.PluginRef, "-y")
        Say "$($e.Name): $($e.Exe) $($unArgs -join ' ')"
        $out  = Invoke-Native $e.Exe $unArgs
        $exit = $script:LastNativeExit
        if ($out.Trim()) { Write-Host $out.Trim() }

        if ($exit -eq 0 -or (Test-AlreadyGone $out)) {
            $e.PluginRemoved = $true
            $cliSucceeded++
            SayOk "$($e.Name): plugin removed"
        }
        elseif (Test-AuthError $out) {
            $e.AuthRequired = $true
            SayFail "$($e.Name): not signed in - cannot reach the plugin registry"
            Write-Host "       Run '$($e.Exe) login' and re-run this script."
        }
        else {
            SayFail "$($e.Name): plugin uninstall failed"
        }
        Write-Host ""
        continue
    }

    # Cursor: no uninstall verb, so the last mile is the IDE - the exact mirror
    # of the installer having to send people to the IDE to install it.
    Write-Host "  $($e.Name) removes plugins from the IDE, not the CLI."
    Write-Host "  Do this in Cursor:"
    Write-Host ""
    Write-Host "    1. Open Cursor"
    Write-Host "    2. Settings  >  Plugins  >  cursor-telemetry"
    Write-Host "    3. Click Uninstall (or Remove / Disable)"
    Write-Host ""

    # Ask, then take the answer. See Confirm-ManualStep.
    if (Confirm-ManualStep $e.CacheRoot) {
        $e.PluginRemoved = $true
        SayOk "$($e.Name): plugin removal confirmed"
    }
    else {
        SayWarn "$($e.Name): not confirmed - do the 3 steps above."
        Write-Host "       The rest of this script will continue; nothing here depends on"
        Write-Host "       it having happened yet."
    }
    Write-Host ""
}

# Fatal only when every editor that CAN be uninstalled from its CLI failed.
# Cursor waiting on its IDE step is an expected outcome, not a failure.
if ($cliCapable -gt 0 -and $cliSucceeded -eq 0) {
    Fail-Exit "Plugin uninstall failed in every editor that supports CLI removal." 6
}

# ── Step 3: Marketplace ────────────────────────────────────────────────────────

Header "Step 3: Marketplace Entry"

Write-Host "  The marketplace entry is what lets you re-install the plugin later:"
Write-Host "    $MarketplaceUrl"
Write-Host ""
Write-Host "  Keeping it is harmless - it is a registry entry, not the plugin."
Write-Host ""

if     ($RemoveMarketplace) { $doMarketplace = $true }
elseif ($KeepMarketplace)   { $doMarketplace = $false }
else {
    # Default No: keeping a registry entry costs nothing, removing it costs a
    # re-add if the user only meant to remove the plugin.
    $doMarketplace = Ask-YesNo "Remove the marketplace entry as well?" $false
}
log "Remove marketplace: $doMarketplace"

$mpAttempted = 0
$mpSucceeded = 0

if ($doMarketplace) {
    Write-Host ""
    foreach ($e in $EDITORS) {
        if (-not $e.CliAvailable) {
            SayWarn "$($e.Name): CLI not available - marketplace entry left in place."
            continue
        }
        $mpAttempted++
        $mpArgs = @("plugin", "marketplace", "remove", $e.MarketplaceName)
        Say "$($e.Name): $($e.Exe) $($mpArgs -join ' ')"
        $out  = Invoke-Native $e.Exe $mpArgs
        $exit = $script:LastNativeExit
        if ($out.Trim()) { Write-Host $out.Trim() }

        if ($exit -eq 0 -or (Test-AlreadyGone $out)) {
            $e.MarketplaceGone = $true
            $mpSucceeded++
            SayOk "$($e.Name): marketplace entry removed"
        }
        else {
            SayFail "$($e.Name): could not remove the marketplace entry"
        }
        Write-Host ""
    }

    if ($mpAttempted -gt 0 -and $mpSucceeded -eq 0) {
        # Not fatal on its own - the plugin is already gone, which is the point
        # of the script - but the exit code has to say something went wrong.
        SayWarn "The marketplace entry could not be removed from any editor."
        $script:MarketplaceFailed = $true
    }
}
else {
    Say "Leaving the marketplace entry in place."
}

# ── Step 4: Captured data ──────────────────────────────────────────────────────

Header "Step 4: Captured Data"

$dataExists = Test-Path $CLOUDBYTE_DIR

if (-not $dataExists) {
    Say "No data directory at $CLOUDBYTE_DIR - nothing to delete."
    $doDelete = $false
}
else {
    $size = Get-DirSize $CLOUDBYTE_DIR
    Write-Host "  Location: $CLOUDBYTE_DIR"
    Write-Host "  Size    : $(Format-Size $size)"
    Write-Host ""
    Write-Host "  This directory holds everything the plugin has captured:"
    Write-Host "    - the database of all sessions, prompts, tokens and observations"
    Write-Host "    - logs and per-session state"
    Write-Host ""
    Write-Host "  Deleting it removes that data PERMANENTLY. There is no backup and" -ForegroundColor Yellow
    Write-Host "  no undo." -ForegroundColor Yellow
    Write-Host ""

    # If the other editor still has the plugin, the data is still in use: the
    # database is a single store shared by both integrations.
    $stillInstalled = @()
    if ($claudeHas -and -not ($EDITORS | Where-Object { $_.Key -eq "claude" -and $_.PluginRemoved })) {
        if (-not $wantClaude) { $stillInstalled += "Claude Code" }
    }
    if ($cursorHas -and -not ($EDITORS | Where-Object { $_.Key -eq "cursor" -and $_.PluginRemoved })) {
        if (-not $wantCursor) { $stillInstalled += "Cursor" }
    }
    if ($stillInstalled.Count -gt 0) {
        SayWarn "$($stillInstalled -join ' and ') still has the plugin installed."
        Write-Host "       Both editors share this one database, so deleting it now would"
        Write-Host "       wipe the history that installation is still writing to."
        Write-Host ""
    }

    if     ($DeleteData) { $doDelete = $true }
    elseif ($KeepData)   { $doDelete = $false }
    else {
        $doDelete = Ask-YesNo "Delete the captured data permanently?" $false
    }
}
log "Delete data: $doDelete"

$dataDeleted   = $false
$dataPartial   = $false

if ($doDelete) {
    # Guard against ever resolving to something that is not the data directory.
    # An empty USERPROFILE would otherwise make this "\.cloudbyte".
    $resolved = $null
    try { $resolved = (Resolve-Path -LiteralPath $CLOUDBYTE_DIR -ErrorAction Stop).ProviderPath } catch { }
    $expected = Join-Path $USER_HOME ".cloudbyte"

    if (-not $USER_HOME -or -not $resolved -or
        $resolved.TrimEnd('\') -ne $expected.TrimEnd('\')) {
        SayFail "Refusing to delete: '$CLOUDBYTE_DIR' did not resolve to the expected data directory."
        log "Delete guard tripped: resolved='$resolved' expected='$expected'"
        $dataPartial = $true
    }
    else {
        Write-Host ""
        # Move the log out of the directory being deleted, so the record of the
        # deletion survives it.
        $newLogDir = Join-Path $env:TEMP "cloudbyte-uninstall-logs"
        New-Item -ItemType Directory -Force -Path $newLogDir -ErrorAction SilentlyContinue | Out-Null
        $LOG_DIR  = $newLogDir
        $LOG_FILE = Join-Path $newLogDir ("uninstall-" + (Get-Date -Format "yyyy-MM-dd") + ".log")
        log "Log continued here after moving out of the deleted directory"

        Stop-Everything | Out-Null

        Say "Deleting $CLOUDBYTE_DIR ..."
        $attempts = 0
        while ($attempts -lt 3) {
            $attempts++
            try {
                Remove-Item -LiteralPath $CLOUDBYTE_DIR -Recurse -Force -ErrorAction Stop
            }
            catch {
                log "Delete attempt $attempts failed: $_"
            }

            if (-not (Test-Path $CLOUDBYTE_DIR)) { break }

            if ($attempts -lt 3) {
                SayWarn "Something still held a file open - stopping processes again and retrying..."
                Stop-Everything | Out-Null
            }
        }

        if (-not (Test-Path $CLOUDBYTE_DIR)) {
            $dataDeleted = $true
            SayOk "Captured data deleted"
        }
        else {
            $dataPartial = $true
            SayFail "Could not fully delete $CLOUDBYTE_DIR after $attempts attempts."
            Write-Host ""
            Write-Host "  Something is still holding a file open. Close every Claude Code and"
            Write-Host "  Cursor window, then either re-run this script or remove it by hand:"
            Write-Host "    Remove-Item -LiteralPath `"$CLOUDBYTE_DIR`" -Recurse -Force"
            Write-Host ""
        }
    }
}
elseif ($dataExists) {
    Say "Keeping the captured data."
    Write-Host ""
    Write-Host "  Remove it later by re-running this script and answering yes, or"
    Write-Host "  delete the folder yourself:"
    Write-Host "    Remove-Item -LiteralPath `"$CLOUDBYTE_DIR`" -Recurse -Force"
}

# ── Step 5: Summary ────────────────────────────────────────────────────────────

Header "Step 5: Summary"

foreach ($e in $EDITORS) {
    if     ($e.PluginRemoved)  { $state = "plugin removed" }
    elseif ($e.AuthRequired)   { $state = "not signed in - run '$($e.Exe) login' and re-run" }
    elseif (-not $e.CliAvailable -and $e.CanUninstallCli) { $state = "CLI missing - remove it from the editor" }
    else                       { $state = "not confirmed - finish the step in the IDE" }

    if ($doMarketplace) {
        if ($e.MarketplaceGone) { $state += ", marketplace removed" }
        else                    { $state += ", marketplace kept" }
    }
    Write-Host ("  {0,-12} ->  {1}" -f $e.Name, $state)
}

Write-Host ""
if     ($dataDeleted)     { Write-Host "  Captured data ->  deleted" }
elseif ($dataPartial)     { Write-Host "  Captured data ->  NOT fully deleted - see above" }
elseif (-not $dataExists) { Write-Host "  Captured data ->  none found" }
else                      { Write-Host "  Captured data ->  kept at $CLOUDBYTE_DIR" }

Write-Host ""
Write-Host "  A restart of each editor finishes the removal: any session that is"
Write-Host "  open right now still has the old plugin loaded in memory."
Write-Host ""

# The plugin cache is deliberately left alone - Claude Code prunes its own
# version directories, and deleting a checkout it still tracks causes more
# problems than the disk space it frees.

if ($dataPartial) {
    log "=== Uninstaller finished with data deletion problems ==="
    Write-Host "======================================================"
    Write-Host ""
    exit 7
}

if ($script:MarketplaceFailed) {
    log "=== Uninstaller finished; marketplace removal failed ==="
    Write-Host "======================================================"
    Write-Host ""
    exit 5
}

Write-Host "======================================================"
Write-Host "            [OK] CloudByte Removed"
Write-Host "======================================================"
Write-Host ""

log "=== Uninstaller finished successfully ==="
exit 0
