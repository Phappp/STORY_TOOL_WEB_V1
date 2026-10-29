param([int]$Port = 27018)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$dataRoot = Join-Path $projectRoot '.data\mongo-dev'
$ownerFile = Join-Path $dataRoot 'project-instance.json'
$pidFile = Join-Path $dataRoot 'mongod.pid'
$configFile = Join-Path $dataRoot 'mongod.cfg'
if (-not (Test-Path $ownerFile) -or -not (Test-Path $pidFile)) { Write-Host 'No owned project MongoDB process is recorded.'; exit 0 }
$owner = Get-Content -Raw $ownerFile | ConvertFrom-Json
if ($owner.projectRoot -ne $projectRoot -or $owner.port -ne $Port -or $owner.replicaSet -ne 'storytool-dev') { throw 'Ownership marker mismatch. Refusing to stop any process.' }
$pidValue = [int](Get-Content -Raw $pidFile)
$proc = Get-CimInstance Win32_Process -Filter "ProcessId=$pidValue" -ErrorAction SilentlyContinue
if (-not $proc) { Remove-Item -LiteralPath $pidFile; Write-Host 'Recorded project process is already stopped.'; exit 0 }
$expectedExe = Join-Path $env:ProgramFiles 'MongoDB\Server\8.0\bin\mongod.exe'
if ($proc.ExecutablePath -ne $expectedExe -or $proc.CommandLine -notmatch [regex]::Escape($configFile)) { throw "PID $pidValue does not match project MongoDB config. Refusing to stop it." }
$listeners = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue
if (-not $listeners -or $listeners.OwningProcess -notcontains $pidValue -or @($listeners | Where-Object { $_.LocalAddress -ne '127.0.0.1' }).Count -gt 0) { throw 'Project PID does not exclusively own the expected loopback port. Refusing to stop it.' }
$yamlDbPath = (Join-Path $dataRoot 'data').Replace('\','/')
$config = Get-Content -Raw $configFile
if ($config -notmatch '(?m)^\s+bindIp:\s*127\.0\.0\.1\s*$' -or $config -notmatch "(?m)^\s+port:\s*$Port\s*$" -or $config -notmatch '(?m)^\s+replSetName:\s*storytool-dev\s*$' -or $config -notmatch [regex]::Escape($yamlDbPath)) { throw 'Project config no longer matches the owned loopback data path. Refusing to stop.' }
$mongoshPath = Join-Path $env:ProgramFiles 'MongoDB\Shell\mongosh.exe'
if (-not (Test-Path $mongoshPath)) {
  $mongosh = Get-Command mongosh.exe -ErrorAction SilentlyContinue
  if ($mongosh -and (Get-Item $mongosh.Source).Length -gt 1000000) { $mongoshPath = $mongosh.Source }
}
if (-not (Test-Path $mongoshPath)) { throw 'mongosh.exe unavailable; refusing forced termination.' }
$uri = "mongodb://127.0.0.1:$Port/?directConnection=true&serverSelectionTimeoutMS=1000"
$ErrorActionPreference = 'Continue'
& $mongoshPath $uri --quiet --eval "db.getSiblingDB('admin').shutdownServer()" 2>$null
$ErrorActionPreference = 'Stop'
for ($i=0; $i -lt 20; $i++) {
  if (-not (Get-Process -Id $pidValue -ErrorAction SilentlyContinue)) { Remove-Item -LiteralPath $pidFile -ErrorAction SilentlyContinue; Write-Host 'Project MongoDB stopped cleanly.'; exit 0 }
  Start-Sleep -Milliseconds 500
}
throw "Owned MongoDB PID $pidValue did not stop cleanly. No force-kill was attempted."
