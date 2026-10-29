param([int]$Port = 27018)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$dataRoot = Join-Path $projectRoot '.data\mongo-dev'
$ownerFile = Join-Path $dataRoot 'project-instance.json'
$pidFile = Join-Path $dataRoot 'mongod.pid'
$allListeners = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue
$listener = @($allListeners | Where-Object { $_.LocalAddress -eq '127.0.0.1' })
if (-not $listener) { Write-Output "UNAVAILABLE: no listener at 127.0.0.1:$Port"; exit 1 }
if (@($allListeners | Where-Object { $_.LocalAddress -ne '127.0.0.1' }).Count -gt 0) { throw 'A non-loopback listener also uses this port; refusing to treat it as the project database.' }
if (-not (Test-Path $ownerFile) -or -not (Test-Path $pidFile)) { throw 'Listener has no project ownership marker; refusing to treat it as this database.' }
$owner = Get-Content -Raw $ownerFile | ConvertFrom-Json
if ($owner.projectRoot -ne $projectRoot -or $owner.port -ne $Port -or $owner.replicaSet -ne 'storytool-dev') { throw 'Project ownership marker mismatch.' }
$pidValue = [int](Get-Content -Raw $pidFile)
if ($listener.OwningProcess -notcontains $pidValue) { throw 'Port listener PID does not match project-owned PID.' }
$configFile = Join-Path $dataRoot 'mongod.cfg'
$proc = Get-CimInstance Win32_Process -Filter "ProcessId=$pidValue" -ErrorAction SilentlyContinue
$expectedExe = Join-Path $env:ProgramFiles 'MongoDB\Server\8.0\bin\mongod.exe'
if (-not $proc -or $proc.ExecutablePath -ne $expectedExe -or $proc.CommandLine -notmatch [regex]::Escape($configFile)) { throw 'Project PID does not match the expected mongod executable and config.' }
$mongoshPath = Join-Path $env:ProgramFiles 'MongoDB\Shell\mongosh.exe'
if (-not (Test-Path $mongoshPath)) {
  $mongosh = Get-Command mongosh.exe -ErrorAction SilentlyContinue
  if ($mongosh -and (Get-Item $mongosh.Source).Length -gt 1000000) { $mongoshPath = $mongosh.Source }
}
if (-not (Test-Path $mongoshPath)) { throw 'mongosh.exe not found.' }
$uri = "mongodb://127.0.0.1:$Port/?directConnection=true&serverSelectionTimeoutMS=1000"
$ErrorActionPreference = 'Continue'
$hello = & $mongoshPath $uri --quiet --eval 'JSON.stringify(db.adminCommand({hello:1}))'
$ErrorActionPreference = 'Stop'
if ($LASTEXITCODE -ne 0 -or $hello -notmatch '"setName"\s*:\s*"storytool-dev"' -or $hello -notmatch '"isWritablePrimary"\s*:\s*true') { throw 'MongoDB is reachable but is not storytool-dev PRIMARY.' }
Write-Output "PRIMARY: storytool-dev at 127.0.0.1:$Port; PID=$pidValue; database=story_tool_web_dev"
