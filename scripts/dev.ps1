<#
.SYNOPSIS
    Runs Gluons locally without Docker.

.DESCRIPTION
    Starts embedded PostgreSQL, applies migrations, syncs skills, then runs the API, worker,
    labelled ERP simulator and web app. Ctrl+C stops everything this script started.
    No provider credentials or cloud resources are used.

.EXAMPLE
    .\scripts\dev.ps1            # Run until Ctrl+C
    .\scripts\dev.ps1 -NoWeb     # Skip the web app
    .\scripts\dev.ps1 -Check     # Prove the API reaches /ready, then stop everything
    .\scripts\dev.ps1 -Stop      # Stop what a previous run left behind
#>
[CmdletBinding()]
param(
    [switch]$NoWeb,
    [switch]$Check,
    [switch]$Stop,
    [int]$ApiPort = 8000,
    [int]$ErpPort = 8100,
    [int]$WebPort = 3000,
    [int]$ReadyTimeoutSeconds = 120,
    [string]$Python = $env:GLUONS_PYTHON
)

# Native tools write progress to stderr, which 'Stop' would turn into a failure in Windows
# PowerShell. Each step checks its exit code instead.
$ErrorActionPreference = 'Continue'

$Root = Split-Path -Parent $PSScriptRoot
$Data = Join-Path $Root '.gluons-data'
$Logs = Join-Path $Data 'logs'
$PidFile = Join-Path $Data 'dev-pids.txt'
$PgData = Join-Path $Root '.pgdata\dev'

if (-not $Python) { $Python = Join-Path $Root '.venv\Scripts\python.exe' }
if (-not (Test-Path $Python)) {
    Write-Host "Python not found at $Python"
    Write-Host 'Create it first:'
    Write-Host '  py -3.13 -m venv .venv'
    Write-Host '  .venv\Scripts\python.exe -m pip install -e "apps/api[dev,dev-postgres,mcp,optimization,documents]"'
    Write-Host 'Or point GLUONS_PYTHON at an existing interpreter.'
    exit 1
}

# Always run this checkout's code, whatever an editable install points at.
$env:PYTHONPATH = Join-Path $Root 'apps\api\src'
$env:PYTHONUTF8 = '1'

function Stop-Tree([int]$Id) {
    # A venv python.exe is a launcher with a child process, so the whole tree has to go.
    & taskkill.exe /PID $Id /T /F *> $null
}

function Stop-Database {
    if (-not (Test-Path (Join-Path $PgData 'postmaster.pid'))) { return }
    $bin = & $Python -c 'from pixeltable_pgserver.utils import POSTGRES_VERSIONS as v, TARGET_POSTGRES_VERSION as t; print(v[t])'
    & (Join-Path $bin 'pg_ctl.exe') -w -D $PgData stop -m fast *> $null
}

function Stop-All {
    if (Test-Path $PidFile) {
        foreach ($id in Get-Content $PidFile) {
            if ($id) { Stop-Tree ([int]$id) }
        }
        Remove-Item $PidFile -Force
    }
    Stop-Database
}

function Assert-PortFree([int]$Port, [string]$Name) {
    $listener = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue
    if ($listener) {
        Write-Host "Port $Port for $Name is already in use. Choose another with -ApiPort, -ErpPort or -WebPort."
        exit 1
    }
}

function Start-Service-Logged([string]$Name, [string]$File, [string[]]$Arguments) {
    $process = Start-Process -FilePath $File -ArgumentList $Arguments -WorkingDirectory $Root -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $Logs "$Name.log") -RedirectStandardError (Join-Path $Logs "$Name.err.log")
    Add-Content -Path $PidFile -Value $process.Id
    return [pscustomobject]@{ Name = $Name; Process = $process }
}

if ($Stop) {
    Stop-All
    Write-Host 'Stopped.'
    exit 0
}

Assert-PortFree $ApiPort 'the API'
Assert-PortFree $ErpPort 'the ERP simulator'
if (-not $NoWeb) {
    Assert-PortFree $WebPort 'the web app'
    if (-not (Test-Path (Join-Path $Root 'apps\web\node_modules'))) {
        Write-Host 'Install web dependencies first with: npm ci --prefix apps/web. Or pass -NoWeb.'
        exit 1
    }
}

New-Item -ItemType Directory -Force -Path $Logs | Out-Null
Set-Location $Root
$env:GLUONS_ENV = 'local'
$env:GLUONS_PUBLIC_API_URL = "http://localhost:$ApiPort"
$env:GLUONS_PUBLIC_WEB_URL = "http://localhost:$WebPort"
$env:GLUONS_ALLOWED_WEB_ORIGINS = "http://localhost:$WebPort,http://127.0.0.1:$WebPort"
$env:GLUONS_SANDBOX_ERP_URL = "http://127.0.0.1:$ErpPort"
$env:NEXT_PUBLIC_API_URL = "http://localhost:$ApiPort"
$env:NEXT_TELEMETRY_DISABLED = '1'

$services = @()
$exitCode = 0
try {
    Write-Host 'Starting embedded PostgreSQL and applying migrations...'
    $lines = & $Python -m gluons.devdb 2> (Join-Path $Logs 'migrate.log')
    if ($LASTEXITCODE -ne 0) { throw "Migration failed. See $Logs\migrate.log" }
    foreach ($line in $lines) {
        if ($line -match '^(GLUONS_[A-Z_]+)=(.+)$') { Set-Item -Path "Env:$($Matches[1])" -Value $Matches[2] }
    }

    Write-Host 'Syncing skills...'
    & $Python -m gluons.cli skills-sync
    if ($LASTEXITCODE -ne 0) { throw 'Skill sync failed.' }

    $services += Start-Service-Logged 'api' $Python @('-m', 'gluons.cli', 'serve', '--port', "$ApiPort")
    $services += Start-Service-Logged 'worker' $Python @('-m', 'gluons.cli', 'worker')
    $services += Start-Service-Logged 'simulator' $Python @('-m', 'uvicorn', 'sandbox_erp:app', '--app-dir', 'apps/sandbox_erp', '--port', "$ErpPort")
    if (-not $NoWeb) {
        $services += Start-Service-Logged 'web' 'npm.cmd' @('--prefix', 'apps/web', 'run', 'dev', '--', '--port', "$WebPort")
    }

    $deadline = (Get-Date).AddSeconds($ReadyTimeoutSeconds)
    $ready = $false
    while ((Get-Date) -lt $deadline -and -not $ready) {
        try {
            $response = Invoke-WebRequest -Uri "http://127.0.0.1:$ApiPort/ready" -UseBasicParsing -TimeoutSec 3
            $ready = $response.StatusCode -eq 200
            if ($ready) { Write-Host "API ready: $($response.Content)" }
        } catch {
            Start-Sleep -Seconds 1
        }
        $dead = $services | Where-Object { $_.Process.HasExited }
        if ($dead) { throw "$($dead[0].Name) stopped during startup. See $Logs" }
    }
    if (-not $ready) { throw "API did not reach /ready within $ReadyTimeoutSeconds seconds. See $Logs\api.err.log" }

    Write-Host ''
    if (-not $NoWeb) { Write-Host "Gluons Web     http://localhost:$WebPort" }
    Write-Host "API docs       http://localhost:$ApiPort/docs"
    Write-Host "API readiness  http://localhost:$ApiPort/ready"
    Write-Host "MCP endpoint   http://localhost:$ApiPort/mcp"
    Write-Host "ERP simulator  http://127.0.0.1:$ErpPort (labelled simulator, not ERPNext)"
    Write-Host "Local email    .gluons-data\mail"
    Write-Host "Logs           .gluons-data\logs"
    Write-Host ''

    if ($Check) {
        Write-Host 'Check passed. Stopping everything this script started.'
    } else {
        Write-Host 'Press Ctrl+C to stop.'
        while ($true) {
            $dead = $services | Where-Object { $_.Process.HasExited }
            if ($dead) { throw "$($dead[0].Name) stopped. See $Logs" }
            Start-Sleep -Seconds 1
        }
    }
} catch {
    Write-Host $_.Exception.Message
    $exitCode = 1
} finally {
    Stop-All
}
exit $exitCode
