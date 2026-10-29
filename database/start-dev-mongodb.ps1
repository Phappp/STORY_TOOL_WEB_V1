param([int]$Port = 27018, [int]$WaitSeconds = 45)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$dataRoot = Join-Path $projectRoot '.data\mongo-dev'
$ownerFile = Join-Path $dataRoot 'project-instance.json'
$pidFile = Join-Path $dataRoot 'mongod.pid'
$logDir = Join-Path $dataRoot 'log'
$configFile = Join-Path $dataRoot 'mongod.cfg'
$replicaSet = 'storytool-dev'
$dbPath = Join-Path $dataRoot 'data'

function Get-ProjectProcess {
  if (-not (Test-Path $pidFile)) { return $null }
  if (-not (Test-Path $ownerFile)) { throw 'Project PID exists without its ownership marker. Refusing to manage it.' }
  $owner = Get-Content -Raw $ownerFile | ConvertFrom-Json
  if ($owner.projectRoot -ne $projectRoot -or $owner.port -ne $Port -or $owner.replicaSet -ne $replicaSet -or $owner.dbPath -ne $dbPath) { throw 'Project ownership marker mismatch. Refusing to manage the listener.' }
  $pidValue = [int](Get-Content -Raw $pidFile)
  $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$pidValue" -ErrorAction SilentlyContinue
  if (-not $proc) { return $null }
  $expectedExe = Join-Path $env:ProgramFiles 'MongoDB\Server\8.0\bin\mongod.exe'
  if ($proc.ExecutablePath -ne $expectedExe -or $proc.CommandLine -notmatch [regex]::Escape($configFile)) { throw "PID $pidValue is not the owned project MongoDB process." }
  $config = Get-Content -Raw $configFile
  if ($config -notmatch '(?m)^\s+bindIp:\s*127\.0\.0\.1\s*$' -or $config -notmatch "(?m)^\s+port:\s*$Port\s*$" -or $config -notmatch '(?m)^\s+replSetName:\s*storytool-dev\s*$' -or $config -notmatch [regex]::Escape($dbPath.Replace('\','/'))) { throw 'Project MongoDB config no longer matches the isolated loopback settings.' }
  return $proc
}

$listener = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue
if ($listener) {
  $owned = Get-ProjectProcess
  if (-not $owned -or $listener.OwningProcess -notcontains $owned.ProcessId -or @($listener | Where-Object { $_.LocalAddress -ne '127.0.0.1' }).Count -gt 0) { throw "Port $Port is occupied by an unverified or non-loopback listener. No changes were made." }
  Write-Host "Verifying existing project MongoDB on 127.0.0.1:$Port"
} else {
  $tcp = [System.Net.Sockets.TcpClient]::new()
  try { $tcp.Connect('127.0.0.1', $Port); throw "Port 127.0.0.1:$Port became occupied." }
  catch [System.Net.Sockets.SocketException] { }
  finally { $tcp.Dispose() }
  $mongodCmd = Get-Command mongod.exe -ErrorAction SilentlyContinue
  $mongoshCmd = Get-Command mongosh.exe -ErrorAction SilentlyContinue
  if ($mongodCmd) { $mongodPath = $mongodCmd.Source } else { $mongodPath = Join-Path $env:ProgramFiles 'MongoDB\Server\8.0\bin\mongod.exe' }
  if ($mongoshCmd) { $mongoshPath = $mongoshCmd.Source } else { $mongoshPath = Join-Path $env:ProgramFiles 'MongoDB\Shell\mongosh.exe' }
  if (-not (Test-Path $mongodPath)) { throw 'MongoDB Community mongod.exe is missing. Install manually; this script does not download or install software.' }
  if (-not (Test-Path $mongoshPath)) { throw 'MongoDB Shell mongosh.exe is missing. Install manually; this script does not download or install software.' }
  if (Test-Path $dataRoot) {
    if (-not (Test-Path $ownerFile)) { throw "Data directory exists without project ownership marker: $dataRoot. Refusing to reuse it." }
    $owner = Get-Content -Raw $ownerFile | ConvertFrom-Json
    if ($owner.projectRoot -ne $projectRoot -or $owner.port -ne $Port -or $owner.replicaSet -ne $replicaSet -or $owner.dbPath -ne $dbPath) { throw 'Project ownership marker mismatch. Refusing to continue.' }
  } else {
    New-Item -ItemType Directory -Path $dataRoot | Out-Null
    @{ projectRoot = $projectRoot; port = $Port; replicaSet = $replicaSet; dbPath = $dbPath } | ConvertTo-Json | Set-Content -Encoding utf8 $ownerFile
  }
  New-Item -ItemType Directory -Force -Path $dbPath,$logDir | Out-Null
  $yamlDbPath = $dbPath.Replace('\','/')
  $yamlLogPath = (Join-Path $logDir 'mongod.log').Replace('\','/')
  $configText = @(
    'storage:', "  dbPath: `"$yamlDbPath`"", 'systemLog:', '  destination: file',
    "  path: `"$yamlLogPath`"", '  logAppend: true', 'net:', '  bindIp: 127.0.0.1',
    "  port: $Port", 'replication:', "  replSetName: $replicaSet"
  ) -join "`n"
  Set-Content -Encoding utf8 $configFile $configText
  $proc = Start-Process -FilePath $mongodPath -ArgumentList @('--config', $configFile) -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru
  Set-Content -NoNewline -Encoding ascii $pidFile $proc.Id
  Write-Host "Started project MongoDB PID $($proc.Id), loopback $Port, data $dbPath"
}

$mongoshPath = Join-Path $env:ProgramFiles 'MongoDB\Shell\mongosh.exe'
if (-not (Test-Path $mongoshPath)) {
  $mongoshCmd = Get-Command mongosh.exe -ErrorAction SilentlyContinue
  if ($mongoshCmd -and (Get-Item $mongoshCmd.Source).Length -gt 1000000) { $mongoshPath = $mongoshCmd.Source }
}
if (-not (Test-Path $mongoshPath)) { throw 'mongosh.exe not found. Install MongoDB Shell manually.' }
$uri = "mongodb://127.0.0.1:$Port/?directConnection=true&serverSelectionTimeoutMS=1000"
$hello = $null
$ErrorActionPreference = 'Continue'
for ($i=0; $i -lt $WaitSeconds; $i++) {
  $hello = & $mongoshPath $uri --quiet --eval 'JSON.stringify(db.adminCommand({hello:1}))' 2>$null
  if ($LASTEXITCODE -eq 0 -and $hello) { break }
  Start-Sleep -Seconds 1
}
$ErrorActionPreference = 'Stop'
if (-not $hello) { throw "Project MongoDB did not start. Inspect $logDir\mongod.log" }
if ($hello -notmatch '"setName"\s*:\s*"storytool-dev"') {
  if (-not (Test-Path $ownerFile)) { throw 'Replica-set initialization refused: project ownership is unverified.' }
  & $mongoshPath $uri --quiet --eval "rs.initiate({_id:'storytool-dev',members:[{_id:0,host:'127.0.0.1:$Port'}]})"
  if ($LASTEXITCODE -ne 0) { throw 'Replica-set initialization failed; inspect project log.' }
}
$primary = $false
for ($i=0; $i -lt $WaitSeconds; $i++) {
  $hello = & $mongoshPath $uri --quiet --eval 'JSON.stringify(db.adminCommand({hello:1}))' 2>$null
  if ($LASTEXITCODE -eq 0 -and $hello -match '"setName"\s*:\s*"storytool-dev"' -and $hello -match '"isWritablePrimary"\s*:\s*true') { $primary = $true; break }
  Start-Sleep -Seconds 1
}
if (-not $primary) { throw 'storytool-dev did not become PRIMARY within the timeout.' }
Write-Host "PASS: storytool-dev PRIMARY on 127.0.0.1:$Port; database=story_tool_web_dev"
