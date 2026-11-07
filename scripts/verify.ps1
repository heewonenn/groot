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
        $previousP3Seed = $env:CQA_E2E_PARTICIPANT3
        try {
            $env:CQA_E2E_PARTICIPANT3 = '0'
            npm run test:e2e
            if ($LASTEXITCODE) { throw 'Browser tests failed' }
            $env:CQA_E2E_PARTICIPANT3 = '1'
            npm run test:e2e -- participant3.integration.spec.ts
            if ($LASTEXITCODE) { throw 'Participant 3 browser tests failed' }
        } finally {
            if ($null -eq $previousP3Seed) {
                Remove-Item Env:CQA_E2E_PARTICIPANT3 -ErrorAction SilentlyContinue
            } else { $env:CQA_E2E_PARTICIPANT3 = $previousP3Seed }
        }
    } finally { Pop-Location }
} finally { Pop-Location }
