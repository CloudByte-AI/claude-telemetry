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
    # Check MSI for any registered Python 3.12.x version
    try {
        # Quick registry check first - skip slow WMI if no Python registered
        $hasRegistry = (Test-Path "HKCU:\Software\Python\PythonCore") -or 
                       (Test-Path "HKLM:\Software\Python\PythonCore")
        if (-not $hasRegistry) {
            log "No Python registry entries - skipping MSI check"
            return $null
        }

        $products = Get-WmiObject -Class Win32_Product -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -like "Python 3.12*Core*" }
        if ($products) {
            # Version format is like 3.12.10150.0 - extract 3.12.x
            $ver = $products[0].Version
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

function Get-PythonDownloadUrl {
    param($version)
    return "https://www.python.org/ftp/python/$version/python-$version-amd64.exe"
}

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
    Write-Host "Downloading Python 3.12 from python.org..."

    # Clean ghost registry entries first
    Remove-GhostPythonRegistry

    # Default version to download
    $pythonVersion = "3.12.0"

    # Check if a specific version is already registered in MSI
    # If so, download that exact version to avoid 1638 error
    $registeredVersion = Get-RegisteredPythonVersion
    if ($registeredVersion) {
        log "MSI has Python $registeredVersion registered - downloading same version"
        Write-Host "Detected registered Python $registeredVersion - downloading matching version..."
        $pythonVersion = $registeredVersion
    }

    $pythonUrl     = Get-PythonDownloadUrl $pythonVersion
    $installerPath = "$env:TEMP\python-$pythonVersion-amd64.exe"

    log "Downloading: $pythonUrl"
    Write-Host "Downloading Python $pythonVersion (~25MB)..."

    try {
        $script:lastPercent = 0
        $webClient = New-Object System.Net.WebClient
        $webClient.DownloadProgressChanged += {
            param($s, $e)
            $percent = $e.ProgressPercentage
            if ($percent -ge ($script:lastPercent + 10)) {
                $script:lastPercent = $percent
                $downloaded = [Math]::Round($e.BytesReceived / 1MB, 1)
                $total = [Math]::Round($e.TotalBytesToReceive / 1MB, 1)
                Write-Host "  Downloading... $percent% ($downloaded MB / $total MB)"
            }
        }
        $script:downloadDone = $false
        $script:downloadError = $null
        $webClient.DownloadFileCompleted += {
            param($s, $e)
            if ($e.Error) { $script:downloadError = $e.Error.Message }
            $script:downloadDone = $true
        }
        $webClient.DownloadFileAsync([Uri]$pythonUrl, $installerPath)
        while (-not $script:downloadDone) { Start-Sleep -Milliseconds 500 }
        $webClient.Dispose()
        if ($script:downloadError) {
            log "Download error: $script:downloadError"
            Write-Host "Download error: $script:downloadError"
            return $false
        }

        if (-not (Test-Path $installerPath)) {
            log "Download failed - installer not found"
            Write-Host "Download failed"
            return $false
        }

        Write-Host "Running Python installer silently..."
        $installArgs = "/quiet InstallAllUsers=0 PrependPath=1 Include_test=0"
        $proc = Start-Process -FilePath $installerPath -ArgumentList $installArgs -Wait -PassThru

        # Exit 1638 = same or newer version already registered
        # This means Python IS installed but files may be missing
        # Try REINSTALL flag with same installer
        if ($proc.ExitCode -eq 1638) {
            log "Exit 1638 - attempting reinstall with REINSTALL=ALL..."
            Write-Host "Forcing reinstall..."
            $reinstallArgs = "/quiet REINSTALL=ALL REINSTALLMODE=amus InstallAllUsers=0 PrependPath=1 Include_test=0"
            $proc = Start-Process -FilePath $installerPath -ArgumentList $reinstallArgs -Wait -PassThru
            log "Reinstall exit code: $($proc.ExitCode)"
        }

        # Clean up installer
        Remove-Item $installerPath -Force -ErrorAction SilentlyContinue

        if ($proc.ExitCode -ne 0) {
            log "Python installer failed (exit: $($proc.ExitCode))"
            Write-Host "Python installer failed (exit: $($proc.ExitCode))"
            return $false
        }

        # Refresh PATH with all common Python locations
        $env:PATH = [System.Environment]::GetEnvironmentVariable("PATH", "Machine") + ";" +
                    [System.Environment]::GetEnvironmentVariable("PATH", "User") + ";" +
                    "$env:LOCALAPPDATA\Programs\Python\Python312;" +
                    "$env:LOCALAPPDATA\Programs\Python\Python312\Scripts;" +
                    "$env:LOCALAPPDATA\Programs\Python\Python311;" +
                    "$env:LOCALAPPDATA\Programs\Python\Python311\Scripts;" +
                    "$env:LOCALAPPDATA\Programs\Python\Python310;" +
                    "$env:LOCALAPPDATA\Programs\Python\Python310\Scripts;" +
                    "$env:PROGRAMFILES\Python312;" +
                    "$env:PROGRAMFILES\Python312\Scripts"

        log "Python $pythonVersion installed via direct download"
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

function Install-Python {
    log "Python not found - installing 3.12..."
    Write-Host "Python not found - installing 3.12..."

    $wingetOk = $false
    try { $wingetOk = Install-Python-Via-Winget } catch { $wingetOk = $false }
    $global:LASTEXITCODE = 0
    if ($wingetOk -eq $true) { return $true }

    log "Falling back to direct download..."
    Write-Host "Falling back to direct download..."
    $directOk = $false
    try { $directOk = Install-Python-Via-Direct-Download } catch { $directOk = $false }
    $global:LASTEXITCODE = 0
    if ($directOk -eq $true) { return $true }

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

function Find-PythonExe {
    # Search common install locations for python.exe
    $searchPaths = @(
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python310\python.exe",
        "$env:PROGRAMFILES\Python312\python.exe",
        "$env:PROGRAMFILES\Python311\python.exe",
        "C:\Python312\python.exe",
        "C:\Python311\python.exe",
        "C:\Python310\python.exe"
    )
    foreach ($path in $searchPaths) {
        if (Test-Path $path) {
            log "Found Python at: $path"
            return $path
        }
    }
    # Also check registry
    $regEntry = Get-ItemProperty "HKCU:\Software\Python\PythonCore\*\InstallPath" -ErrorAction SilentlyContinue
    if (-not $regEntry) {
        $regEntry = Get-ItemProperty "HKLM:\Software\Python\PythonCore\*\InstallPath" -ErrorAction SilentlyContinue
    }
    if ($regEntry -and $regEntry.ExecutablePath -and (Test-Path $regEntry.ExecutablePath)) {
        log "Found Python via registry: $($regEntry.ExecutablePath)"
        return $regEntry.ExecutablePath
    }
    return $null
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
    log "Python not found on PATH - checking other locations..."

    # Try to find Python in known locations before installing
    $foundExe = Find-PythonExe
    if ($foundExe) {
        $pyDir = Split-Path $foundExe
        $env:PATH = "$pyDir;$pyDir\Scripts;" + $env:PATH
        log "Added $pyDir to PATH"
        if (Test-PythonWorks $foundExe) {
            $PYTHON_CMD     = $foundExe
            $PYTHON_VERSION = (& $foundExe --version 2>&1) -replace "Python ", ""
            log "Python found at: $foundExe version $PYTHON_VERSION"
            Write-Host "[OK] Python $PYTHON_VERSION found at $foundExe"
        }
    }

    # Still not found - install
    if (-not $PYTHON_CMD) {
        log "Python not found anywhere - installing..."
        $installed = Install-Python
        if (-not $installed) { exit 1 }

        # Refresh PATH after install
        $env:PATH = [System.Environment]::GetEnvironmentVariable("PATH", "Machine") + ";" +
                    [System.Environment]::GetEnvironmentVariable("PATH", "User") + ";" +
                    "$env:LOCALAPPDATA\Programs\Python\Python312;" +
                    "$env:LOCALAPPDATA\Programs\Python\Python312\Scripts;" +
                    "$env:LOCALAPPDATA\Programs\Python\Python311;" +
                    "$env:LOCALAPPDATA\Programs\Python\Python311\Scripts;" +
                    "$env:LOCALAPPDATA\Programs\Python\Python310;" +
                    "$env:LOCALAPPDATA\Programs\Python\Python310\Scripts"

        # Check PATH commands first
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
            # Last resort - search by path
            $foundExe = Find-PythonExe
            if ($foundExe -and (Test-PythonWorks $foundExe)) {
                $PYTHON_CMD     = $foundExe
                $PYTHON_VERSION = (& $foundExe --version 2>&1) -replace "Python ", ""
                log "Python found at path: $foundExe version $PYTHON_VERSION"
                Write-Host "[OK] Python $PYTHON_VERSION"
            }
            else {
                log "Python not available after install"
                Write-Host "[FAIL] Python not available after install"
                Write-Host "Please install Python 3.12 manually: https://www.python.org/downloads/"
                exit 1
            }
        }
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

log "Python OK: $PYTHON_VERSION"
Write-Host "[OK] Python $PYTHON_VERSION confirmed"

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