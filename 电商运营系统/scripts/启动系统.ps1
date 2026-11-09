param([int]$Port = 8777, [switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$mysqlConfig = Join-Path $projectRoot 'config\local\mysql.env'
if (Test-Path -LiteralPath $mysqlConfig) {
    & (Join-Path $PSScriptRoot 'mysql\mysql_start.ps1')
}
$env:PYTHONUTF8 = '1'
$arguments = @((Join-Path $projectRoot 'backend\run.py'), '--port', "$Port")
if (-not $NoBrowser) { $arguments += '--open-browser' }
Push-Location $projectRoot
try { & python @arguments } finally { Pop-Location }
