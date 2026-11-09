# Start the project-local portable MySQL 8 instance (idempotent).
#
#   powershell -ExecutionPolicy Bypass -File scripts\mysql_start.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\mysql_start.ps1 -Port 3307 -TimeoutSeconds 90
#
# Listens on 127.0.0.1 only. Waits until the TCP port accepts connections. The server
# keeps running in the background; use scripts\mysql_stop.ps1 to shut it down.
[CmdletBinding()]
param(
    [int]$Port = 0,
    [int]$TimeoutSeconds = 90
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'mysql_common.ps1')

$root = Get-EceRoot
if ($Port -le 0) { $Port = Get-EceMysqlPort }
$mysqld = Get-EceMysqld
$iniFile = Get-EceIniFile
$pidFile = Get-EcePidFile
$logDir = Get-EceLogDir

if (Test-EcePortOpen -Port $Port) {
    Write-Host "[mysql-start] already listening on 127.0.0.1:$Port (nothing to do)"
    exit 0
}
if (-not (Test-Path $mysqld)) {
    throw "mysqld not found: $mysqld. Run scripts\mysql_init.ps1 first."
}
if (-not (Test-Path $iniFile)) {
    throw "my.ini not found: $iniFile. Run scripts\mysql_init.ps1 first."
}
New-Item -ItemType Directory -Path $logDir -Force | Out-Null

$mysqlTemp = Join-Path $root 'runtime\tmp\mysql'
New-Item -ItemType Directory -Path $mysqlTemp -Force | Out-Null
$env:TEMP = $mysqlTemp
$env:TMP = $mysqlTemp
Write-Host "[mysql-start] starting mysqld (127.0.0.1:$Port)"
$process = Start-Process -FilePath $env:ComSpec `
    -ArgumentList @('/d', '/c', 'bin\mysqld.exe --defaults-file=..\..\config\local\mysql.ini') `
    -WorkingDirectory (Get-EceMysqlHome) `
    -WindowStyle Hidden -PassThru

if (-not (Wait-EceMysqlReady -Port $Port -TimeoutSeconds $TimeoutSeconds)) {
    throw "mysqld did not accept connections on 127.0.0.1:$Port within $TimeoutSeconds s. Error log:`n$(Get-EceErrorLogTail 40)"
}

$admin = Get-EceMysqlAdmin
$cfg = Read-EceMysqlEnv
if ((Test-Path $admin) -and $cfg.ContainsKey('ECE_MYSQL_PASSWORD')) {
    $previous = $env:MYSQL_PWD
    $env:MYSQL_PWD = $cfg['ECE_MYSQL_PASSWORD']
    try {
        $version = & $admin "--host=127.0.0.1" "--port=$Port" "--user=$($cfg['ECE_MYSQL_USER'])" '--protocol=TCP' 'version' 2>$null
        if ($LASTEXITCODE -eq 0) {
            Write-Host "[mysql-start] ready: $(($version | Select-Object -Last 1))"
        } else {
            Write-Host '[mysql-start] port is open but the ece account did not answer yet'
        }
    } finally {
        $env:MYSQL_PWD = $previous
    }
} else {
    Write-Host "[mysql-start] ready on 127.0.0.1:$Port"
}
Write-Host "[mysql-start] data dir : $(Get-EceDataDir)"
Write-Host "[mysql-start] error log: $(Join-Path $logDir 'error.log')"
