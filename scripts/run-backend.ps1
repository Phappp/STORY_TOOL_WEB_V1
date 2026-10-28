$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { throw "Run scripts/setup-dev.ps1 first." }
$hostAddress = if ($env:APP_HOST) { $env:APP_HOST } else { "127.0.0.1" }
if ($hostAddress -notin @("127.0.0.1", "localhost", "::1")) { throw "The development API may bind only to loopback." }
$port = if ($env:APP_PORT) { [int]$env:APP_PORT } else { 8000 }
if ($port -lt 1024 -or $port -gt 65535) { throw "APP_PORT must be between 1024 and 65535." }
$env:PYTHONPATH = Join-Path $root "backend"
& $python -m uvicorn app.main:app --app-dir (Join-Path $root "backend") --host $hostAddress --port $port --reload
