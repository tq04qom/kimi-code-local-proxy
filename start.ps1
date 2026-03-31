$ErrorActionPreference = 'Stop'

$projectRoot = $PSScriptRoot
$repoRoot = Split-Path -Parent $projectRoot
$pythonExe = Join-Path $repoRoot '.venv\Scripts\python.exe'
$envFile = Join-Path $projectRoot '.env'

function Get-DateLogDirectory {
    param([string]$BaseDirectory)

    $dateFolder = Get-Date -Format 'yyyy-MM-dd'
    return Join-Path $BaseDirectory $dateFolder
}

function Get-EnvValue {
    param(
        [string]$Path,
        [string]$Key,
        [string]$DefaultValue
    )

    if (-not (Test-Path $Path)) {
        return $DefaultValue
    }

    $line = Get-Content $Path | Where-Object { $_ -match "^$Key=" } | Select-Object -First 1
    if (-not $line) {
        return $DefaultValue
    }

    return ($line -split '=', 2)[1].Trim()
}

function Get-RunningProcess {
    param([int]$ProcessId)

    try {
        return Get-Process -Id $ProcessId -ErrorAction Stop
    }
    catch {
        return $null
    }
}

if (-not (Test-Path $pythonExe)) {
    throw "Python executable not found: $pythonExe"
}

$logRoot = Get-EnvValue -Path $envFile -Key 'LOG_DIR' -DefaultValue 'logs'
if ([System.IO.Path]::IsPathRooted($logRoot)) {
    $resolvedLogRoot = $logRoot
}
else {
    $resolvedLogRoot = Join-Path $projectRoot $logRoot
}

$logDir = Get-DateLogDirectory -BaseDirectory $resolvedLogRoot
$pidFile = Join-Path $logDir 'service.pid'
$stdoutLog = Join-Path $logDir 'service.out.log'
$stderrLog = Join-Path $logDir 'service.err.log'

New-Item -ItemType Directory -Path $logDir -Force | Out-Null

if (Test-Path $pidFile) {
    $existingPidText = (Get-Content $pidFile -Raw).Trim()
    if ($existingPidText) {
        $existingProcess = Get-RunningProcess -ProcessId ([int]$existingPidText)
        if ($existingProcess) {
            Write-Host "Service is already running. PID: $($existingProcess.Id)"
            exit 0
        }
    }
    Remove-Item $pidFile -Force
}

$hostValue = Get-EnvValue -Path $envFile -Key 'HOST' -DefaultValue '127.0.0.1'
$portValue = Get-EnvValue -Path $envFile -Key 'PORT' -DefaultValue '8000'
$healthUrl = "http://$hostValue`:$portValue/health"

$process = Start-Process `
    -FilePath $pythonExe `
    -ArgumentList 'start.py' `
    -WorkingDirectory $projectRoot `
    -RedirectStandardOutput $stdoutLog `
    -RedirectStandardError $stderrLog `
    -PassThru

Set-Content -Path $pidFile -Value $process.Id -Encoding ascii

$deadline = (Get-Date).AddSeconds(30)
$healthy = $false

while ((Get-Date) -lt $deadline) {
    Start-Sleep -Milliseconds 500

    $currentProcess = Get-RunningProcess -ProcessId $process.Id
    if (-not $currentProcess) {
        break
    }

    try {
        $response = Invoke-WebRequest -Uri $healthUrl -UseBasicParsing -TimeoutSec 2
        if ($response.StatusCode -eq 200) {
            $healthy = $true
            break
        }
    }
    catch {
    }
}

if (-not $healthy) {
    $currentProcess = Get-RunningProcess -ProcessId $process.Id
    if (-not $currentProcess) {
        if (Test-Path $pidFile) {
            Remove-Item $pidFile -Force
        }
        throw "Service failed to start. Check logs: $stdoutLog and $stderrLog"
    }

    Write-Warning "Service process started but health check did not pass within 30 seconds."
    Write-Host "PID: $($process.Id)"
    Write-Host "Logs: $stdoutLog"
    exit 1
}

Write-Host "Service started successfully."
Write-Host "PID: $($process.Id)"
Write-Host "Health: $healthUrl"
Write-Host "Logs: $logDir"
Write-Host "Stop command: .\stop.ps1"
