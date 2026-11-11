# CloudByte Prerequisites Script (Windows PowerShell)
# Ensures uv is installed, then ensures a Python 3.12 interpreter is available
# TO UV - not to the user's shell.
#
# The plugin environment is built on a signed Python, so that Windows Smart App
# Control or an App Control policy, now or later, keeps starting it: an
# installed signed Python 3.12 is used, otherwise python.org's signed installer
# runs for this user only (no PATH change, no py launcher, no file
# associations). uv's own Python (`uv python install`, under %APPDATA%\uv\python,
# also off PATH) is the fallback while Smart App Control is off. A pre-existing
# Python of any version is left exactly as it is: an older interpreter (3.9,
# say) is no longer an error, because nothing the plugin runs depends on the
# user's default `python`.
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

# Download $Url to $OutFile, retrying network failures with exponential backoff.
# Kept identical in bootstrap.ps1, install.ps1 and validate.ps1: each is a
# standalone downloaded file, so there is nothing to share it from.
#
# Throws the last error when every attempt failed. An HTTP 4xx other than 408
# or 429 (a ref or path that does not exist) is thrown at once, since retrying
# cannot fix it. -TimeoutSec 0 means no limit, for large files on slow links.
function Save-Download {
    param([string] $Url, [string] $OutFile, [int] $TimeoutSec = 120)

    $attempts = 4
    $delay    = 2
    if ($env:CLOUDBYTE_FETCH_ATTEMPTS -match '^[1-9][0-9]*$') { $attempts = [int] $env:CLOUDBYTE_FETCH_ATTEMPTS }
    if ($env:CLOUDBYTE_FETCH_DELAY -match '^[0-9]+$')         { $delay    = [int] $env:CLOUDBYTE_FETCH_DELAY }

    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    # Windows PowerShell 5.1 redraws its progress bar per chunk, which makes
    # Invoke-WebRequest many times slower. Scoped to this function only.
    $ProgressPreference = "SilentlyContinue"

    for ($attempt = 1; ; $attempt++) {
        try {
            Invoke-WebRequest -Uri $Url -OutFile $OutFile -UseBasicParsing -TimeoutSec $TimeoutSec -ErrorAction Stop
            return
        }
        catch {
            # 5.1 raises WebException and 7.x HttpResponseException; both carry
            # .Response.StatusCode when the server answered, and no Response
            # when the connection itself failed.
            $status = 0
            if ($_.Exception.Response) { $status = [int] $_.Exception.Response.StatusCode }
            if ($status -ge 400 -and $status -lt 500 -and $status -ne 408 -and $status -ne 429) { throw }
            if ($attempt -ge $attempts) { throw }

            Write-Host "  Download failed ($($_.Exception.Message))" -ForegroundColor Yellow
            Write-Host "  Retrying in ${delay}s (attempt $($attempt + 1) of $attempts)..." -ForegroundColor Yellow
            Start-Sleep -Seconds $delay
            $delay *= 2
        }
    }
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
            Save-Download -Url "https://astral.sh/uv/install.ps1" -OutFile $uvInstaller
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

# Smart App Control (Windows 11) starts only programs that are signed or that
# Microsoft already trusts. The Pythons `uv python install` downloads are not
# signed, so on such a machine every plugin hook would die before any plugin
# code runs. Smart App Control can be switched on at any time, and a company
# App Control policy acts the same way, so the plugin is built on a signed
# python.org Python whenever one can be installed; uv's own Python is only the
# fallback while Smart App Control is off.
# VerifiedAndReputablePolicyState: 0 off, 1 on, 2 evaluation. Evaluation blocks
# nothing yet, but Windows switches it on by itself, so it counts as on.
function Test-SmartAppControl {
    try {
        $state = (Get-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Control\CI\Policy" `
            -Name "VerifiedAndReputablePolicyState" -ErrorAction Stop).VerifiedAndReputablePolicyState
        return ($state -eq 1 -or $state -eq 2)
    }
    catch { return $false }
}

$SacOn = Test-SmartAppControl
if ($SacOn) {
    log "Smart App Control is on or evaluating - only a signed Python will be used"
    Write-Host "     Smart App Control is on - a signed Python from python.org will be used"
}

# python.org revoked the signing certificate of these releases: Windows may
# refuse to run them, so they never count as signed. Kept identical in install.ps1.
$REVOKED_PYTHON_VERSIONS = @("3.12.8", "3.13.1")
# The last Python 3.12 release with Windows installers.
$PYTHON_ORG_VERSION = "3.12.10"

# A Python $PY_TARGET for building the plugin environment, or $null.
# -SignedOnly (implied under Smart App Control): a system install, signed, not
# a revoked release, and one that Windows actually starts.
function Find-Python312 {
    param([switch] $SignedOnly)

    $signedOnly = $SignedOnly -or $SacOn
    # --no-project  so a pyproject.toml in the working directory cannot redirect
    #               the answer to an unrelated project environment
    # --system      so a .venv in the working directory is not mistaken for an
    #               interpreter; a venv cannot be used as a base for building
    #               the plugin's own environment. uv-managed installs are still
    #               included, only virtualenvs are excluded.
    $findArgs = @("python", "find", $PY_TARGET, "--no-project", "--system")
    # A uv-managed install is never signed, even one that is already on disk.
    if ($signedOnly) { $findArgs += @("--python-preference", "only-system") }
    $out = Invoke-Native "uv" $findArgs
    if ($script:LastNativeExit -ne 0) { return $null }
    $path = $out.Trim()
    if (-not $path -or -not (Test-Path $path)) { return $null }
    if (-not $signedOnly) { return $path }

    # Only a signed Python gives the plugin environment a signed launcher. Found
    # is not the same as allowed to run, so prove that too.
    if ((Get-AuthenticodeSignature -LiteralPath $path).Status -ne 'Valid') {
        log "Python at $path is not signed"
        return $null
    }
    $version = (Invoke-Native $path @("-c", "import platform; print(platform.python_version())")).Trim()
    if ($script:LastNativeExit -ne 0) {
        log "Python at $path is blocked from running (exit $($script:LastNativeExit))"
        return $null
    }
    if ($REVOKED_PYTHON_VERSIONS -contains $version) {
        log "Python $version at $path has a revoked signature"
        return $null
    }
    return $path
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

# ── System-wide installs ───────────────────────────────────────────────────────
# python.org's signed installer comes first and installs for this user only,
# with nothing beyond Python itself. winget is the last resort: its package
# adds Python to PATH.

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

function Install-Python-Via-Direct-Download {
    log "Trying direct download from python.org..."

    Remove-GhostPythonRegistry

    $pythonVersion = $PYTHON_ORG_VERSION
    $pythonUrl     = "https://www.python.org/ftp/python/$pythonVersion/python-$pythonVersion-amd64.exe"
    $installerPath = "$env:TEMP\python-$pythonVersion-amd64.exe"

    log "Downloading: $pythonUrl"
    Write-Host "Downloading Python $pythonVersion (about 26 MB)..."

    try {
        # No time limit: ~26MB can legitimately take minutes on a slow link.
        Save-Download -Url $pythonUrl -OutFile $installerPath -TimeoutSec 0

        if (-not (Test-Path $installerPath)) {
            log "Download failed - installer not found"
            Write-Host "Download failed"
            return $false
        }

        # Never run a download that the Python Software Foundation did not sign.
        $signature = Get-AuthenticodeSignature -LiteralPath $installerPath
        if ($signature.Status -ne 'Valid' -or
            "$($signature.SignerCertificate.Subject)" -notlike "*Python Software Foundation*") {
            log "Downloaded installer is not signed by the Python Software Foundation ($($signature.Status))"
            Write-Host "The downloaded Python installer is not signed by python.org - not running it"
            Remove-Item $installerPath -Force -ErrorAction SilentlyContinue
            return $false
        }

        Write-Host "Running Python installer silently..."
        # For this user only, and nothing beyond Python itself: no PATH change
        # (uv discovers the install through the Windows registry, PEP 514), no
        # py launcher, no Start menu shortcuts, no .py file associations.
        $installArgs = "/quiet InstallAllUsers=0 PrependPath=0 Include_launcher=0 Include_test=0 Shortcuts=0 AssociateFiles=0"
        $proc = Start-Process -FilePath $installerPath -ArgumentList $installArgs -Wait -PassThru

        if ($proc.ExitCode -eq 1638) {
            log "Exit 1638 - attempting reinstall with REINSTALL=ALL..."
            Write-Host "Forcing reinstall..."
            $reinstallArgs = "$installArgs REINSTALL=ALL REINSTALLMODE=amus"
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

# The Python the plugin environment is built on, or $null. A signed system
# Python first, installing python.org's when there is none; uv's own Python
# only while Smart App Control is off; winget last. Sets $script:UvPythonBroken
# when uv installed a Python that it then cannot find.
function Get-PluginPython {
    $script:UvPythonBroken = $false
    $path = Find-Python312 -SignedOnly
    if ($path) { return $path }

    log "No signed Python $PY_TARGET found - installing one from python.org"
    Write-Host "Installing a signed Python $PY_TARGET from python.org..."
    Write-Host "  (for this user only - PATH, the py launcher and file associations are untouched)"
    $directOk = $false
    try { $directOk = Install-Python-Via-Direct-Download } catch { $directOk = $false }
    $global:LASTEXITCODE = 0
    if ($directOk) {
        Refresh-Path
        $path = Find-Python312 -SignedOnly
        if ($path) { return $path }
    }

    # Smart App Control off: Windows starts unsigned programs, so uv's own
    # Python is an acceptable fallback, and lighter than winget's PATH change.
    if (-not $SacOn) {
        $path = Find-Python312
        if ($path) {
            log "No signed Python could be installed - using the Python at $path"
            return $path
        }
        $uvInstallOk = $false
        try { $uvInstallOk = Install-PythonViaUv } catch { log "uv python install threw: $_"; $uvInstallOk = $false }
        $global:LASTEXITCODE = 0
        if ($uvInstallOk) {
            $path = Find-Python312
            if ($path) { return $path }
            log "uv python install reported success but $PY_TARGET is not discoverable - uv looks broken"
            $script:UvPythonBroken = $true
            return $null
        }
    }

    # winget last, and ONLY if the direct download failed outright - otherwise a
    # working install would be layered over with a second copy that also
    # rewrites PATH.
    if (-not $directOk) {
        $wingetOk = $false
        try { $wingetOk = Install-Python-Via-Winget } catch { $wingetOk = $false }
        $global:LASTEXITCODE = 0
        if ($wingetOk) {
            $path = Find-Python312 -SignedOnly
            if (-not $path -and -not $SacOn) { $path = Find-Python312 }
            if ($path) { return $path }
        }
    }
    return $null
}

$pythonPath = Get-PluginPython

# uv said it installed Python but cannot then see it. Installing anything else
# would not help - uv is the thing that has to find it - so stop here with a
# real diagnostic.
if ($script:UvPythonBroken) {
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

if (-not $pythonPath) {
    log "All Python provisioning methods failed"
    Write-Host ""
    Write-Host "[FAIL] Could not provide Python $PY_TARGET"
    if ($SacOn) {
        # A uv-managed Python would be blocked by Smart App Control too.
        Write-Host "Install it manually from python.org (its installer is signed):"
    }
    else {
        Write-Host "Install it manually with either:"
        Write-Host "  uv python install $PY_TARGET"
    }
    Write-Host "  https://www.python.org/downloads/"
    Write-Host "Then re-run the installer."
    exit 1
}

log "Python $PY_TARGET ready at: $pythonPath"
Write-Host "[OK] Python $PY_TARGET ready"
Write-Host "     $pythonPath"

# ── Done ───────────────────────────────────────────────────────────────────────

log "Prerequisites check complete"
Write-Host ""
Write-Host "======================================"
Write-Host "  [OK] Prerequisites Ready!"
Write-Host "======================================"
Write-Host ""
Write-Host "Log saved to: $LOG_FILE"
exit 0
