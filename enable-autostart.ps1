$ErrorActionPreference = 'Stop'

$projectRoot = $PSScriptRoot
$repoRoot = Split-Path -Parent $projectRoot
$pythonwExe = Join-Path $repoRoot '.venv\Scripts\pythonw.exe'
$trayScript = Join-Path $projectRoot 'tray_app.py'
$runKeyPath = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
$name = 'LocalKimiApiTray'
$command = '"{0}" "{1}"' -f $pythonwExe, $trayScript

New-Item -Path $runKeyPath -Force | Out-Null
Set-ItemProperty -Path $runKeyPath -Name $name -Value $command
Write-Host 'Launch at startup enabled.'
