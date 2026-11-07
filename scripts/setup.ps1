$ErrorActionPreference = 'Stop'
Push-Location (Split-Path $PSScriptRoot -Parent)
try {
    if (!(Test-Path .venv/Scripts/python.exe)) {
        py -3.14 -m venv .venv
        if ($LASTEXITCODE) { throw 'Python 3.14 environment creation failed' }
    }
    & .\.venv\Scripts\python.exe -m pip install -r backend/requirements.lock.txt
    if ($LASTEXITCODE) { throw 'Python installation failed' }
    Push-Location frontend
    try {
        npm ci
        if ($LASTEXITCODE) { throw 'Node installation failed' }
        npx playwright install chromium
        if ($LASTEXITCODE) { throw 'Test browser installation failed' }
        npx @playwright/mcp install-browser chrome-for-testing
        if ($LASTEXITCODE) { throw 'MCP browser installation failed' }
    } finally { Pop-Location }
} finally { Pop-Location }
