# A fresh synthetic recording database on each launch. Existing databases are never reset.
param([ValidateRange(1, 65535)][int]$Port = 8002)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root 'backend/.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Run setup-gradelens.cmd first.' }
if (-not (Test-Path -LiteralPath (Join-Path $root 'frontend/dist/index.html'))) { throw 'Build the frontend first: npm --prefix frontend run build' }
$probe = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $Port)
try { $probe.Start() } catch { throw "Port $Port is in use. Stop your prior demo with Ctrl+C, or pass -Port 8003." } finally { $probe.Stop() }
$demoName = 'calibration-take-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [guid]::NewGuid().ToString('N').Substring(0, 6)
$demoDir = Join-Path $root ('data/private/' + $demoName)
New-Item -ItemType Directory -Path $demoDir | Out-Null
$saved = @{}
foreach ($name in @('DATABASE_URL', 'PRIVATE_DATA_DIR', 'PORT', 'ALLOW_CLOUD_INFERENCE', 'START_WORKER')) {
    $saved[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
}
$proc = $null
try {
    $env:DATABASE_URL = 'sqlite:///' + (Join-Path $demoDir 'gradelens.db').Replace('\', '/')
    $env:PRIVATE_DATA_DIR = $demoDir
    $env:PORT = "$Port"
    $env:ALLOW_CLOUD_INFERENCE = 'false'
    $env:START_WORKER = 'true'
    $proc = Start-Process -FilePath $python -ArgumentList @('-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', "$Port") -WorkingDirectory (Join-Path $root 'backend') -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $demoDir 'server.stdout.log') -RedirectStandardError (Join-Path $demoDir 'server.stderr.log')
    $proc.Id | Set-Content (Join-Path $demoDir 'server.pid')
    $deadline = (Get-Date).AddSeconds(45)
    do {
        if ($proc.HasExited) { throw "Server exited. Check $demoDir/server.stderr.log" }
        try { $ready = (Invoke-RestMethod "http://127.0.0.1:$Port/api/health" -TimeoutSec 2).status -eq 'ok' } catch { $ready = $false }
        if (-not $ready) { Start-Sleep -Milliseconds 250 }
    } until ($ready -or (Get-Date) -gt $deadline)
    if (-not $ready) { throw 'Demo server did not become ready.' }
    $prepared = & $python (Join-Path $PSScriptRoot 'prepare-calibration-demo.py') --base-url "http://127.0.0.1:$Port"
    if ($LASTEXITCODE -ne 0) { throw 'Demo preparation failed. See the error above.' }
    $prepared | Set-Content -Encoding utf8 (Join-Path $demoDir 'ready.json')
    Write-Host ($prepared -join "`n")
    Write-Host "Keep this terminal open. Ctrl+C stops this demo. Rerun for a fresh recording take."
    while (-not $proc.HasExited) { Start-Sleep -Milliseconds 500 }
} finally {
    if ($proc -and -not $proc.HasExited) { Stop-Process -Id $proc.Id }
    foreach ($name in $saved.Keys) { [Environment]::SetEnvironmentVariable($name, $saved[$name], 'Process') }
}
