param(
  [string]$DbPath = (Join-Path (Split-Path -Parent $PSScriptRoot) ".data\mongo-dev"),
  [int]$Port = 27017,
  [int]$WaitSeconds = 30
)
$ErrorActionPreference = "Stop"
$mongod = Get-Command mongod -ErrorAction SilentlyContinue
$mongosh = Get-Command mongosh -ErrorAction SilentlyContinue
if (-not $mongod) { throw "mongod was not found. Install MongoDB Community locally, then rerun this script." }
if (-not $mongosh) { throw "mongosh was not found. Install MongoDB Shell locally, then rerun this script." }
New-Item -ItemType Directory -Force -Path $DbPath | Out-Null
$stdoutPath = Join-Path $DbPath "mongod.stdout.log"
$stderrPath = Join-Path $DbPath "mongod.stderr.log"
$alreadyListening = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue
$startedHere = $false
if (-not $alreadyListening) {
  Write-Host "Starting development MongoDB replica set on 127.0.0.1:$Port"
  Start-Process -FilePath $mongod.Source -ArgumentList @("--dbpath", $DbPath, "--port", "$Port", "--bind_ip", "127.0.0.1", "--replSet", "storytool-dev") -WindowStyle Hidden -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath | Out-Null
  $startedHere = $true
}
$ready = $false
for ($i = 0; $i -lt $WaitSeconds; $i++) {
  & $mongosh.Source "mongodb://127.0.0.1:$Port/?serverSelectionTimeoutMS=750" --quiet --eval "db.adminCommand({ping:1}).ok" *> $null
  if ($LASTEXITCODE -eq 0) { $ready = $true; break }
  Start-Sleep -Seconds 1
}
if (-not $ready) { throw "MongoDB did not become available on 127.0.0.1:$Port within $WaitSeconds seconds. Logs: $stdoutPath and $stderrPath" }
$hello = & $mongosh.Source "mongodb://127.0.0.1:$Port/?serverSelectionTimeoutMS=750" --quiet --eval "JSON.stringify(db.adminCommand({hello:1}))"
if ($LASTEXITCODE -ne 0) { throw "MongoDB hello check failed. See $logPath" }
if ($hello -notmatch '"setName"\s*:\s*"storytool-dev"') {
  if (-not $startedHere) { throw "A MongoDB listener already owns port $Port but is not storytool-dev. Refusing to modify it." }
  & $mongosh.Source "mongodb://127.0.0.1:$Port/?serverSelectionTimeoutMS=750" --quiet --eval "rs.initiate({_id:'storytool-dev',members:[{_id:0,host:'127.0.0.1:$Port'}]})"
if ($LASTEXITCODE -ne 0) { throw "Could not initialize storytool-dev replica set. See $stdoutPath and $stderrPath" }
}
$primary = $false
for ($i = 0; $i -lt $WaitSeconds; $i++) {
  $hello = & $mongosh.Source "mongodb://127.0.0.1:$Port/?serverSelectionTimeoutMS=750" --quiet --eval "JSON.stringify(db.adminCommand({hello:1}))"
  if ($hello -match '"setName"\s*:\s*"storytool-dev"' -and $hello -match '"isWritablePrimary"\s*:\s*true') { $primary = $true; break }
  Start-Sleep -Seconds 1
}
if (-not $primary) { throw "storytool-dev did not elect its local primary within $WaitSeconds seconds. See $stdoutPath and $stderrPath" }
Write-Host "Development replica set storytool-dev is initialized on loopback. Database: story_tool_web_dev"
