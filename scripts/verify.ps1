$ErrorActionPreference = 'Stop'
Push-Location (Split-Path $PSScriptRoot -Parent)
try {
    & .\.venv\Scripts\python.exe -m pip check
    if ($LASTEXITCODE) { throw 'Dependency check failed' }
    & .\.venv\Scripts\python.exe -m ruff check backend
    if ($LASTEXITCODE) { throw 'Python lint failed' }
    & .\.venv\Scripts\python.exe -m pytest -q
    if ($LASTEXITCODE) { throw 'Python tests failed' }
    Push-Location frontend
    try {
        npm run lint
        if ($LASTEXITCODE) { throw 'Frontend lint failed' }
        npm run build
        if ($LASTEXITCODE) { throw 'Frontend build failed' }
        npm run test:e2e
        if ($LASTEXITCODE) { throw 'Browser tests failed' }
    } finally { Pop-Location }
} finally { Pop-Location }
