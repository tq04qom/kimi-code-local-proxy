$ErrorActionPreference = 'Stop'

$projectRoot = $PSScriptRoot
$repoRoot = Split-Path -Parent $projectRoot
$pythonwExe = Join-Path $repoRoot '.venv\Scripts\pythonw.exe'
$trayScript = Join-Path $projectRoot 'tray_app.py'
$trayPidFile = Join-Path $projectRoot '.tray.pid'

if (-not (Test-Path $pythonwExe)) {
    throw "pythonw.exe not found: $pythonwExe"
}

if (-not (Test-Path $trayScript)) {
    throw "tray_app.py not found: $trayScript"
}

$existingPid = $null
if (Test-Path $trayPidFile) {
    $existingPidText = (Get-Content $trayPidFile -Raw).Trim()
    if ($existingPidText) {
        try {
            $existingPid = [int]$existingPidText
            Get-Process -Id $existingPid -ErrorAction Stop | Out-Null
            exit 0
        }
        catch {
            Remove-Item $trayPidFile -Force -ErrorAction SilentlyContinue
        }
    }
}

$process = Start-Process -FilePath $pythonwExe -ArgumentList $trayScript -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru
Set-Content -Path $trayPidFile -Value $process.Id -Encoding ascii
