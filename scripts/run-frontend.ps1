$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Push-Location (Join-Path $root "frontend")
if (-not (Test-Path "node_modules")) { throw "Run scripts/setup-dev.ps1 first." }
$env:NEXT_PUBLIC_API_URL = if ($env:NEXT_PUBLIC_API_URL) { $env:NEXT_PUBLIC_API_URL } else { "http://127.0.0.1:8000/api/v1" }
npm run dev
Pop-Location
