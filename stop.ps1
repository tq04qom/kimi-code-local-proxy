$ErrorActionPreference = 'Stop'

$projectRoot = $PSScriptRoot
$startScript = Join-Path $projectRoot 'start.py'
$envFile = Join-Path $projectRoot '.env'

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

function Get-PidFiles {
    param([string]$BaseDirectory)

    if (-not (Test-Path $BaseDirectory)) {
        return @()
    }

    return Get-ChildItem -Path $BaseDirectory -Recurse -Filter 'service.pid' -File | Sort-Object FullName -Descending
}

function Stop-TrackedProcess {
    param([int]$ProcessId)

    try {
        Get-Process -Id $ProcessId -ErrorAction Stop | Out-Null
        Stop-Process -Id $ProcessId -Force
        Write-Host "Stopped service PID: $ProcessId"
        return $true
    }
    catch {
        return $false
    }
}

$stopped = $false

$logRoot = Get-EnvValue -Path $envFile -Key 'LOG_DIR' -DefaultValue 'logs'
if ([System.IO.Path]::IsPathRooted($logRoot)) {
    $resolvedLogRoot = $logRoot
}
else {
    $resolvedLogRoot = Join-Path $projectRoot $logRoot
}

foreach ($pidFile in Get-PidFiles -BaseDirectory $resolvedLogRoot) {
    $pidText = (Get-Content $pidFile.FullName -Raw).Trim()
    if ($pidText) {
        if (Stop-TrackedProcess -ProcessId ([int]$pidText)) {
            $stopped = $true
        }
    }
    Remove-Item $pidFile.FullName -Force
}

if (-not $stopped) {
    $escapedPath = [Regex]::Escape($startScript)
    $candidates = Get-CimInstance Win32_Process | Where-Object {
        $_.CommandLine -and $_.CommandLine -match $escapedPath
    }

    foreach ($candidate in $candidates) {
        if (Stop-TrackedProcess -ProcessId $candidate.ProcessId) {
            $stopped = $true
        }
    }
}

if (-not $stopped) {
    Write-Host 'Service is not running.'
    exit 0
}

Write-Host 'Service stopped.'
