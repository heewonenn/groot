# Start the requested Google-only CQA profile from any directory.
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repo '.venv\Scripts\python.exe'
if (!(Test-Path -LiteralPath $python)) { throw 'Run scripts/setup.ps1 first.' }
$Host.UI.RawUI.WindowTitle = 'CQA - Gemini + Gemma'
if (!$env:GEMINI_API_KEY) {
    $secret = Read-Host 'Paste the Google AI Studio key (hidden), then press Enter' -AsSecureString
    $env:GEMINI_API_KEY = [System.Net.NetworkCredential]::new('', $secret).Password.Trim()
    $secret.Dispose()
}
if (!$env:GEMINI_API_KEY) { throw 'No key entered.' }
$env:CQA_MODEL_ENABLED = '1'
$env:CQA_MODEL_PROVIDER = 'gemini'
$env:CQA_MODEL = 'gemini-2.5-flash'
$env:CQA_FALLBACK_PROVIDER = 'gemini'
$env:CQA_FALLBACK_MODEL = 'gemma-3-27b-it'
function Get-ModelContext([string]$id) {
    $uri = "https://generativelanguage.googleapis.com/v1beta/models/$id"
    try {
        $m = Invoke-RestMethod -Uri $uri -Headers @{'x-goog-api-key'=$env:GEMINI_API_KEY} -TimeoutSec 15
    } catch {
        $status = $_.Exception.Response.StatusCode
        throw "Cannot look up $id in this Google project (HTTP $status). Check the key and model access in AI Studio."
    }
    if ($m.supportedGenerationMethods -notcontains 'generateContent' -or !($m.inputTokenLimit -gt 0)) {
        throw "Google does not list a usable generateContent endpoint for $id in this project."
    }
    Write-Host "Found $id in the Google model catalog."
    return [string]$m.inputTokenLimit
}
# Metadata requests only. Inference starts when the operator submits an evaluation job.
$env:CQA_CONTEXT_TOKENS = Get-ModelContext $env:CQA_MODEL
$env:CQA_FALLBACK_CONTEXT_TOKENS = Get-ModelContext $env:CQA_FALLBACK_MODEL
Push-Location $repo
try {
    & $python -m uvicorn backend.app.model_app:create_configured_app --factory --host 127.0.0.1 --port 8765
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
