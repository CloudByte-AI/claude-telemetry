# CloudByte Prerequisites Script (Windows PowerShell)
# Ensures uv is installed, then ensures a Python 3.12 interpreter is available
# TO UV - not to the user's shell.
#
# Python is provisioned through `uv python install`, which places a managed
# interpreter under %APPDATA%\uv\python and touches neither PATH nor the `py`
# launcher. A pre-existing Python of any version is left exactly as it is: an
# older interpreter (3.9, say) is no longer an error, because nothing the
# plugin runs depends on the user's default `python`.
#
# Run directly or via skill - no plugin context required.

# Home directory
$USER_HOME     = $env:USERPROFILE
$CLOUDBYTE_DIR = "$USER_HOME\.cloudbyte"
$LOG_DIR       = "$CLOUDBYTE_DIR\logs"
$SETUP_LOG_DIR = "$CLOUDBYTE_DIR\logs\setup"

New-Item -ItemType Directory -Force -Path $LOG_DIR | Out-Null
New-Item -ItemType Directory -Force -Path $SETUP_LOG_DIR | Out-Null

# Logging
$DATE_STR = Get-Date -Format "yyyy-MM-dd"
$LOG_FILE = "$SETUP_LOG_DIR\setup-$DATE_STR.log"

function log {
    param($Message)
    $ts   = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "[$ts] $Message"
    Write-Host $line
    Add-Content -Path $LOG_FILE -Value $line
}

# Version of Python the plugin is locked against. Kept exact rather than
# ">=3.12" so resolution matches uv.lock instead of drifting onto whatever
# newest interpreter happens to be present.
$PY_TARGET = "3.12"

log "=== CloudByte Prerequisites Check ==="
log "OS: Windows"
log "Home: $USER_HOME"
Write-Host ""
Write-Host "======================================"
Write-Host "  CloudByte Prerequisites Check"
Write-Host "======================================"
Write-Host ""

# An activated virtualenv would make uv resolve to that environment instead of
# a real interpreter, so probes run without it.
if ($env:VIRTUAL_ENV) {
    log "Ignoring active VIRTUAL_ENV for probes: $env:VIRTUAL_ENV"
    Remove-Item Env:VIRTUAL_ENV -ErrorAction SilentlyContinue
}

# Run a native command and capture stdout+stderr as plain text, without
# PowerShell 5.1 wrapping stderr lines in ErrorRecords.
function Invoke-Native {
    param([string] $Exe, [string[]] $Arguments = @())
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
    return $output
}

function Refresh-Path {
    $current = $env:PATH
    $machine = [System.Environment]::GetEnvironmentVariable("PATH", "Machine")
    $user    = [System.Environment]::GetEnvironmentVariable("PATH", "User")
    $extra   = @(
        "$env:USERPROFILE\.local\bin"
        "$env:USERPROFILE\.cargo\bin"
    )
    $seen  = New-Object System.Collections.Generic.HashSet[string] ([StringComparer]::OrdinalIgnoreCase)
    $parts = New-Object System.Collections.Generic.List[string]
    foreach ($entry in (@($current, $machine, $user) -join ";").Split(";") + $extra) {
        $e = $entry.Trim()
        if ($e -and $seen.Add($e)) { [void] $parts.Add($e) }
    }
    $env:PATH = ($parts -join ";")
}

# ── uv ─────────────────────────────────────────────────────────────────────────
# uv comes first: it is the hard requirement, and it is what provisions Python.

Write-Host "-- Checking uv --------------------------"

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { Refresh-Path }

if (Get-Command uv -ErrorAction SilentlyContinue) {
    $UV_VERSION = (uv --version 2>&1)
    log "uv found: $UV_VERSION"
    Write-Host "[OK] $UV_VERSION"
}
else {
    log "uv not found - installing..."
    Write-Host "uv not found - installing..."

    try {
        # Download and run as a FILE rather than `-Command "irm ... | iex"`.
        # That form nests a pipeline inside a -Command string and the quoting
        # does not survive every host: the pipe can bind in the caller, so the
        # child only prints the script and the caller's iex parses its first
        # line alone. uv's installer opens with a param() block, so that failed
        # with "Missing ')' in function parameter list".
        $uvInstaller = Join-Path $env:TEMP ("uv-install-" + [guid]::NewGuid().ToString("N").Substring(0, 8) + ".ps1")
        try {
            [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
            Invoke-WebRequest -Uri "https://astral.sh/uv/install.ps1" -OutFile $uvInstaller -UseBasicParsing
            powershell -ExecutionPolicy Bypass -NoProfile -File $uvInstaller
        }
        finally {
            Remove-Item $uvInstaller -Force -ErrorAction SilentlyContinue
        }
        Refresh-Path

        if (Get-Command uv -ErrorAction SilentlyContinue) {
            $UV_VERSION = (uv --version 2>&1)
            log "uv installed: $UV_VERSION"
            Write-Host "[OK] $UV_VERSION installed"
        }
        else {
            log "uv installed but not on PATH"
            Write-Host "[FAIL] uv installed but is not on PATH yet"
            Write-Host "Open a new terminal and re-run, or install manually:"
            Write-Host "  https://docs.astral.sh/uv/getting-started/installation/"
            exit 1
        }
    }
    catch {
        log "Failed to install uv: $_"
        Write-Host "[FAIL] Failed to install uv"
        Write-Host "Please install manually: https://docs.astral.sh/uv/getting-started/installation/"
        exit 1
    }
}

# ── Python ─────────────────────────────────────────────────────────────────────

Write-Host ""
Write-Host "-- Checking Python $PY_TARGET ---------------"

# Report whatever the user's shell currently resolves, purely so the log shows
# it was seen and deliberately left alone.
$systemPython = $null
foreach ($cmd in @("python", "python3")) {
    if (Get-Command $cmd -ErrorAction SilentlyContinue) {
        $v = (Invoke-Native $cmd @("--version")).Trim()
        if ($v -match "^Python [0-9]") { $systemPython = $v; break }
    }
}
if ($systemPython) {
    log "System python on PATH: $systemPython (left untouched)"
    Write-Host "     Your default python is $($systemPython -replace 'Python ', '') - it will not be changed"
}

function Find-Python312 {
    # --no-project  so a pyproject.toml in the working directory cannot redirect
    #               the answer to an unrelated project environment
    # --system      so a .venv in the working directory is not mistaken for an
    #               interpreter; a venv cannot be used as a base for building
    #               the plugin's own environment. uv-managed installs are still
    #               included, only virtualenvs are excluded.
    $out = Invoke-Native "uv" @("python", "find", $PY_TARGET, "--no-project", "--system")
    if ($script:LastNativeExit -eq 0) {
        $path = $out.Trim()
        if ($path -and (Test-Path $path)) { return $path }
    }
    return $null
}

function Install-PythonViaUv {
    log "Installing managed Python $PY_TARGET via uv..."
    Write-Host "Python $PY_TARGET not available - downloading a managed copy (~30MB)..."
    Write-Host "  (installs under $env:APPDATA\uv\python - PATH and the py launcher are untouched)"
    Write-Host ""

    # --no-bin: do not add even a versioned python3.12.exe shim to ~/.local/bin.
    #   uv discovers its own managed installs without one, so the interpreter
    #   stays completely invisible to the user's shell. (--default, which WOULD
    #   take over bare `python`, is never passed.)
    # uv writes progress to stderr; flatten ErrorRecords so PowerShell does not
    # wrap each progress line in "At line:N char:N" decoration.
    & uv python install $PY_TARGET --no-bin 2>&1 |
        ForEach-Object {
            if ($_ -is [System.Management.Automation.ErrorRecord]) { $_.ToString() } else { "$_" }
        } | Out-Host
    $code = $LASTEXITCODE
    log "uv python install exit: $code"
    return ($code -eq 0)
}

# ── Legacy system-wide fallbacks ───────────────────────────────────────────────
# Only reached when uv cannot provision Python at all (blocked download host,
# for example). These install a real system Python, so they are deliberately
# last and deliberately do not prepend to PATH.

function Remove-GhostPythonRegistry {
    $regRoots = @("HKCU:\Software\Python\PythonCore", "HKLM:\Software\Python\PythonCore")
    foreach ($root in $regRoots) {
        if (-not (Test-Path $root)) { continue }
        $versions = Get-ChildItem $root -ErrorAction SilentlyContinue
        foreach ($ver in $versions) {
            $installPath = "$($ver.PSPath)\InstallPath"
            if (Test-Path $installPath) {
                $entry = Get-ItemProperty $installPath -ErrorAction SilentlyContinue
                $folder = $entry.'(default)'
                if ($folder -and -not (Test-Path $folder)) {
                    log "Removing ghost registry entry: $folder"
                    Write-Host "Removing ghost registry entry: $folder"
                    Remove-Item $ver.PSPath -Recurse -Force -ErrorAction SilentlyContinue
                }
            }
        }
    }
}

function Get-RegisteredPythonVersion {
    try {
        $hasRegistry = (Test-Path "HKCU:\Software\Python\PythonCore") -or
                       (Test-Path "HKLM:\Software\Python\PythonCore")
        if (-not $hasRegistry) {
            log "No Python registry entries - skipping MSI check"
            return $null
        }

        $products = Get-WmiObject -Class Win32_Product -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -like "Python 3.12*Core*" }
        if ($products) {
            $ver = @($products)[0].Version
            if (-not $ver) { return $null }
            $parts = $ver.Split(".")
            if ($parts.Length -ge 3) {
                $buildNum = [int]$parts[2]
                $patchVersion = [Math]::Floor($buildNum / 1000)
                $pyVer = "$($parts[0]).$($parts[1]).$patchVersion"
                log "Found registered Python version: $pyVer"
                return $pyVer
            }
        }
    } catch {
        log "Could not check MSI records: $_"
    }
    return $null
}

function Install-Python-Via-Direct-Download {
    log "Trying direct download from python.org..."
    Write-Host "Downloading Python 3.12 from python.org..."

    Remove-GhostPythonRegistry

    $pythonVersion = "3.12.0"

    # Match an already-registered version to avoid MSI error 1638.
    $registeredVersion = Get-RegisteredPythonVersion
    if ($registeredVersion) {
        log "MSI has Python $registeredVersion registered - downloading same version"
        Write-Host "Detected registered Python $registeredVersion - downloading matching version..."
        $pythonVersion = $registeredVersion
    }

    $pythonUrl     = "https://www.python.org/ftp/python/$pythonVersion/python-$pythonVersion-amd64.exe"
    $installerPath = "$env:TEMP\python-$pythonVersion-amd64.exe"

    log "Downloading: $pythonUrl"
    Write-Host "Downloading Python $pythonVersion (~25MB)..."

    try {
        $wc = New-Object System.Net.WebClient
        $wc.DownloadFile($pythonUrl, $installerPath)
        $wc.Dispose()

        if (-not (Test-Path $installerPath)) {
            log "Download failed - installer not found"
            Write-Host "Download failed"
            return $false
        }

        Write-Host "Running Python installer silently..."
        # PrependPath=0 on purpose: uv discovers this install through the
        # Windows registry (PEP 514), so there is no reason to change which
        # python the user's shell resolves.
        $installArgs = "/quiet InstallAllUsers=0 PrependPath=0 Include_test=0"
        $proc = Start-Process -FilePath $installerPath -ArgumentList $installArgs -Wait -PassThru

        if ($proc.ExitCode -eq 1638) {
            log "Exit 1638 - attempting reinstall with REINSTALL=ALL..."
            Write-Host "Forcing reinstall..."
            $reinstallArgs = "/quiet REINSTALL=ALL REINSTALLMODE=amus InstallAllUsers=0 PrependPath=0 Include_test=0"
            $proc = Start-Process -FilePath $installerPath -ArgumentList $reinstallArgs -Wait -PassThru
            log "Reinstall exit code: $($proc.ExitCode)"
        }

        Remove-Item $installerPath -Force -ErrorAction SilentlyContinue

        if ($proc.ExitCode -ne 0) {
            log "Python installer failed (exit: $($proc.ExitCode))"
            Write-Host "Python installer failed (exit: $($proc.ExitCode))"
            return $false
        }

        log "Python $pythonVersion installed via direct download (not added to PATH)"
        Write-Host "[OK] Python $pythonVersion installed"
        return $true
    }
    catch {
        log "Direct download failed: $_"
        Write-Host "Direct download failed: $_"
        Remove-Item $installerPath -Force -ErrorAction SilentlyContinue
        return $false
    }
}

function Install-Python-Via-Winget {
    log "Trying winget..."
    Write-Host "Trying winget..."

    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        log "winget not available"
        Write-Host "winget not available - skipping"
        return $false
    }

    Write-Host "[WARN] The winget package adds Python 3.12 to PATH, which changes"
    Write-Host "       which python your shell resolves. This is the last resort."

    winget source update --disable-interactivity 2>$null
    Start-Sleep -Seconds 2

    winget install Python.Python.3.12 `
        --silent `
        --accept-package-agreements `
        --accept-source-agreements `
        --disable-interactivity

    if ($LASTEXITCODE -ne 0) {
        log "winget install failed (exit: $LASTEXITCODE)"
        Write-Host "winget failed (exit: $LASTEXITCODE)"
        return $false
    }

    Refresh-Path
    log "Python 3.12 installed via winget"
    Write-Host "[OK] Python 3.12 installed via winget"
    return $true
}

# ── Ensure a 3.12 interpreter exists for uv ────────────────────────────────────

$pythonPath = Find-Python312

if ($pythonPath) {
    log "Python $PY_TARGET already available at: $pythonPath"
    Write-Host "[OK] Python $PY_TARGET available"
    Write-Host "     $pythonPath"
}
else {
    log "No Python $PY_TARGET found - provisioning"

    $uvInstallOk = $false
    try { $uvInstallOk = Install-PythonViaUv } catch { log "uv python install threw: $_"; $uvInstallOk = $false }
    $global:LASTEXITCODE = 0

    if ($uvInstallOk) { $pythonPath = Find-Python312 }

    # uv said it installed Python but cannot then see it. Installing a system
    # Python would not help - uv is the thing that has to find it - and would
    # modify the machine for nothing, so stop here with a real diagnostic.
    if ($uvInstallOk -and -not $pythonPath) {
        log "uv python install reported success but $PY_TARGET is not discoverable - uv looks broken"
        Write-Host ""
        Write-Host "[FAIL] uv installed Python $PY_TARGET but cannot find it afterwards."
        Write-Host "       This usually means the uv installation itself is damaged."
        Write-Host ""
        Write-Host "  Check what uv reports:"
        Write-Host "    uv --version"
        Write-Host "    uv python list"
        Write-Host ""
        Write-Host "  Reinstalling uv normally fixes it:"
        Write-Host "    irm https://astral.sh/uv/install.ps1 | iex"
        Write-Host ""
        exit 1
    }

    # uv genuinely could not download (blocked host, offline mirror). A real
    # system install still helps, because uv discovers registered installs
    # through the Windows registry.
    $directOk = $false
    if (-not $pythonPath) {
        log "uv could not download Python - falling back to a system install"
        Write-Host ""
        Write-Host "[WARN] uv could not download Python $PY_TARGET - trying a system install..."

        try { $directOk = Install-Python-Via-Direct-Download } catch { $directOk = $false }
        $global:LASTEXITCODE = 0
        if ($directOk) {
            Refresh-Path
            $pythonPath = Find-Python312
        }
    }

    # winget last, and ONLY if the direct download failed outright - otherwise a
    # working install would be layered over with a second copy that also
    # rewrites PATH.
    if (-not $pythonPath -and -not $directOk) {
        $wingetOk = $false
        try { $wingetOk = Install-Python-Via-Winget } catch { $wingetOk = $false }
        $global:LASTEXITCODE = 0
        if ($wingetOk) { $pythonPath = Find-Python312 }
    }

    if (-not $pythonPath) {
        log "All Python provisioning methods failed"
        Write-Host ""
        Write-Host "[FAIL] Could not provide Python $PY_TARGET"
        Write-Host "Install it manually with either:"
        Write-Host "  uv python install $PY_TARGET"
        Write-Host "  https://www.python.org/downloads/"
        Write-Host "Then re-run the installer."
        exit 1
    }

    log "Python $PY_TARGET ready at: $pythonPath"
    Write-Host "[OK] Python $PY_TARGET ready"
    Write-Host "     $pythonPath"
}

# ── Done ───────────────────────────────────────────────────────────────────────

log "Prerequisites check complete"
Write-Host ""
Write-Host "======================================"
Write-Host "  [OK] Prerequisites Ready!"
Write-Host "======================================"
Write-Host ""
Write-Host "Log saved to: $LOG_FILE"
exit 0
