param([ValidateRange(1,65535)][int]$Port = 8790)
$ErrorActionPreference = 'Stop'
$ruleName = "Luma-LAN-$Port"
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'Run start.cmd first.' }
if (Get-NetFirewallRule -Name $ruleName -ErrorAction SilentlyContinue) {
    Write-Host 'The Luma firewall rule already exists.'
    exit 0
}
New-NetFirewallRule -Name $ruleName -DisplayName "Luma LAN Gallery ($Port)" -Direction Inbound -Action Allow -Protocol TCP -LocalPort $Port -RemoteAddress LocalSubnet -Profile Private | Out-Null
Write-Host 'Allowed on Private networks, LocalSubnet only.'
