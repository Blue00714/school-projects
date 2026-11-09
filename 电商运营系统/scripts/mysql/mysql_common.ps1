# Shared helpers for the project-local portable MySQL 8 instance.
# Dot-source this file:  . (Join-Path $PSScriptRoot 'mysql_common.ps1')
# ASCII only on purpose: Windows PowerShell 5.1 mis-reads UTF-8 scripts without a BOM.

$script:EceRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$script:EceMysqlHome = Join-Path $script:EceRoot 'vendor\mysql'
$script:EceRuntime = Join-Path $script:EceRoot 'runtime'
$script:EceDataDir = Join-Path $script:EceRuntime 'mysql-data'
$script:EceLogDir = Join-Path $script:EceRuntime 'logs\mysql'
$script:EceEnvFile = Join-Path $script:EceRoot 'config\local\mysql.env'
$script:EcePidFile = Join-Path $script:EceRuntime 'tmp\mysql.pid'
$script:EceIniFile = Join-Path $script:EceRoot 'config\local\mysql.ini'

function Get-EceRoot { return $script:EceRoot }
function Get-EceMysqlHome { return $script:EceMysqlHome }
function Get-EceDataDir { return $script:EceDataDir }
function Get-EceLogDir { return $script:EceLogDir }
function Get-EceEnvFile { return $script:EceEnvFile }
function Get-EcePidFile { return $script:EcePidFile }
function Get-EceIniFile { return $script:EceIniFile }
function Get-EceMysqld { return (Join-Path $script:EceMysqlHome 'bin\mysqld.exe') }
function Get-EceMysqlAdmin { return (Join-Path $script:EceMysqlHome 'bin\mysqladmin.exe') }
function Get-EceMysqlClient { return (Join-Path $script:EceMysqlHome 'bin\mysql.exe') }

function Read-EceMysqlEnv {
    $values = @{}
    if (Test-Path $script:EceEnvFile) {
        foreach ($line in Get-Content -Path $script:EceEnvFile -Encoding UTF8) {
            $text = $line.Trim()
            if (-not $text -or $text.StartsWith('#') -or -not $text.Contains('=')) { continue }
            $parts = $text.Split('=', 2)
            $values[$parts[0].Trim()] = $parts[1].Trim().Trim('"').Trim("'")
        }
    }
    return $values
}

function Get-EceMysqlPort {
    $values = Read-EceMysqlEnv
    if ($values.ContainsKey('ECE_MYSQL_PORT') -and $values['ECE_MYSQL_PORT']) {
        return [int]$values['ECE_MYSQL_PORT']
    }
    return 3307
}

function Get-EceMysqlHost {
    $values = Read-EceMysqlEnv
    if ($values.ContainsKey('ECE_MYSQL_HOST') -and $values['ECE_MYSQL_HOST']) {
        return $values['ECE_MYSQL_HOST']
    }
    return '127.0.0.1'
}

function Test-EcePortOpen {
    param(
        [int]$Port = 3307,
        [string]$Endpoint = '127.0.0.1',
        [int]$TimeoutMs = 800
    )
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $task = $client.ConnectAsync($Endpoint, $Port)
        if (-not $task.Wait($TimeoutMs)) { return $false }
        return $client.Connected
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

function Wait-EceMysqlReady {
    param(
        [int]$Port = 3307,
        [int]$TimeoutSeconds = 90
    )
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-EcePortOpen -Port $Port) { return $true }
        Start-Sleep -Milliseconds 500
    }
    return $false
}

function Wait-EceMysqlStopped {
    param(
        [int]$Port = 3307,
        [int]$TimeoutSeconds = 60
    )
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if (-not (Test-EcePortOpen -Port $Port)) { return $true }
        Start-Sleep -Milliseconds 500
    }
    return $false
}

function Find-EcePython {
    $candidates = @()
    if ($env:ECE_PYTHON) { $candidates += $env:ECE_PYTHON }
    $command = Get-Command python -ErrorAction SilentlyContinue
    if ($command) { $candidates += $command.Source }
    $command = Get-Command python3 -ErrorAction SilentlyContinue
    if ($command) { $candidates += $command.Source }
    $command = Get-Command py -ErrorAction SilentlyContinue
    if ($command) { $candidates += $command.Source }
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path $candidate)) { return $candidate }
    }
    throw 'Python not found on PATH. Install Python 3.10+ or set ECE_PYTHON.'
}

function Get-EceErrorLogTail {
    param([int]$Lines = 20)
    $logFile = Join-Path $script:EceLogDir 'error.log'
    if (Test-Path $logFile) {
        return (Get-Content -Path $logFile -Tail $Lines -Encoding UTF8) -join [Environment]::NewLine
    }
    return "(no error log at $logFile)"
}
