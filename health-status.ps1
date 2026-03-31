$ErrorActionPreference = 'Stop'

Add-Type -AssemblyName System.Windows.Forms

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

function Show-InfoMessage {
    param(
        [string]$Title,
        [string]$Message,
        [System.Windows.Forms.MessageBoxIcon]$Icon = [System.Windows.Forms.MessageBoxIcon]::Information
    )

    [System.Windows.Forms.MessageBox]::Show(
        $Message,
        $Title,
        [System.Windows.Forms.MessageBoxButtons]::OK,
        $Icon
    ) | Out-Null
}

function Format-RunTime {
    param([double]$Seconds)

    $duration = [TimeSpan]::FromSeconds([Math]::Max(0, $Seconds))
    return '{0:00}:{1:00}:{2:00}' -f [Math]::Floor($duration.TotalHours), $duration.Minutes, $duration.Seconds
}

$hostValue = Get-EnvValue -Path $envFile -Key 'HOST' -DefaultValue '127.0.0.1'
$portValue = Get-EnvValue -Path $envFile -Key 'PORT' -DefaultValue '8000'
$healthUrl = "http://$hostValue`:$portValue/health"
$statsUrl = "http://$hostValue`:$portValue/api/dashboard/stats"
$dashboardUrl = "http://$hostValue`:$portValue/dashboard"

try {
    $health = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 5
}
catch {
    $message = @(
        'local-kimi-api is unavailable.'
        ''
        "Health URL: $healthUrl"
        'Possible causes:'
        '1. Service is not started'
        '2. Port is occupied by another process'
        '3. Service started but initialization failed'
        ''
        'Try: .\start.ps1'
    ) -join [Environment]::NewLine
    Show-InfoMessage -Title 'local-kimi-api status' -Message $message -Icon ([System.Windows.Forms.MessageBoxIcon]::Warning)
    exit 1
}

$stats = $null
try {
    $stats = Invoke-RestMethod -Uri $statsUrl -TimeoutSec 5
}
catch {
}

$lines = @(
    'local-kimi-api is running normally'
    ''
    "Status: $($health.status)"
    "Provider: $($health.provider)"
    "Model: $($health.model)"
    "Upstream target: $($health.upstream_target)"
)

if ($stats) {
    $lines += ''
    $lines += "Uptime: $(Format-RunTime -Seconds $stats.service.uptime_seconds)"
    $lines += "Total requests: $($stats.totals.requests)"
    $lines += "Chat requests: $($stats.totals.chat_requests)"
    $lines += "Total tokens: $($stats.totals.total_tokens)"
    $lines += "Today tokens: $($stats.totals.today_tokens)"
    $lines += "Average duration: $($stats.totals.average_duration_ms) ms"
    $lines += "Success rate: $($stats.totals.success_rate)%"
}

$lines += ''
$lines += "Dashboard: $dashboardUrl"

Show-InfoMessage -Title 'local-kimi-api status' -Message ($lines -join [Environment]::NewLine)
