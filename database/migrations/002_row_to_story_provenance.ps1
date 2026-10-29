# Migration 002: Row-to-Story provenance
# Run only against the project development database after backup and dry-run.
param([switch]$DryRun)
$ErrorActionPreference='Stop'
$root=Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$mongo=Join-Path $root '.venv\Scripts\python.exe'
if($DryRun){ Write-Output 'DRY RUN: additive provenance validators/index are applied by backend ensure_schema; no data mutation.'; exit 0 }
Write-Output 'Apply through backend startup ensure_schema after owner-approved backup. This migration never deletes Stories.'
