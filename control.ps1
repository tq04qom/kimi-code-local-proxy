param(
    [ValidateSet('menu', 'start', 'stop', 'status', 'dashboard', 'exit')]
    [string]$Action = 'menu'
)

$ErrorActionPreference = 'Stop'

$projectRoot = $PSScriptRoot

function Invoke-ProjectScript {
    param([string]$ScriptName)

    $scriptPath = Join-Path $projectRoot $ScriptName
    if (-not (Test-Path $scriptPath)) {
        throw "Script not found: $scriptPath"
    }

    & $scriptPath
}

function Show-Menu {
    Clear-Host
    Write-Host 'local-kimi-api control'
    Write-Host ''
    Write-Host '1. Start service'
    Write-Host '2. Stop service'
    Write-Host '3. Show status popup'
    Write-Host '4. Open dashboard'
    Write-Host '5. Exit'
    Write-Host ''
}

function Invoke-Action {
    param([string]$Name)

    switch ($Name) {
        'start' {
            Invoke-ProjectScript -ScriptName 'start.ps1'
        }
        'stop' {
            Invoke-ProjectScript -ScriptName 'stop.ps1'
        }
        'status' {
            Invoke-ProjectScript -ScriptName 'health-status.ps1'
        }
        'dashboard' {
            Invoke-ProjectScript -ScriptName 'open-dashboard.ps1'
        }
        'exit' {
            return $false
        }
        default {
            Write-Warning 'Invalid selection.'
        }
    }

    return $true
}

if ($Action -ne 'menu') {
    Invoke-Action -Name $Action | Out-Null
    exit 0
}

while ($true) {
    Show-Menu
    $choice = Read-Host 'Select an option'

    switch ($choice) {
        '1' { $continue = Invoke-Action -Name 'start' }
        '2' { $continue = Invoke-Action -Name 'stop' }
        '3' { $continue = Invoke-Action -Name 'status' }
        '4' { $continue = Invoke-Action -Name 'dashboard' }
        '5' { $continue = Invoke-Action -Name 'exit' }
        default { $continue = Invoke-Action -Name 'invalid' }
    }

    if (-not $continue) {
        break
    }

    Pause
}
