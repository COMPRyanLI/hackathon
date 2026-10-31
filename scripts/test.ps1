# Run backend tests (synthetic fixtures; the real-snapshot check is skipped if the
# private data is absent) and the frontend type check + build.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $root "backend\.venv\Scripts\python.exe"

# Build first: browser tests must exercise this source revision, not stale dist.
Push-Location (Join-Path $root "frontend")
try {
    & npm run build
    if ($LASTEXITCODE -ne 0) { throw "Frontend type check/build failed" }
} finally { Pop-Location }
Push-Location (Join-Path $root "backend")
try {
    & $venvPython -m pytest -q
    if ($LASTEXITCODE -ne 0) { throw "Backend tests failed" }
} finally { Pop-Location }
Write-Host "All checks passed."
