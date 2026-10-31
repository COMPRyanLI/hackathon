# GradeLens one-time setup for Windows PowerShell 5.1+.
# Creates backend\.venv, installs pinned Python packages, installs pinned
# frontend packages, builds the UI and creates .env from .env.example if absent.
# Safe to re-run. Nothing is installed system-wide.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

function Assert-LastExit($what) {
    if ($LASTEXITCODE -ne 0) { throw "$what failed with exit code $LASTEXITCODE" }
}

Write-Host "== GradeLens setup in $root"

# --- Python 3.12 virtual environment
$venvPython = Join-Path $root "backend\.venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Host "Creating backend\.venv"
    if (Get-Command py -ErrorAction SilentlyContinue) {
        & py -3.12 -m venv (Join-Path $root "backend\.venv")
    } else {
        & python -m venv (Join-Path $root "backend\.venv")
    }
    Assert-LastExit "Creating the virtual environment"
}
& $venvPython --version
& $venvPython -m pip install --disable-pip-version-check -q -r (Join-Path $root "backend\requirements.txt")
Assert-LastExit "Installing Python packages"

# --- Frontend (pinned in package-lock.json)
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    throw "npm was not found. Install Node.js LTS, reopen PowerShell and re-run this script."
}
Push-Location (Join-Path $root "frontend")
try {
    & npm ci --no-audit --no-fund
    Assert-LastExit "npm ci"
    & npm run build
    Assert-LastExit "Frontend build"
} finally {
    Pop-Location
}

# --- Settings
$envFile = Join-Path $root ".env"
if (-not (Test-Path $envFile)) {
    Copy-Item (Join-Path $root ".env.example") $envFile
    Write-Host "Created .env from .env.example (edit it to change paths or models)."
}

# --- Model check (informational; models are never pulled automatically)
try {
    $tags = Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/tags" -TimeoutSec 5
    $names = @($tags.models | ForEach-Object { $_.name })
    foreach ($m in @("qwen3.5:4b-q4_K_M", "qwen3-embedding:0.6b")) {
        if ($names -contains $m) { Write-Host "Ollama model present: $m" }
        else { Write-Host "Ollama model MISSING: $m  ->  run: ollama pull $m" -ForegroundColor Yellow }
    }
} catch {
    Write-Host "Ollama is not reachable at http://127.0.0.1:11434. Start Ollama from the Start menu for real inference." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Setup complete. Start the app with:  .\start-gradelens.cmd   (then open http://127.0.0.1:8000)"
