# CloudByte Prerequisites Script (Windows PowerShell)
# Checks and installs Python 3.12 and uv
# Run directly or via skill - no plugin context required

# Colors
$RED    = "`e[31m"
$GREEN  = "`e[32m"
$YELLOW = "`e[33m"
$NC     = "`e[0m"

function Write-Color {
    param($Color, $Message)
    Write-Host "$Color$Message$NC"
}

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
Write-Host "╔══════════════════════════════════════╗"
Write-Host "║   CloudByte Prerequisites Check      ║"
Write-Host "╚══════════════════════════════════════╝"
Write-Host ""

# ── Python ─────────────────────────────────────────────────────────────────────

function Install-Python {
    log "Python not found - installing 3.12..."
    Write-Color $YELLOW "Python not found - installing 3.12..."

    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        log "winget not found"
        Write-Color $RED "✗ winget not found"
        Write-Host "Please install Python 3.12 manually: https://www.python.org/downloads/"
        return $false
    }

    log "Installing Python 3.12 via winget..."
    Write-Host "Installing Python 3.12 via winget..."

    winget install Python.Python.3.12 `
        --silent `
        --accept-package-agreements `
        --accept-source-agreements

    if ($LASTEXITCODE -ne 0) {
        log "Failed to install Python 3.12 (exit: $LASTEXITCODE)"
        Write-Color $RED "✗ Failed to install Python 3.12"
        Write-Host "Please install manually: https://www.python.org/downloads/"
        return $false
    }

    # Refresh PATH
    $env:PATH = [System.Environment]::GetEnvironmentVariable("PATH", "Machine") + ";" +
                [System.Environment]::GetEnvironmentVariable("PATH", "User")

    log "Python 3.12 installed successfully"
    Write-Color $GREEN "✓ Python 3.12 installed"
    return $true
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

Write-Host "── Checking Python ──────────────────────"

$PYTHON_CMD     = $null
$PYTHON_VERSION = $null

if ((Get-Command python3 -ErrorAction SilentlyContinue) -and (Test-PythonWorks "python3")) {
    $PYTHON_CMD     = "python3"
    $PYTHON_VERSION = (python3 --version 2>&1) -replace "Python ", ""
    log "Python found: $PYTHON_VERSION (python3)"
    Write-Color $GREEN "✓ Python $PYTHON_VERSION"
}
elseif ((Get-Command python -ErrorAction SilentlyContinue) -and (Test-PythonWorks "python")) {
    $PYTHON_CMD     = "python"
    $PYTHON_VERSION = (python --version 2>&1) -replace "Python ", ""
    log "Python found: $PYTHON_VERSION (python)"
    Write-Color $GREEN "✓ Python $PYTHON_VERSION"
}
else {
    log "Python not found or Windows Store stub detected"
    $installed = Install-Python
    if (-not $installed) { exit 1 }

    # Refresh PATH
    $env:PATH = [System.Environment]::GetEnvironmentVariable("PATH", "Machine") + ";" +
                [System.Environment]::GetEnvironmentVariable("PATH", "User")

    if ((Get-Command python3 -ErrorAction SilentlyContinue) -and (Test-PythonWorks "python3")) {
        $PYTHON_CMD     = "python3"
        $PYTHON_VERSION = (python3 --version 2>&1) -replace "Python ", ""
        log "Python now available: $PYTHON_VERSION"
        Write-Color $GREEN "✓ Python $PYTHON_VERSION ready"
    }
    elseif ((Get-Command python -ErrorAction SilentlyContinue) -and (Test-PythonWorks "python")) {
        $PYTHON_CMD     = "python"
        $PYTHON_VERSION = (python --version 2>&1) -replace "Python ", ""
        log "Python now available: $PYTHON_VERSION"
        Write-Color $GREEN "✓ Python $PYTHON_VERSION ready"
    }
    else {
        log "Python not available after install"
        Write-Color $RED "✗ Python not available after install"
        exit 1
    }
}

# Version check - 3.10+ required
$versionParts = $PYTHON_VERSION.Split(".")
$PYTHON_MAJOR = [int]$versionParts[0]
$PYTHON_MINOR = [int]$versionParts[1]

$VERSION_OK = $false
if ($PYTHON_MAJOR -gt 3)                          { $VERSION_OK = $true }
elseif ($PYTHON_MAJOR -eq 3 -and $PYTHON_MINOR -ge 10) { $VERSION_OK = $true }

if (-not $VERSION_OK) {
    log "Python $PYTHON_VERSION too old - need 3.10+"
    Write-Color $RED "✗ Python $PYTHON_VERSION is too old. Need 3.10+"
    Write-Host "Please install Python 3.12: https://www.python.org/downloads/"
    exit 1
}

# ── uv ─────────────────────────────────────────────────────────────────────────

Write-Host ""
Write-Host "── Checking uv ──────────────────────────"

if (Get-Command uv -ErrorAction SilentlyContinue) {
    $UV_VERSION = (uv --version 2>&1)
    log "uv found: $UV_VERSION"
    Write-Color $GREEN "✓ $UV_VERSION"
}
else {
    log "uv not found - installing..."
    Write-Color $YELLOW "uv not found - installing..."

    try {
        powershell -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"

        # Refresh PATH after install
        $env:PATH = "$env:USERPROFILE\.local\bin;" +
                    "$env:USERPROFILE\.cargo\bin;" +
                    [System.Environment]::GetEnvironmentVariable("PATH", "Machine") + ";" +
                    [System.Environment]::GetEnvironmentVariable("PATH", "User")

        if (Get-Command uv -ErrorAction SilentlyContinue) {
            $UV_VERSION = (uv --version 2>&1)
            log "uv installed: $UV_VERSION"
            Write-Color $GREEN "✓ $UV_VERSION installed"
        }
        else {
            log "uv installed but not on PATH yet"
            Write-Color $YELLOW "⚠ uv installed but needs terminal restart to be on PATH"
        }
    }
    catch {
        log "Failed to install uv: $_"
        Write-Color $RED "✗ Failed to install uv"
        Write-Host "Please install manually: https://docs.astral.sh/uv/getting-started/installation/"
        exit 1
    }
}

# ── Done ───────────────────────────────────────────────────────────────────────

log "Prerequisites check complete"
Write-Host ""
Write-Host "╔══════════════════════════════════════╗"
Write-Host "║   ✓  Prerequisites Ready!            ║"
Write-Host "╚══════════════════════════════════════╝"
Write-Host ""
Write-Host "Log saved to: $LOG_FILE"
exit 0