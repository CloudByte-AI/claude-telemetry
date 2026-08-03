#Requires -Version 5.1
<#
.SYNOPSIS
    CloudByte telemetry plugin installer for Windows - Claude Code and Cursor.

.DESCRIPTION
    Installs the telemetry plugin into the supported editors found on the
    machine. Dependencies are handled automatically - anything missing is
    installed, anything in the way is cleared, and there is never a "do it
    yourself" branch.

    The one question it asks is which editors to install into. Both supported
    editors are always offered, even on a machine with neither installed, since
    either CLI can be provisioned here - what is already present only decides
    which entries Enter accepts. Everything else is unattended. -Target skips
    the question, and it is skipped automatically when stdin is redirected, so
    `irm | iex` in a pipeline cannot stall.

      Step 1  - Detect editors, ask which to use, install any missing CLI
      Step 2  - Ensure uv (installed by Step 3 if missing)
      Step 3  - Run prerequisites validation (scripts/validate.ps1), which
                installs uv and provisions Python 3.12 through it

      Step 4  - Add the marketplace to each editor
      Step 5  - Install the plugin
      Step 6  - Prepare each plugin environment (uv sync), stopping any plugin
                process that holds a virtualenv open
      Step 7  - Print activation instructions and the summary

    Editor differences that the script has to work around:

      Claude Code  CLI is `claude`. The plugin installs from the CLI, so
                   Step 5 is automatic. Cache directories are named by version
                   (0.1.40), so the newest is chosen by version.

      Cursor       CLI is `cursor-agent`, a separate download from the IDE, so
                   Step 1 installs it when Cursor is present but its CLI is not.
                   Only the marketplace can be added from the CLI - the plugin
                   itself must be enabled from the IDE (Settings > Plugins >
                   cursor-telemetry > Install), so Step 5 prints those steps and
                   then waits for the plugin to appear. Cache directories are
                   named by commit sha, which cannot be ordered, so the newest
                   is chosen by modification time.

    It only exits non-zero when an automatic install genuinely fails, and then
    it prints the manual command to run.

    Plugin activation (/reload-plugins or a session restart) cannot be
    automated and remains a manual step in both editors.

.PARAMETER Yes
    Do not ask which editors to use - take every one that was detected.
    Dependency installation is automatic either way.

.PARAMETER NonInteractive
    Same as -Yes: never ask, use every detected editor. Both switches exist so
    unattended runs and CI cannot block on the selection prompt.

.PARAMETER SkipPrereqs
    Skip Step 3 (validate.ps1). Ignored when uv is missing, since Step 3 is
    what installs it.

.PARAMETER Target
    Which editors to install into: auto (default), ask, claude, cursor, or both.

      auto    every editor found on the machine, minus anything deselected at
              the prompt. For Cursor "found" means the IDE, not just its CLI,
              since the CLI is installed automatically when missing. On a
              machine with neither editor, the Claude Code CLI is installed.
      ask     always show the selection prompt, even for a single editor.
      claude
      cursor
      both    an explicit choice - the prompt is skipped entirely.

.PARAMETER CursorWaitSeconds
    How long Step 5 waits for the Cursor plugin to appear after printing the
    IDE steps. 0 skips the wait. Timing out is not an error - the plugin builds
    its own environment on first use.

.PARAMETER CursorDir
    Cursor's data directory. Defaults to ~/.cursor.

.PARAMETER CursorCliInstallUrl
    Where the cursor-agent installer is fetched from. Override for an internal
    mirror, or to test the install path without hitting cursor.com.

.PARAMETER ClaudeCliInstallUrl
    Where the Claude Code installer is fetched from. Same purpose as
    -CursorCliInstallUrl.

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
      2  no usable editor CLI for the requested target, and the automatic
         install of the missing CLI failed
      3  reserved (uv failures are reported by validation as code 4)
      4  prerequisites validation failed
      5  marketplace add failed for every targeted editor
      6  plugin install failed for every targeted editor
#>

[CmdletBinding()]
param(
    # Kept for backward compatibility only - the installer is always automatic.
    [switch] $Yes,
    [switch] $NonInteractive,

    [switch] $SkipPrereqs,
    [switch] $UseLocalValidate,
    [switch] $OpenDashboard,

    [ValidateSet("auto", "ask", "claude", "cursor", "both")]
    [string] $Target            = "auto",
    [int]    $CursorWaitSeconds = 120,
    [string] $CursorDir         = "",
    [string] $CursorCliInstallUrl = "https://cursor.com/install?win32=true",
    [string] $ClaudeCliInstallUrl = "https://claude.ai/install.ps1",

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

if ($CursorDir)             { $CURSOR_DIR = $CursorDir }
elseif ($env:CURSOR_DIR)    { $CURSOR_DIR = $env:CURSOR_DIR }
else                        { $CURSOR_DIR = Join-Path $USER_HOME ".cursor" }

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
        "$env:LOCALAPPDATA\cursor-agent"
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

# Whether it is safe to ask a question at all.
#
# Read-Host against a redirected or absent stdin either returns instantly with
# nothing or throws, so the answer has to be decided before prompting rather
# than recovered from afterwards. Note that `irm | iex` in a normal console is
# still interactive - the script text arrived over HTTP, not over stdin.
function Test-CanPrompt {
    if ($Yes -or $NonInteractive) { return $false }
    try {
        if ([Console]::IsInputRedirected) { return $false }
    }
    catch {
        return $false   # no console host at all
    }
    if (-not [Environment]::UserInteractive) { return $false }
    return $true
}

# Ask which editors should get the plugin.
#
# Every supported editor is listed, not just the ones already on the machine -
# both CLIs can be installed automatically, so "not detected" is not the same as
# "not available", and a fresh machine would otherwise never get a choice.
# Detection decides the DEFAULT (what Enter selects), never the menu.
#
# Always returns a non-empty subset: an unusable answer falls back to the
# default rather than leaving the user with nothing installed.
function Select-Editors {
    param(
        [array]    $Candidates,
        [string[]] $DefaultKeys
    )

    $defaults     = @($Candidates | Where-Object { $DefaultKeys -contains $_.Key })
    $defaultLabel = (@($defaults | ForEach-Object { $_.Name }) -join ', ')
    if (-not $defaultLabel) { $defaultLabel = "none" }

    Write-Host ""
    Write-Host "  Install the plugin for which editors?"
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

        # Dedupe by Key so "1,1" is one editor, not two passes over the same one.
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

$CLAUDE_CLI_INSTALL_URL = $ClaudeCliInstallUrl

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

# Run a vendor `irm ... | iex` installer in a child shell.
#
# Two things this gets right that a bare invocation does not:
#   - Out-Host keeps the installer's progress visible without letting its stdout
#     leak into the caller's return value (a non-empty array is truthy, so a
#     failed install would otherwise read as success).
#   - ErrorActionPreference is forced to Continue and ErrorRecords are flattened,
#     because PowerShell 5.1 turns any stderr line from a native command into a
#     terminating NativeCommandError decorated with "At line:N char:N". Vendor
#     installers write progress to stderr, so that would abort on a success.
function Invoke-ChildInstaller {
    param([string] $Command)

    $shell   = Get-HostShell
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $shell -ExecutionPolicy Bypass -NoProfile -Command $Command 2>&1 |
            ForEach-Object {
                if ($_ -is [System.Management.Automation.ErrorRecord]) { $_.ToString() } else { "$_" }
            } | Out-Host
        log "Child installer exit: $LASTEXITCODE"
    }
    finally {
        $ErrorActionPreference = $prevEap
    }
}

function Install-ClaudeCode {
    # 1) Official native installer - no Node.js required.
    Say "Installing Claude Code via the official installer..."
    Write-Host ""
    try {
        Invoke-ChildInstaller "irm '$CLAUDE_CLI_INSTALL_URL' | iex"
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

# Printed only when the automatic install of Claude Code has already failed -
# never offered as a choice.
function Show-ClaudeRecovery {
    Write-Host ""
    Write-Host "  Install Claude Code by hand with either:"
    Write-Host "    irm $CLAUDE_CLI_INSTALL_URL | iex"
    Write-Host "    npm install -g @anthropic-ai/claude-code"
    Write-Host ""
    Write-Host "  Then open a new terminal and re-run this script - it will pick up"
    Write-Host "  from Step 2 automatically."
    Write-Host ""
}

$CURSOR_CLI_INSTALL_URL = $CursorCliInstallUrl

function Find-CursorAgentExe {
    # The official installer unpacks into %LOCALAPPDATA%\cursor-agent\versions\<v>
    # and copies cursor-agent* up into %LOCALAPPDATA%\cursor-agent.
    $base = Join-Path $env:LOCALAPPDATA "cursor-agent"
    foreach ($name in @("cursor-agent.exe", "cursor-agent.cmd", "cursor-agent.ps1")) {
        $p = Join-Path $base $name
        if (Test-Path $p) { return $p }
    }
    return $null
}

function Install-CursorAgent {
    Say "Installing the Cursor CLI via the official installer..."
    Write-Host ""

    # Deliberately only called when cursor-agent is absent: the official
    # installer starts by deleting %LOCALAPPDATA%\cursor-agent outright, so
    # running it over a working install would replace it rather than repair it.
    try {
        Invoke-ChildInstaller "irm '$CURSOR_CLI_INSTALL_URL' | iex"
    }
    catch {
        SayWarn "Cursor CLI installer failed: $_"
    }

    # The installer writes the User PATH, which Refresh-Path merges in; its own
    # change to $env:PATH happened in the child process and is already gone.
    Refresh-Path
    if (Test-Command "cursor-agent") {
        SayOk "Cursor CLI installed"
        return $true
    }

    $exe = Find-CursorAgentExe
    if ($exe) {
        $env:PATH = (Split-Path $exe) + ";" + $env:PATH
        if (Test-Command "cursor-agent") {
            SayOk "Cursor CLI installed - found at $exe"
            return $true
        }
    }

    return $false
}

function Show-CursorCliRecovery {
    Write-Host ""
    Write-Host "  Install the Cursor CLI by hand with:"
    Write-Host "    irm '$CURSOR_CLI_INSTALL_URL' | iex"
    Write-Host ""
    Write-Host "  If that fails, install or update Cursor itself first:"
    Write-Host "    https://cursor.com/download"
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

# An editor CLI can be installed and on PATH yet refuse to talk to the
# marketplace because nobody has signed in. That is not a broken install and
# must not be reported as one - it needs a one-time `login`, which is an
# interactive browser flow this script cannot and should not automate.
function Test-AuthError {
    param([string] $Text)
    return ($Text -match "(?i)authentication required|not (logged in|authenticated)|unauthorized|\b401\b|please (log ?in|sign ?in)|CURSOR_API_KEY")
}

function Show-CliAuthHelp {
    param([object] $Editor)
    Write-Host ""
    Write-Host "  $($Editor.Name) is installed but not signed in, so its CLI cannot"
    Write-Host "  reach the marketplace. Sign in once:"
    Write-Host ""
    if ($Editor.Key -eq "cursor") {
        Write-Host "    cursor-agent login        (or:  agent login)"
        Write-Host ""
        Write-Host "  Non-interactively, set CURSOR_API_KEY instead."
    }
    else {
        Write-Host "    $($Editor.Exe) login"
    }
    Write-Host ""
    Write-Host "  Then re-run this script - everything else is already in place."
    Write-Host ""
}

function Test-LockError {
    param([string] $Text)
    # "Access is denied. (os error 5)" is how uv reports a held .venv on
    # Windows; the others cover the Claude CLI and .NET file APIs.
    return ($Text -match "EACCES|EPERM|permission denied|being used by another process|Access to the path|Access is denied|os error 5")
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
                ($_.CommandLine -like "*claude-telemetry*" -or
                 $_.CommandLine -like "*cursor-telemetry*" -or
                 $_.CommandLine -like "*cloudbyte*") -and
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

# Run an editor CLI command, retrying once after releasing file locks.
# Treats "already exists" style output as success.
function Invoke-CliStep {
    param(
        [string]   $Exe,
        [string[]] $Arguments,
        [string]   $What
    )
    $out  = Invoke-Native $Exe $Arguments
    $exit = $script:LastNativeExit
    if ($out.Trim()) { Write-Host $out.Trim() }

    if (Test-LockError $out) {
        Write-Host ""
        SayWarn "Permission / file lock error while $What"
        Release-FileLock
        Say "Retrying..."
        $out  = Invoke-Native $Exe $Arguments
        $exit = $script:LastNativeExit
        if ($out.Trim()) { Write-Host $out.Trim() }
    }

    # Published so callers can tell a real failure from a missing login.
    $script:LastCliOutput = $out

    if ($exit -ne 0 -and ($out -notmatch "(?i)already|exists")) {
        return $false
    }
    return $true
}

# Pick the current plugin checkout under a cache root.
#
# Claude Code names these directories by version ("0.1.40"); Cursor names them
# by commit sha ("7d91aa5f..."), which carries no ordering. So: order by version
# when every name is a version, and fall back to modification time only when at
# least one is not. The fallback is deliberately NOT the default - creating a
# .venv inside a directory bumps that directory's mtime, so mtime alone would
# rank an older version above a newer one purely because it was synced later.
function Get-PluginDir {
    param([string] $Root)

    if (-not (Test-Path $Root)) { return $null }
    $dirs = @(Get-ChildItem -Path $Root -Directory -ErrorAction SilentlyContinue)
    if (-not $dirs) { return $null }

    $versions   = @{}
    $allVersion = $true
    foreach ($d in $dirs) {
        $v = $null
        if ([version]::TryParse(($d.Name -replace '^[vV]', ''), [ref] $v)) { $versions[$d.Name] = $v }
        else { $allVersion = $false }
    }

    if ($allVersion) {
        $ranked = $dirs | Sort-Object @{ Expression = { $versions[$_.Name] } }, Name
    }
    else {
        $ranked = $dirs | Sort-Object LastWriteTimeUtc, Name
    }
    return @($ranked)[-1].FullName
}

# Condition 1 of the uv sync check: is there a real environment in there?
# A bare .venv directory is not enough - an interrupted build leaves one behind
# with no interpreter, and uv sync has to run again to finish it.
function Test-BuiltEnv {
    param([string] $Dir)

    $venv = Join-Path $Dir ".venv"
    if (-not (Test-Path (Join-Path $venv "pyvenv.cfg"))) { return $false }
    foreach ($py in @("Scripts\python.exe", "bin\python.exe", "bin\python")) {
        if (Test-Path (Join-Path $venv $py)) { return $true }
    }
    return $false
}

# Condition 2 of the uv sync check: is the environment older than what it was
# built from? pyvenv.cfg is written when the environment is created, so it is
# the honest "built at" timestamp; uv.lock and pyproject.toml are the inputs.
# An upgrade rewrites the inputs but leaves an existing .venv in place, which is
# exactly the case a plain existence check would miss.
function Test-EnvStale {
    param([string] $Dir)

    $marker = Join-Path $Dir ".venv\pyvenv.cfg"
    if (-not (Test-Path $marker)) { return $true }

    # Not named $input - that is an automatic variable inside a function.
    $built = (Get-Item $marker).LastWriteTimeUtc
    foreach ($src in @("uv.lock", "pyproject.toml")) {
        $p = Join-Path $Dir $src
        if ((Test-Path $p) -and ((Get-Item $p).LastWriteTimeUtc -gt $built)) {
            log "Stale env: $src is newer than .venv ($((Get-Item $p).LastWriteTimeUtc) > $built)"
            return $true
        }
    }
    return $false
}

# Cursor cannot install a plugin from its CLI, so Step 5 prints the IDE steps
# and then watches for the checkout to show up. Polling rather than prompting
# keeps the script stdin-free; timing out is not an error.
function Wait-ForPluginDir {
    param([string] $Root, [int] $Seconds)

    $dir = Get-PluginDir $Root
    if ($dir) { return $dir }
    if ($Seconds -le 0) { return $null }

    Write-Host "  Waiting up to $Seconds seconds for the plugin to appear..."
    Write-Host "  (Ctrl+C is safe - the plugin builds its own environment on first use)"
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 3
        $dir = Get-PluginDir $Root
        if ($dir) {
            Write-Host ""
            SayOk "Plugin detected"
            return $dir
        }
        Write-Host "." -NoNewline
    }
    Write-Host ""
    return $null
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

Header "Step 1: Detecting Editors"

if (-not (Test-Command "claude") -or -not (Test-Command "cursor-agent")) {
    Refresh-Path
}

$claudePresent = Test-Command "claude"
$cursorPresent = Test-Command "cursor-agent"

# Cursor's CLI is a separate download from the IDE, so a machine can have Cursor
# without cursor-agent. Detect the IDE too, so auto mode installs the CLI for it
# instead of silently skipping the editor the user actually uses.
$cursorIdePresent = (Test-Path $CURSOR_DIR) -or
                    (Test-Path (Join-Path $env:LOCALAPPDATA "Programs\cursor\Cursor.exe")) -or
                    (Test-Path (Join-Path ${env:ProgramFiles} "cursor\Cursor.exe"))

if ($claudePresent) { SayOk "Claude Code CLI found (claude)" }  else { Say "Claude Code CLI not found" }
if ($cursorPresent) { SayOk "Cursor CLI found (cursor-agent)" } else { Say "Cursor CLI not found" }
if (-not $cursorPresent -and $cursorIdePresent) { Say "Cursor itself is installed - its CLI will be added" }

switch ($Target) {
    "claude" { $wantClaude = $true;  $wantCursor = $false }
    "cursor" { $wantClaude = $false; $wantCursor = $true }
    "both"   { $wantClaude = $true;  $wantCursor = $true }
    default  {
        # auto / ask. Detection sets the default only - the menu below always
        # offers both editors, because either CLI can be installed from here.
        $wantClaude = $claudePresent
        $wantCursor = $cursorPresent -or $cursorIdePresent
        if (-not $wantClaude -and -not $wantCursor) { $wantClaude = $true }
    }
}
log "Target=$Target -> default claude=$wantClaude cursor=$wantCursor"

# Every supported editor is a candidate regardless of what is installed: the
# script can provision either CLI, so a fresh machine still gets a real choice.
# Detection only annotates the list and picks what Enter selects.
$cursorNote = "not installed - needs the Cursor IDE"
if     ($cursorPresent)    { $cursorNote = "installed" }
elseif ($cursorIdePresent) { $cursorNote = "Cursor found - its CLI will be installed" }

$candidates = @(
    [pscustomobject] @{
        Key  = "claude"
        Name = "Claude Code"
        Note = if ($claudePresent) { "installed" } else { "not installed - will be installed for you" }
    }
    [pscustomobject] @{ Key = "cursor"; Name = "Cursor"; Note = $cursorNote }
)

$defaultKeys = @()
if ($wantClaude) { $defaultKeys += "claude" }
if ($wantCursor) { $defaultKeys += "cursor" }

# An explicit -Target is an answer already given, so do not ask again.
$explicitTarget = $PSBoundParameters.ContainsKey("Target") -and $Target -ne "ask"

if (-not $explicitTarget -and (Test-CanPrompt)) {
    $chosen     = @(Select-Editors -Candidates $candidates -DefaultKeys $defaultKeys)
    $chosenKeys = @($chosen | ForEach-Object { $_.Key })
    $wantClaude = $chosenKeys -contains "claude"
    $wantCursor = $chosenKeys -contains "cursor"
    Write-Host ""
    SayOk "Selected: $(@($chosen | ForEach-Object { $_.Name }) -join ', ')"
    log "User selected editors: $($chosenKeys -join ',')"
}
elseif (-not $explicitTarget) {
    Say "No console attached - using detected editors: $($defaultKeys -join ', ')"
    log "Prompt skipped (non-interactive), using $($defaultKeys -join ',')"
}

if ($wantClaude -and -not $claudePresent) {
    Write-Host ""
    SayWarn "Installing the Claude Code CLI"
    Write-Host ""
    if (Install-ClaudeCode) {
        $claudePresent = $true
    }
    else {
        Write-Host ""
        SayFail "Could not install the Claude Code CLI automatically."
        Show-ClaudeRecovery
        $wantClaude = $false
    }
}

if ($wantCursor -and -not $cursorPresent) {
    Write-Host ""
    SayWarn "Installing the Cursor CLI (cursor-agent)"
    Write-Host ""
    if (Install-CursorAgent) {
        $cursorPresent = $true
    }
    else {
        Write-Host ""
        SayFail "Could not install the Cursor CLI automatically."
        Show-CursorCliRecovery
        $wantCursor = $false
    }
}

if (-not $wantClaude -and -not $wantCursor) {
    log "No usable editor CLI for -Target $Target - aborting"
    # Say which target failed rather than "no editor found" - with -Target cursor
    # on a Claude-only machine, an editor IS present, just not the requested one.
    if ($Target -eq "auto") {
        Fail-Exit "No usable editor CLI. Install Claude Code or Cursor and re-run." 2
    }
    Fail-Exit "No usable editor CLI for -Target $Target. Re-run without -Target to use whatever is installed." 2
}

# Editors to install into. CanInstallPlugin is the real difference between them:
# Claude Code installs a plugin from its CLI, Cursor only from its IDE.
$EDITORS = @()
if ($wantClaude) {
    $EDITORS += [pscustomobject] @{
        Key              = "claude"
        Name             = "Claude Code"
        Exe              = "claude"
        CacheRoot        = Join-Path $CLAUDE_DIR "plugins\cache\claude-telemetry\claude-telemetry"
        PluginRef        = $PluginRef
        CanInstallPlugin = $true
        MarketplaceOk    = $false
        AuthRequired     = $false
        PluginOk         = $false
        PluginDir        = $null
        SyncOk           = $false
    }
}
if ($wantCursor) {
    $EDITORS += [pscustomobject] @{
        Key              = "cursor"
        Name             = "Cursor"
        Exe              = "cursor-agent"
        CacheRoot        = Join-Path $CURSOR_DIR "plugins\cache\cursor-telemetry\cursor-telemetry"
        PluginRef        = "cursor-telemetry@cursor-telemetry"
        CanInstallPlugin = $false
        MarketplaceOk    = $false
        AuthRequired     = $false
        PluginOk         = $false
        PluginDir        = $null
        SyncOk           = $false
    }
}

Write-Host ""
foreach ($e in $EDITORS) {
    $ver = (Invoke-Native $e.Exe @("--version")).Trim()
    if (-not $ver) { $ver = "version unknown" }
    SayOk "$($e.Name) ready - $ver"
}

# ── Step 2: uv (and, through it, Python) ───────────────────────────────────────

Header "Step 2: Checking uv"

# uv is the only hard dependency. Python 3.12 is provisioned BY uv in Step 3,
# into uv's own directory - the user's existing python, whatever its version,
# is neither required nor modified.
$uvOk = Test-Command "uv"
if ($uvOk) { SayOk "uv found" } else { SayWarn "uv not found" }

if (Test-PythonPresent) {
    $sysPy = $null
    foreach ($cmd in @("python", "python3")) {
        if (Test-Command $cmd) {
            $v = (Invoke-Native $cmd @("--version")).Trim()
            if ($v -match "^Python [0-9]") { $sysPy = $v -replace "Python ", ""; break }
        }
    }
    if ($sysPy) { Say "Your default python is $sysPy - it will not be changed" }
}

# Set when uv has to be installed, which makes Step 3 mandatory: validate.ps1
# is what installs uv, so -SkipPrereqs cannot be honoured in that case.
$installDeps = $false

if (-not $uvOk) {
    Write-Host ""
    Write-Host "  uv is required by the CloudByte plugin. It also supplies the"
    Write-Host "  Python 3.12 the plugin runs on, without altering your own."
    Write-Host ""
    Say "It will be installed automatically in Step 3."
    $installDeps = $true
    log "uv missing - Step 3 will install it (forced, -SkipPrereqs ignored)"
}
else {
    Write-Host ""
    Say "uv is present - Step 3 will confirm Python 3.12 is available to it."
}

# ── Step 3: Prerequisites validation ───────────────────────────────────────────

if ($SkipPrereqs -and -not $installDeps) {
    Header "Step 3: Prerequisites (skipped)"
    SayWarn "-SkipPrereqs supplied - not running validate.ps1"
}
else {
    Header "Step 3: Running Prerequisites Validation"

    if ($SkipPrereqs) {
        SayWarn "-SkipPrereqs ignored - validation is what installs uv"
    }

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

    # Python deliberately is not checked here: it lives inside uv's own
    # directory and is never expected on PATH.
    if (-not (Test-Command "uv")) {
        SayWarn "uv still not visible on this session's PATH - open a new terminal and re-run."
    }
}

# ── Step 4: Add marketplace ────────────────────────────────────────────────────

Header "Step 4: Adding Marketplace"

$mpArgs = @("plugin", "marketplace", "add", $MarketplaceUrl)
foreach ($e in $EDITORS) {
    Say "$($e.Name): $($e.Exe) $($mpArgs -join ' ')"
    $e.MarketplaceOk = Invoke-CliStep $e.Exe $mpArgs "adding the marketplace to $($e.Name)"

    if ($e.MarketplaceOk) {
        SayOk "$($e.Name): marketplace added"
    }
    elseif (Test-AuthError $script:LastCliOutput) {
        $e.AuthRequired = $true
        SayFail "$($e.Name): not signed in"
        Show-CliAuthHelp $e
        log "$($e.Key): marketplace add blocked by missing login"
    }
    else {
        SayFail "$($e.Name): marketplace add failed"
    }
    Write-Host ""
}

# Only fatal when no editor got the marketplace - one editor failing must not
# discard a working install into the other.
if (-not (@($EDITORS | Where-Object { $_.MarketplaceOk }).Count)) {
    if (@($EDITORS | Where-Object { $_.AuthRequired }).Count -eq $EDITORS.Count) {
        Fail-Exit "Sign in to your editor CLI (see above), then re-run this script." 5
    }
    Fail-Exit "Failed to add the marketplace to any editor." 5
}

# ── Step 5: Install plugin ─────────────────────────────────────────────────────

Header "Step 5: Installing Plugin"

foreach ($e in $EDITORS) {
    if (-not $e.MarketplaceOk) {
        SayWarn "$($e.Name): skipping - marketplace was not added"
        continue
    }

    if ($e.CanInstallPlugin) {
        $instArgs = @("plugin", "install", $e.PluginRef)
        Say "$($e.Name): $($e.Exe) $($instArgs -join ' ')"
        $e.PluginOk = Invoke-CliStep $e.Exe $instArgs "installing the plugin in $($e.Name)"
        if ($e.PluginOk) { SayOk "$($e.Name): plugin installed" }
        else             { SayFail "$($e.Name): plugin install failed" }
        Write-Host ""
        continue
    }

    # Cursor: the CLI has no plugin install verb, so the last mile is the IDE.
    # Warn when the IDE is missing entirely - otherwise -Target cursor on a
    # machine without Cursor waits for a plugin that can never appear.
    if (-not $cursorIdePresent) {
        SayWarn "Cursor itself was not found on this machine."
        Write-Host "       The CLI is installed and the marketplace is registered, but the"
        Write-Host "       steps below need the Cursor IDE: https://cursor.com/download"
        Write-Host ""
    }

    Write-Host "  $($e.Name) installs plugins from the IDE, not the CLI."
    Write-Host "  The marketplace is registered - finish it in Cursor:"
    Write-Host ""
    Write-Host "    1. Open Cursor"
    Write-Host "    2. Settings  >  Plugins  >  cursor-telemetry"
    Write-Host "    3. Click Install (or Add)"
    Write-Host ""

    $e.PluginDir = Wait-ForPluginDir $e.CacheRoot $CursorWaitSeconds
    if ($e.PluginDir) {
        $e.PluginOk = $true
        SayOk "$($e.Name): plugin installed"
        Say "  $($e.PluginDir)"
    }
    else {
        SayWarn "$($e.Name): plugin not installed yet - do the 3 steps above."
        Write-Host "       Nothing else is needed afterwards: the plugin builds its own"
        Write-Host "       environment the first time Cursor runs it."
    }
    Write-Host ""
}

# Fatal only when every editor that CAN install from its CLI failed. Cursor
# waiting on its IDE step is an expected outcome, not a failure.
$cliCapable = @($EDITORS | Where-Object { $_.CanInstallPlugin -and $_.MarketplaceOk })
if ($cliCapable.Count -gt 0 -and -not @($cliCapable | Where-Object { $_.PluginOk }).Count) {
    Fail-Exit "Plugin install failed in every editor that supports CLI installation." 6
}

# ── Step 6: Plugin environment ─────────────────────────────────────────────────

Header "Step 6: Preparing Plugin Environment"

# Sync one plugin checkout. Returns $true when the environment is usable,
# whether it was just built or was already good.
function Invoke-PluginSync {
    param([string] $Name, [string] $Dir)

    # The two conditions that decide whether a sync is needed at all. Neither is
    # sufficient alone: a .venv can exist without an interpreter (interrupted
    # build), and a complete .venv can be older than the uv.lock it was built
    # from (plugin upgraded in place).
    $built = Test-BuiltEnv $Dir
    $stale = Test-EnvStale $Dir

    if ($built -and -not $stale) {
        SayOk "$Name : environment already built and up to date"
        Say  "  $Dir"
        return $true
    }
    if ($built) { Say "$Name : environment is older than uv.lock - rebuilding" }
    else        { Say "$Name : no environment yet - building" }

    Say "  $Dir"
    Say "  Syncing dependencies (this can take a minute on first run)..."

    $syncArgs = @("sync", "--frozen", "--python", "3.12", "--directory", $Dir)

    # An inherited VIRTUAL_ENV from the caller's shell makes uv warn and target
    # the wrong environment - hide it for the duration of the sync.
    $savedVenv = $env:VIRTUAL_ENV
    Remove-Item Env:VIRTUAL_ENV -ErrorAction SilentlyContinue
    try {
        $syncOut = Invoke-Native "uv" $syncArgs
        if ($syncOut.Trim()) { Write-Host $syncOut.Trim() }

        # A live editor session running the MCP server holds .venv open, which
        # surfaces as "Access is denied" while uv rebuilds it. Clear it without
        # asking: those processes belong to sessions that must restart anyway to
        # pick up the plugin, and Release-FileLock never touches this script's
        # own process tree.
        if ($script:LastNativeExit -ne 0 -and (Test-LockError $syncOut)) {
            Write-Host ""
            SayWarn "The plugin virtualenv is locked by a running editor/MCP process."
            Write-Host "       Stopping it - those sessions have to restart anyway to load"
            Write-Host "       the plugin. The session running this script is not affected."
            Write-Host ""
            Release-FileLock
            Say "Retrying sync..."
            $syncOut = Invoke-Native "uv" $syncArgs
            if ($syncOut.Trim()) { Write-Host $syncOut.Trim() }
        }
    }
    finally {
        if ($savedVenv) { $env:VIRTUAL_ENV = $savedVenv }
    }

    if ($script:LastNativeExit -eq 0) {
        SayOk "$Name : environment ready"
        return $true
    }

    SayWarn "$Name : environment setup incomplete - it will be rebuilt on first use."
    Write-Host "       You can finish it now with:"
    Write-Host "         uv sync --frozen --python 3.12 --directory `"$Dir`""
    return $false
}

$uvAvailable = Test-Command "uv"
if (-not $uvAvailable) {
    SayWarn "uv is not on this session's PATH - skipping 'uv sync'."
    Write-Host "       Open a new terminal and re-run this script to finish."
}

foreach ($e in $EDITORS) {
    if (-not $e.PluginDir) { $e.PluginDir = Get-PluginDir $e.CacheRoot }

    if (-not $e.PluginDir) {
        SayWarn "$($e.Name): no plugin checkout found - environment builds on first use."
        Write-Host "         Looked in: $($e.CacheRoot)"
        Write-Host ""
        continue
    }
    if (-not $uvAvailable) {
        Write-Host "         $($e.Name): uv sync --frozen --python 3.12 --directory `"$($e.PluginDir)`""
        continue
    }

    $e.SyncOk = Invoke-PluginSync $e.Name $e.PluginDir
    Write-Host ""
}

# ── Step 7: Activation, dashboard, summary ─────────────────────────────────────

Header "Step 7: Activate the Plugin (manual)"

Write-Host "  The plugin is installed but not yet active in running sessions."
Write-Host ""

foreach ($e in $EDITORS) {
    if ($e.Key -eq "claude") {
        Write-Host "  Claude Code"
        Write-Host "    Quickest path : type  /reload-plugins"
        Write-Host "    If tools do not appear, or you see an MCP error, restart instead:"
        Write-Host "      CLI            : Ctrl+C, then  claude --resume <session-id>"
        Write-Host "      VS Code/Desktop: close the Claude panel, reopen it, resume"
        Write-Host ""
    }
    else {
        Write-Host "  Cursor"
        if ($e.PluginOk) {
            Write-Host "    Reload the window (Command Palette > Reload Window), or"
            Write-Host "    quit and reopen Cursor."
        }
        else {
            Write-Host "    Settings > Plugins > cursor-telemetry > Install, then reload"
            Write-Host "    the window. No terminal step is needed afterwards."
        }
        Write-Host ""
    }
}

Write-Host "  Note: reloading only affects the current window/session. Any other"
Write-Host "        open sessions need their own restart."
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

foreach ($e in $EDITORS) {
    if     ($e.PluginOk -and $e.SyncOk) { $state = "installed, environment ready" }
    elseif ($e.PluginOk)                { $state = "installed, environment builds on first use" }
    elseif ($e.MarketplaceOk)           { $state = "marketplace added - finish the install in the IDE" }
    elseif ($e.AuthRequired)            { $state = "not signed in - run '$($e.Exe) login' and re-run" }
    else                                { $state = "not installed" }
    Write-Host ("  {0,-12} ->  {1}" -f $e.Name, $state)
}

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
