$ErrorActionPreference = 'Stop'

$runKeyPath = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
$name = 'LocalKimiApiTray'

try {
    Remove-ItemProperty -Path $runKeyPath -Name $name -ErrorAction Stop
}
catch {
}

Write-Host 'Launch at startup disabled.'
