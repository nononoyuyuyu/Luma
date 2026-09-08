$ErrorActionPreference = 'Stop'
$pidPath = Join-Path $PSScriptRoot 'data\background-process.json'
if (-not (Test-Path -LiteralPath $pidPath)) { Write-Host 'No background instance recorded.'; exit 0 }
$record = Get-Content -LiteralPath $pidPath -Raw | ConvertFrom-Json
$processInfo = Get-CimInstance Win32_Process -Filter "ProcessId = $([int]$record.processId)" -ErrorAction SilentlyContinue
if (-not $processInfo) { Write-Host 'The recorded background process has already stopped.'; exit 0 }
$expectedRun = Join-Path $PSScriptRoot 'run.py'
if (-not $processInfo.CommandLine.Contains($expectedRun)) { throw 'Process identity differs. Refusing to stop it.' }
if ($processInfo.CreationDate.ToUniversalTime().ToString('o') -ne $record.createdUtc) { throw 'Process creation time differs. Refusing to stop it.' }
Stop-Process -Id ([int]$record.processId)
Write-Host 'Luma background process stopped.'
