$ErrorActionPreference = 'Stop'

$projectRoot = $PSScriptRoot
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

$hostValue = Get-EnvValue -Path $envFile -Key 'HOST' -DefaultValue '127.0.0.1'
$portValue = Get-EnvValue -Path $envFile -Key 'PORT' -DefaultValue '8000'
$dashboardUrl = "http://$hostValue`:$portValue/dashboard"

Start-Process $dashboardUrl | Out-Null
Write-Host "Opened dashboard: $dashboardUrl"
