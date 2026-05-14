# CloudByte Prerequisites Script (Windows PowerShell)
# Checks and installs Python 3.12 and uv
# Run directly or via skill - no plugin context required

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

log "=== CloudByte Prerequisites Check ==="
log "OS: Windows"
log "Home: $USER_HOME"
Write-Host ""
Write-Host "======================================"
Write-Host "  CloudByte Prerequisites Check"
Write-Host "======================================"
Write-Host ""

# ── Python ─────────────────────────────────────────────────────────────────────

function Install-Python-Via-Winget {
    log "Trying winget..."
    Write-Host "Trying winget..."

    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        log "winget not available"
        Write-Host "winget not available - skipping"
        return $false
    }

    Write-Host "Initializing winget source..."
    winget source update --disable-interactivity 2>$null
    Start-Sleep -Seconds 2

    winget install Python.Python.3.12 `
        --silent `
        --accept-package-agreements `
        --accept-source-agreements `
        --disable-interactivity

    if ($LASTEXITCODE -ne 0) {
        log "winget install failed (exit: $LASTEXITCODE)"
        Write-Host "winget failed (exit: $LASTEXITCODE) - trying fallback..."
        return $false
    }

    $env:PATH = [System.Environment]::GetEnvironmentVariable("PATH", "Machine") + ";" +
                [System.Environment]::GetEnvironmentVariable("PATH", "User")

    log "Python 3.12 installed via winget"
    Write-Host "[OK] Python 3.12 installed via winget"
    return $true
}

function Install-Python-Via-Direct-Download {
    log "Trying direct download from python.org..."
    Write-Host "Downloading Python 3.12 directly from python.org (~25MB)..."

    $pythonUrl     = "https://www.python.org/ftp/python/3.12.0/python-3.12.0-amd64.exe"
    $installerPath = "$env:TEMP\python-3.12.0-amd64.exe"

    try {
        Invoke-WebRequest -Uri $pythonUrl -OutFile $installerPath -UseBasicParsing
        if (-not (Test-Path $installerPath)) {
            log "Download failed - installer not found"
            Write-Host "Download failed"
            return $false
        }

        Write-Host "Running Python installer silently..."
        $installArgs = "/quiet InstallAllUsers=0 PrependPath=1 Include_test=0"
        $proc = Start-Process -FilePath $installerPath -ArgumentList $installArgs -Wait -PassThru
        Remove-Item $installerPath -Force -ErrorAction SilentlyContinue

        if ($proc.ExitCode -ne 0) {
            log "Python installer failed (exit: $($proc.ExitCode))"
            Write-Host "Python installer failed (exit: $($proc.ExitCode))"
            return $false
        }

        $env:PATH = [System.Environment]::GetEnvironmentVariable("PATH", "Machine") + ";" +
                    [System.Environment]::GetEnvironmentVariable("PATH", "User") + ";" +
                    "$env:LOCALAPPDATA\Programs\Python\Python312;" +
                    "$env:LOCALAPPDATA\Programs\Python\Python312\Scripts"

        log "Python 3.12 installed via direct download"
        Write-Host "[OK] Python 3.12 installed via direct download"
        return $true
    }
    catch {
        log "Direct download failed: $_"
        Write-Host "Direct download failed: $_"
        return $false
    }
}

function Install-Python {
    log "Python not found - installing 3.12..."
    Write-Host "Python not found - installing 3.12..."

    $wingetOk = Install-Python-Via-Winget
    if ($wingetOk) { return $true }

    log "Falling back to direct download..."
    Write-Host "Falling back to direct download..."
    $directOk = Install-Python-Via-Direct-Download
    if ($directOk) { return $true }

    log "All install methods failed"
    Write-Host ""
    Write-Host "[FAIL] Could not install Python automatically"
    Write-Host "Please install Python 3.12 manually: https://www.python.org/downloads/"
    Write-Host "Then re-run /cloudbyte-claude-plugin-install"
    return $false
}

function Test-PythonWorks {
    param($cmd)
    try {
        $ver = & $cmd --version 2>&1
        return ($ver -match "^Python [0-9]")
    }
    catch {
        return $false
    }
}

Write-Host "-- Checking Python ----------------------"

$PYTHON_CMD     = $null
$PYTHON_VERSION = $null

if ((Get-Command python3 -ErrorAction SilentlyContinue) -and (Test-PythonWorks "python3")) {
    $PYTHON_CMD     = "python3"
    $PYTHON_VERSION = (python3 --version 2>&1) -replace "Python ", ""
    log "Python found: $PYTHON_VERSION (python3)"
    Write-Host "[OK] Python $PYTHON_VERSION"
}
elseif ((Get-Command python -ErrorAction SilentlyContinue) -and (Test-PythonWorks "python")) {
    $PYTHON_CMD     = "python"
    $PYTHON_VERSION = (python --version 2>&1) -replace "Python ", ""
    log "Python found: $PYTHON_VERSION (python)"
    Write-Host "[OK] Python $PYTHON_VERSION"
}
else {
    log "Python not found or Windows Store stub detected"
    $installed = Install-Python
    if (-not $installed) { exit 1 }

    $env:PATH = [System.Environment]::GetEnvironmentVariable("PATH", "Machine") + ";" +
                [System.Environment]::GetEnvironmentVariable("PATH", "User") + ";" +
                "$env:LOCALAPPDATA\Programs\Python\Python312;" +
                "$env:LOCALAPPDATA\Programs\Python\Python312\Scripts"

    if ((Get-Command python3 -ErrorAction SilentlyContinue) -and (Test-PythonWorks "python3")) {
        $PYTHON_CMD     = "python3"
        $PYTHON_VERSION = (python3 --version 2>&1) -replace "Python ", ""
        log "Python now available: $PYTHON_VERSION"
        Write-Host "[OK] Python $PYTHON_VERSION ready"
    }
    elseif ((Get-Command python -ErrorAction SilentlyContinue) -and (Test-PythonWorks "python")) {
        $PYTHON_CMD     = "python"
        $PYTHON_VERSION = (python --version 2>&1) -replace "Python ", ""
        log "Python now available: $PYTHON_VERSION"
        Write-Host "[OK] Python $PYTHON_VERSION ready"
    }
    else {
        log "Python not available after install"
        Write-Host "[FAIL] Python not available after install"
        Write-Host "Please install Python 3.12 manually: https://www.python.org/downloads/"
        exit 1
    }
}

# Version check - 3.10+ required
$versionParts = $PYTHON_VERSION.Split(".")
$PYTHON_MAJOR = [int]$versionParts[0]
$PYTHON_MINOR = [int]$versionParts[1]

$VERSION_OK = $false
if ($PYTHON_MAJOR -gt 3)                               { $VERSION_OK = $true }
elseif ($PYTHON_MAJOR -eq 3 -and $PYTHON_MINOR -ge 10) { $VERSION_OK = $true }

if (-not $VERSION_OK) {
    log "Python $PYTHON_VERSION too old - need 3.10+"
    Write-Host "[FAIL] Python $PYTHON_VERSION is too old. Need 3.10+"
    Write-Host "Please install Python 3.12: https://www.python.org/downloads/"
    exit 1
}

# ── uv ─────────────────────────────────────────────────────────────────────────

Write-Host ""
Write-Host "-- Checking uv --------------------------"

if (Get-Command uv -ErrorAction SilentlyContinue) {
    $UV_VERSION = (uv --version 2>&1)
    log "uv found: $UV_VERSION"
    Write-Host "[OK] $UV_VERSION"
}
else {
    log "uv not found - installing..."
    Write-Host "uv not found - installing..."

    try {
        powershell -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"

        $env:PATH = "$env:USERPROFILE\.local\bin;" +
                    "$env:USERPROFILE\.cargo\bin;" +
                    [System.Environment]::GetEnvironmentVariable("PATH", "Machine") + ";" +
                    [System.Environment]::GetEnvironmentVariable("PATH", "User")

        if (Get-Command uv -ErrorAction SilentlyContinue) {
            $UV_VERSION = (uv --version 2>&1)
            log "uv installed: $UV_VERSION"
            Write-Host "[OK] $UV_VERSION installed"
        }
        else {
            log "uv installed but not on PATH yet"
            Write-Host "[WARN] uv installed but needs terminal restart to be on PATH"
        }
    }
    catch {
        log "Failed to install uv: $_"
        Write-Host "[FAIL] Failed to install uv"
        Write-Host "Please install manually: https://docs.astral.sh/uv/getting-started/installation/"
        exit 1
    }
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