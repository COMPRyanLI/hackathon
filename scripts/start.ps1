# Start GradeLens on loopback. Ctrl+C stops it; interrupted runs can be resumed.
param(
    [ValidateRange(1, 65535)]
    [int]$Port = 8000
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $root "backend\.venv\Scripts\python.exe"

if (-not (Test-Path $venvPython)) {
    throw "backend\.venv is missing. Run .\setup-gradelens.cmd first."
}
# Check before FastAPI starts: a duplicate process must not run startup recovery
# against the same database before discovering that it cannot bind its port.
$gradeLensPortProbe = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $Port)
try {
    $gradeLensPortProbe.Start()
} catch [System.Net.Sockets.SocketException] {
    Write-Host "Port $Port is already in use. GradeLens may already be running at http://127.0.0.1:$Port" -ForegroundColor Yellow
    Write-Host "To load updated code, stop the existing GradeLens server with Ctrl+C in its terminal, then run this launcher again."
    Write-Host "No new server was started."
    exit 1
} finally {
    $gradeLensPortProbe.Stop()
}
if (-not (Test-Path (Join-Path $root "frontend\dist\index.html"))) {
    Write-Host "Frontend is not built; only the API (/docs) will be available. Run .\setup-gradelens.cmd." -ForegroundColor Yellow
}
try {
    $v = Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/version" -TimeoutSec 3
    Write-Host "Ollama $($v.version) reachable."
} catch {
    Write-Host "Ollama is not reachable; model stages will fail visibly until it is started." -ForegroundColor Yellow
}

$env:PORT = "$Port"
Write-Host "GradeLens: http://127.0.0.1:$Port   (API docs: http://127.0.0.1:$Port/docs)"
Push-Location (Join-Path $root "backend")
try {
    & $venvPython -m uvicorn app.main:app --host 127.0.0.1 --port $Port
} finally {
    Pop-Location
}
