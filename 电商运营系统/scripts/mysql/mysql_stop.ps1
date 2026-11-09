# Stop the project-local portable MySQL 8 instance (idempotent).
#
#   powershell -ExecutionPolicy Bypass -File scripts\mysql_stop.ps1
#
# Graceful shutdown through mysqladmin (ece account from config\local\mysql.env); falls back
# to stopping the recorded PID if the account is unavailable.
[CmdletBinding()]
param(
    [int]$Port = 0,
    [int]$TimeoutSeconds = 60
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'mysql_common.ps1')

$root = Get-EceRoot
if ($Port -le 0) { $Port = Get-EceMysqlPort }
$pidFile = Get-EcePidFile
$admin = Get-EceMysqlAdmin
$cfg = Read-EceMysqlEnv

if (-not (Test-EcePortOpen -Port $Port)) {
    Write-Host "[mysql-stop] nothing listening on 127.0.0.1:$Port (already stopped)"
    if (Test-Path $pidFile) { Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue }
    exit 0
}

$stopped = $false
if ((Test-Path $admin) -and $cfg.ContainsKey('ECE_MYSQL_USER') -and $cfg.ContainsKey('ECE_MYSQL_PASSWORD')) {
    $previous = $env:MYSQL_PWD
    $env:MYSQL_PWD = $cfg['ECE_MYSQL_PASSWORD']
    try {
        Write-Host "[mysql-stop] mysqladmin shutdown (127.0.0.1:$Port)"
        & $admin "--host=127.0.0.1" "--port=$Port" "--user=$($cfg['ECE_MYSQL_USER'])" '--protocol=TCP' 'shutdown'
        if ($LASTEXITCODE -eq 0) { $stopped = $true }
        else { Write-Host "[mysql-stop] mysqladmin exit $LASTEXITCODE; trying the recorded PID" }
    } catch {
        Write-Host "[mysql-stop] mysqladmin failed: $($_.Exception.Message)"
    } finally {
        $env:MYSQL_PWD = $previous
    }
}

if (-not $stopped -and (Test-Path $pidFile)) {
    $recorded = 0
    [void][int]::TryParse(((Get-Content -LiteralPath $pidFile -Raw) -replace '\s', ''), [ref]$recorded)
    if ($recorded -gt 0) {
        $process = Get-Process -Id $recorded -ErrorAction SilentlyContinue
        if ($process -and $process.ProcessName -like 'mysqld*') {
            Write-Host "[mysql-stop] stopping recorded mysqld pid $recorded"
            Stop-Process -Id $recorded -Force
        }
    }
}

if (-not (Wait-EceMysqlStopped -Port $Port -TimeoutSeconds $TimeoutSeconds)) {
    throw "MySQL still accepts connections on 127.0.0.1:$Port after $TimeoutSeconds s"
}
if (Test-Path $pidFile) { Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue }
Write-Host "[mysql-stop] stopped (data dir kept: $(Get-EceDataDir))"
