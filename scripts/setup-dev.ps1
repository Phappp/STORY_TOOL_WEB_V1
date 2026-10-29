$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$venv = Join-Path $root ".venv"
if (-not (Test-Path $venv)) { py -3 -m venv $venv }
& (Join-Path $PSScriptRoot "configure-pip-windows-ca.ps1")
& (Join-Path $venv "Scripts\python.exe") -m pip install -r (Join-Path $root "backend\requirements.txt")
Push-Location (Join-Path $root "frontend")
npm install
Pop-Location
Write-Host "Development dependencies installed. MongoDB remains optional for mock endpoints."
