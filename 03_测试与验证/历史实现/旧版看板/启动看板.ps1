param([int]$Port=8777)
$ErrorActionPreference='Stop'
$projectPython='C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe'
if (-not (Test-Path -LiteralPath $projectPython)) { $projectPython=(Get-Command python).Source }
Push-Location $PSScriptRoot
try { & $projectPython manage.py serve --port $Port } finally { Pop-Location }
