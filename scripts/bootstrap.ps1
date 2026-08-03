<#
    CloudByte plugin installer - one-liner bootstrap.

    Run on a fresh Windows machine - no options, fully interactive:

        irm https://raw.githubusercontent.com/CloudByte-AI/claude-telemetry/main/scripts/bootstrap.ps1 | iex

    With options, still one command (a plain `| iex` cannot take arguments,
    but a scriptblock can):

        & ([scriptblock]::Create((irm https://raw.githubusercontent.com/CloudByte-AI/claude-telemetry/main/scripts/bootstrap.ps1))) -Yes

    Any arguments are forwarded verbatim to install.ps1.

    Installing from a branch other than main - note the ref appears TWICE,
    once in the URL you fetch and once as -Ref. This file cannot detect which
    branch it was downloaded from, so without -Ref it would pull install.ps1
    from main:

        & ([scriptblock]::Create((irm https://raw.githubusercontent.com/CloudByte-AI/claude-telemetry/development/scripts/bootstrap.ps1))) -Ref development

    This file exists because piping install.ps1 itself into iex would evaluate
    it in the CALLING session, where every `exit` terminates the user's
    PowerShell window. This bootstrap therefore contains no `exit` of its own
    and invokes install.ps1 as a script FILE, which contains its exit code.

    Environment variables are an alternative to arguments, useful when the
    plain `| iex` form is required:

        $env:CLOUDBYTE_INSTALL_ARGS = "-Yes"          # switches for install.ps1
        $env:CLOUDBYTE_REF          = "development"   # branch/tag/sha to install from
        $env:CLOUDBYTE_INSTALL_URL  = "http://..."    # full override of the script URL

    The installer's own exit code is left in $LASTEXITCODE and $CloudByteExitCode.
#>

param(
    # Branch, tag or sha to install from. A script piped into iex cannot know
    # which URL it was fetched from, so fetching this file from a branch does
    # NOT imply install.ps1 comes from that branch - say so explicitly.
    [string] $Ref,

    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $InstallArgs
)

$ErrorActionPreference = "Stop"

$repo = "CloudByte-AI/claude-telemetry"
if     ($Ref)                 { $ref = $Ref }
elseif ($env:CLOUDBYTE_REF)   { $ref = $env:CLOUDBYTE_REF }
else                          { $ref = "main" }

if ($env:CLOUDBYTE_INSTALL_URL) {
    $installUrl = $env:CLOUDBYTE_INSTALL_URL
    $rawBase    = $installUrl -replace "/[^/]+$", ""
}
else {
    $rawBase    = "https://raw.githubusercontent.com/$repo/$ref/scripts"
    $installUrl = "$rawBase/install.ps1"
}

$dest = Join-Path $env:TEMP ("cloudbyte-install-" + [guid]::NewGuid().ToString("N").Substring(0, 8) + ".ps1")

Write-Host ""
Write-Host "Fetching CloudByte installer..."
Write-Host "  $installUrl"

try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -Uri $installUrl -OutFile $dest -UseBasicParsing
}
catch {
    Write-Host ""
    Write-Host "[FAIL] Could not download the installer: $_" -ForegroundColor Red
    Write-Host "       Check your connection, or that the ref '$ref' exists."
    Write-Host ""
    $global:CloudByteExitCode = 1
    return
}

# Build a NAMED parameter set. Array splatting would bind positionally - the
# tokens would land in MarketplaceUrl/PluginRef/DashboardUrl instead of the
# switches they name - so parse into a hashtable and splat that.
# Note: parameter values containing spaces are not supported here.
$tokens = @()
if ($InstallArgs)                  { $tokens += $InstallArgs }
if ($env:CLOUDBYTE_INSTALL_ARGS)   { $tokens += ($env:CLOUDBYTE_INSTALL_ARGS -split "\s+") }
$tokens = @($tokens | Where-Object { $_ })

$params = @{}
for ($i = 0; $i -lt $tokens.Count; $i++) {
    if ($tokens[$i] -notmatch "^-") { continue }
    $name = $tokens[$i].TrimStart("-")
    if (($i + 1) -lt $tokens.Count -and $tokens[$i + 1] -notmatch "^-") {
        $params[$name] = $tokens[$i + 1]
        $i++
    }
    else {
        $params[$name] = $true
    }
}

# Fetch validate.ps1 from the same ref install.ps1 came from.
if (-not $params.ContainsKey("RawBase")) { $params["RawBase"] = $rawBase }

try {
    & $dest @params
    $global:CloudByteExitCode = $LASTEXITCODE
}
finally {
    Remove-Item $dest -Force -ErrorAction SilentlyContinue
}
