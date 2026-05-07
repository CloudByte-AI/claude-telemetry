# CloudByte Setup Validation Script (Windows PowerShell)
# Pure installation setup - auto-installs Python and uv if missing

# Plugin root - use env var, fallback to cache discovery
$PLUGIN_ROOT = $env:CLAUDE_PLUGIN_ROOT
if (-not $PLUGIN_ROOT) {
    $cachePath = "$env:USERPROFILE\.claude\plugins\cache\claude-telemetry\claude-telemetry"
    if (Test-Path $cachePath) {
        $PLUGIN_ROOT = (Get-ChildItem $cachePath |
            Sort-Object Name -Descending |
            Select-Object -First 1).FullName
    }
}

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
$INIT_FILE     = "$CLOUDBYTE_DIR\.initialized"
$VERSION_FILE  = "$CLOUDBYTE_DIR\.version"

New-Item -ItemType Directory -Force -Path $LOG_DIR | Out-Null
New-Item -ItemType Directory -Force -Path $SETUP_LOG_DIR | Out-Null

# Trace - written before anything else
$ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
Add-Content "$LOG_DIR\hook_trace.log" "[$ts] PS1: hook triggered - PLUGIN_ROOT=$PLUGIN_ROOT"

# Get current plugin version from .claude-plugin/plugin.json
function Get-PluginVersion {
    try {
        $jsonPath = "$PLUGIN_ROOT\.claude-plugin\plugin.json"
        if (-not (Test-Path $jsonPath)) {
            return "unknown"
        }
        $json = Get-Content $jsonPath -Raw | ConvertFrom-Json
        $ver = $json.version
        if (-not $ver -or $ver -eq "") {
            return "unknown"
        }
        return $ver.Trim()
    }
    catch {
        return "unknown"
    }
}

$CURRENT_VERSION = Get-PluginVersion

# Early exit if already initialized for this version
if ((Test-Path $INIT_FILE) -and (Test-Path $VERSION_FILE)) {
    $SAVED_VERSION = Get-Content $VERSION_FILE -ErrorAction SilentlyContinue
    if ($SAVED_VERSION -eq $CURRENT_VERSION) {
        $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
        Add-Content "$LOG_DIR\hook_trace.log" "[$ts] PS1: already initialized - exit 0"
        exit 0
    }
    Write-Host "Version changed: $SAVED_VERSION to $CURRENT_VERSION"
    Write-Host "Re-running setup for new version..."
}

# Logging - setup/setup-YYYY-MM-DD.log
$DATE_STR = Get-Date -Format "yyyy-MM-dd"
$LOG_FILE = "$SETUP_LOG_DIR\setup-$DATE_STR.log"

function log {
    param($Message)
    $ts   = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "[$ts] $Message"
    Write-Host $line
    Add-Content -Path $LOG_FILE -Value $line
}

log "=== CloudByte Setup started ==="
log "Version: $CURRENT_VERSION"
log "OS: Windows"
log "Home: $USER_HOME"
log "Plugin: $PLUGIN_ROOT"
Write-Host ""

Write-Host "CloudByte Setup - Prerequisites Check"
Write-Host "=========================================="
Write-Host ""
Write-Host "Plugin directory: $PLUGIN_ROOT"
Write-Host ""

# Python install function
function Install-Python {
    log "Python not found - installing automatically..."
    Write-Color $YELLOW "Python not found - installing automatically..."

    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        log "winget not found"
        Write-Color $RED "winget not found"
        Write-Host "Please install Python manually: https://www.python.org/downloads/"
        return $false
    }

    log "Installing Python via winget..."
    Write-Host "Installing Python via winget..."

    winget install Python.Python.3.12 `
        --silent `
        --accept-package-agreements `
        --accept-source-agreements

    if ($LASTEXITCODE -ne 0) {
        log "Failed to install Python (exit: $LASTEXITCODE)"
        Write-Color $RED "Failed to install Python automatically"
        Write-Host "Please install manually: https://www.python.org/downloads/"
        return $false
    }

    # Refresh PATH
    $env:PATH = [System.Environment]::GetEnvironmentVariable("PATH", "Machine") + ";" +
                [System.Environment]::GetEnvironmentVariable("PATH", "User")

    log "Python installed successfully"
    Write-Color $GREEN "Python installed successfully"
    return $true
}

# Python check - detects Windows Store stub
Write-Host "Checking Python..."

$PYTHON_CMD     = $null
$PYTHON_VERSION = $null

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

if ((Get-Command python3 -ErrorAction SilentlyContinue) -and (Test-PythonWorks "python3")) {
    $PYTHON_CMD     = "python3"
    $PYTHON_VERSION = (python3 --version 2>&1) -replace "Python ", ""
    log "Python found: $PYTHON_VERSION (python3)"
    Write-Color $GREEN "Python $PYTHON_VERSION found"
}
elseif ((Get-Command python -ErrorAction SilentlyContinue) -and (Test-PythonWorks "python")) {
    $PYTHON_CMD     = "python"
    $PYTHON_VERSION = (python --version 2>&1) -replace "Python ", ""
    log "Python found: $PYTHON_VERSION (python)"
    Write-Color $GREEN "Python $PYTHON_VERSION found"
}
else {
    log "Python not found or Windows Store stub detected"
    $installed = Install-Python
    if (-not $installed) {
        exit 1
    }

    # Refresh PATH
    $env:PATH = [System.Environment]::GetEnvironmentVariable("PATH", "Machine") + ";" +
                [System.Environment]::GetEnvironmentVariable("PATH", "User")

    if ((Get-Command python3 -ErrorAction SilentlyContinue) -and (Test-PythonWorks "python3")) {
        $PYTHON_CMD     = "python3"
        $PYTHON_VERSION = (python3 --version 2>&1) -replace "Python ", ""
        log "Python now available: $PYTHON_VERSION"
        Write-Color $GREEN "Python $PYTHON_VERSION ready"
    }
    elseif ((Get-Command python -ErrorAction SilentlyContinue) -and (Test-PythonWorks "python")) {
        $PYTHON_CMD     = "python"
        $PYTHON_VERSION = (python --version 2>&1) -replace "Python ", ""
        log "Python now available: $PYTHON_VERSION"
        Write-Color $GREEN "Python $PYTHON_VERSION ready"
    }
    else {
        log "Python not available after install"
        Write-Color $RED "Python not available"
        exit 1
    }
}

# Python version 3.10+ check
$versionParts = $PYTHON_VERSION.Split(".")
$PYTHON_MAJOR = [int]$versionParts[0]
$PYTHON_MINOR = [int]$versionParts[1]

$VERSION_OK = $false
if ($PYTHON_MAJOR -gt 3) {
    $VERSION_OK = $true
}
elseif ($PYTHON_MAJOR -eq 3 -and $PYTHON_MINOR -ge 10) {
    $VERSION_OK = $true
}

if (-not $VERSION_OK) {
    log "Python $PYTHON_VERSION too old - need 3.10+"
    Write-Color $RED "Python $PYTHON_VERSION is too old. Need 3.10+"
    Write-Host "Please upgrade: https://www.python.org/downloads/"
    exit 1
}

# uv check and auto-install
Write-Host ""
Write-Host "Checking uv..."

$USE_UV = $false

if (Get-Command uv -ErrorAction SilentlyContinue) {
    $UV_VERSION = (uv --version 2>&1)
    log "uv: $UV_VERSION"
    Write-Color $GREEN "$UV_VERSION found"
    $USE_UV = $true
}
else {
    log "uv not found - installing automatically..."
    Write-Color $YELLOW "uv not found - installing..."

    try {
        powershell -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"

        $env:PATH = "$env:USERPROFILE\.local\bin;" +
                    "$env:USERPROFILE\.cargo\bin;" +
                    $env:PATH

        if (Get-Command uv -ErrorAction SilentlyContinue) {
            log "uv installed successfully"
            Write-Color $GREEN "uv installed successfully"
            $USE_UV = $true
        }
        else {
            log "uv installed but not on PATH yet"
            Write-Color $YELLOW "uv installed - may need terminal restart"
            $USE_UV = $false
        }
    }
    catch {
        log "Failed to install uv: $_"
        Write-Color $RED "Failed to install uv automatically"
        Write-Host "Please install manually: https://docs.astral.sh/uv/getting-started/installation/"
        exit 1
    }
}

# Run setup
Write-Host ""
Write-Host "=========================================="
Write-Color $GREEN "Prerequisites OK - Running Setup"
Write-Host "=========================================="
Write-Host ""

Set-Location $PLUGIN_ROOT

if ($USE_UV) {
    log "Running setup with uv..."
    Write-Host "Running setup with uv..."
    uv run -m src.main setup
    $EXIT_CODE = $LASTEXITCODE
}
else {
    log "Running setup with $PYTHON_CMD..."
    Write-Host "Running setup with $PYTHON_CMD..."
    & $PYTHON_CMD -m src.main setup
    $EXIT_CODE = $LASTEXITCODE
}

# Write .initialized on success
if ($EXIT_CODE -eq 0) {
    Set-Content -Path $VERSION_FILE -Value $CURRENT_VERSION
    New-Item -ItemType File -Force -Path $INIT_FILE | Out-Null
    log "Initialized version $CURRENT_VERSION"
    log "Setup completed successfully"
    Write-Host ""
    Write-Host "=========================================="
    Write-Color $GREEN "CloudByte Setup Complete!"
    Write-Host "=========================================="
    Write-Host ""
    Write-Host "Log saved to: $LOG_FILE"
    exit 0
}
else {
    log "Setup failed with exit code $EXIT_CODE"
    Write-Host ""
    Write-Host "=========================================="
    Write-Color $RED "Setup Failed (exit code: $EXIT_CODE)"
    Write-Host "=========================================="
    Write-Host ""
    Write-Host "Log saved to: $LOG_FILE"
    exit $EXIT_CODE
}
