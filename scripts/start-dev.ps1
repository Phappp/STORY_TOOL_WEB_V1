$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Write-Host "Start backend and frontend in separate VS Code terminals:"
Write-Host "  powershell -ExecutionPolicy Bypass -File '$root\scripts\run-backend.ps1'"
Write-Host "  powershell -ExecutionPolicy Bypass -File '$root\scripts\run-frontend.ps1'"
Write-Host "Optional MongoDB: powershell -ExecutionPolicy Bypass -File '$root\database\init-dev-replica.ps1'"
