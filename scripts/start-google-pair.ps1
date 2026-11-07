# Start the requested Google-only CQA profile from any directory.
#Requires -Version 7.0
param([switch]$CheckOnly, [switch]$KeyDialog)
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repo '.venv\Scripts\python.exe'
function Write-LauncherStatus([string]$state, [string]$message) {
    if (!$KeyDialog) { return }
    try {
        $dir = [IO.Directory]::CreateDirectory((Join-Path $repo 'data'))
        $path = Join-Path $dir.FullName 'google-startup.json'
        $json = @{state=$state; message=$message; updated_at=[datetimeoffset]::Now.ToString('o')} | ConvertTo-Json
        [IO.File]::WriteAllText("$path.$PID.tmp", $json)
        [IO.File]::Move("$path.$PID.tmp", $path, $true)
    } catch { Write-Warning 'Не удалось сохранить локальный статус запуска.' }
}
# Only messages authored below may be displayed or written to the local status file.
trap {
    if (!$KeyDialog) { break }
    $message = if ($_.Exception -is [ApplicationException] -or $_.Exception -is [NotSupportedException]) {
        $_.Exception.Message
    } else { 'Локальный запуск завершился с ошибкой. Проверьте установку и доступность порта 8765.' }
    Write-LauncherStatus 'failed' $message
    if (!$CheckOnly) {
        Add-Type -AssemblyName PresentationFramework
        [void][System.Windows.MessageBox]::Show($message, 'CQA — ошибка подключения')
    }
    exit 1
}
if (!(Test-Path -LiteralPath $python)) { throw 'Run scripts/setup.ps1 first.' }
$Host.UI.RawUI.WindowTitle = 'CQA - Gemini + Gemma'
$env:CQA_MODEL_ENABLED = '1'
$env:CQA_MODEL_PROVIDER = 'gemini'
$env:CQA_MODEL = 'gemini-2.5-flash'
$env:CQA_FALLBACK_PROVIDER = 'gemini'
$env:CQA_FALLBACK_MODEL = 'gemma-3-27b-it'
function Read-GoogleKeyDialog([string]$notice) {
    Add-Type -AssemblyName PresentationFramework
    [xml]$layout = @'
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        Title="CQA — подключение Google" Width="460" SizeToContent="Height"
        ResizeMode="NoResize" WindowStartupLocation="CenterScreen">
  <StackPanel Margin="22">
    <TextBlock TextWrapping="Wrap" Margin="0,0,0,12">В Google AI Studio нажмите Copy API key. Вернитесь сюда и вставьте ключ в поле с помощью Ctrl+V.</TextBlock>
    <PasswordBox Name="KeyBox" Height="32" Padding="5" AutomationProperties.Name="Ключ Google AI Studio"/>
    <TextBlock Name="Hint" TextWrapping="Wrap" Margin="0,8,0,16"/>
    <StackPanel Orientation="Horizontal" HorizontalAlignment="Right">
      <Button IsCancel="True" Padding="12,5" Margin="0,0,8,0">Отмена</Button>
      <Button Name="Connect" IsDefault="True" IsEnabled="False" Padding="12,5">Подключить</Button>
    </StackPanel>
  </StackPanel>
</Window>
'@
    $reader = [System.Xml.XmlNodeReader]::new($layout)
    try { $window = [System.Windows.Markup.XamlReader]::Load($reader) }
    finally { $reader.Dispose() }
    $box = $window.FindName('KeyBox')
    $button = $window.FindName('Connect')
    $hint = $window.FindName('Hint')
    $hint.Text = if ($notice) { $notice } else { 'Поле пустое.' }
    $box.Add_PasswordChanged({
        $entry = $box.SecurePassword
        try { $length = $entry.Length } finally { $entry.Dispose() }
        $button.IsEnabled = $length -gt 1
        $hint.Text = if ($length -eq 0) { 'Поле пустое.' }
            elseif ($length -eq 1) { 'Введён один символ. Вставьте ключ целиком.' }
            else { "Введено символов: $length" }
    })
    $button.Add_Click({ $window.DialogResult = $true })
    $window.Add_ContentRendered({ [void]$box.Focus() })
    try {
        if ($window.ShowDialog() -eq $true) { return $box.SecurePassword }
    } finally { $box.Clear() }
}
function Get-ModelContext([string]$id, [string]$key) {
    $uri = "https://generativelanguage.googleapis.com/v1beta/models/$id"
    try {
        $m = Invoke-RestMethod -Uri $uri -Headers @{'x-goog-api-key'=$key} -TimeoutSec 15 `
            -MaximumRedirection 0 -SkipHttpErrorCheck -StatusCodeVariable httpCode
    } catch {
        throw [ApplicationException]::new("Не удалось связаться с каталогом Google для ${id}. Проверьте подключение к сети.")
    }
    if ($httpCode -ne 200) {
        # Map known Google errors to safe messages. Never print the raw response or request headers.
        $reasons = @($m.error.details | ForEach-Object { $_.reason })
        if ($httpCode -eq 401 -or 'API_KEY_INVALID' -in $reasons -or 'API_KEY_EXPIRED' -in $reasons -or
            $m.error.message -match 'API key (?:not valid|expired)') {
            throw [System.Security.Authentication.AuthenticationException]::new(
                "Google отклонил ключ (HTTP $httpCode). Заново скопируйте его кнопкой Copy API key."
            )
        }
        $hint = if ($m.error.message -match 'user location is not supported') {
            'Google сообщает, что регион запроса не поддерживается.'
        } else {
            switch ([int]$httpCode) {
                403 { 'Проверьте ограничения ключа и доступ проекта к Gemini API.' }
                404 { 'Google не нашёл эту модель для вашего проекта. Проверьте список моделей.' }
                429 { 'Google ограничил частоту запросов. Проверьте квоту проекта в AI Studio.' }
                default { 'Проверьте доступ проекта к модели в AI Studio.' }
            }
        }
        $message = "Каталог Google, $id (HTTP $httpCode). $hint"
        if ($httpCode -eq 404) { throw [NotSupportedException]::new($message) }
        throw [ApplicationException]::new($message)
    }
    if ($m.supportedGenerationMethods -notcontains 'generateContent' -or !($m.inputTokenLimit -gt 0)) {
        throw [NotSupportedException]::new("Для $id в этом проекте недоступен метод generateContent или размер контекста.")
    }
    Write-Host "Found $id in the Google model catalog."
    return [string]$m.inputTokenLimit
}
# Metadata requests only. Inference starts when the operator submits an evaluation job.
$key = $env:GEMINI_API_KEY
$notice = ''
while ($true) {
    if (!$key) {
        Write-LauncherStatus 'awaiting_key' $notice
        $secret = if ($KeyDialog) { Read-GoogleKeyDialog $notice } else {
            Read-Host 'Вставьте ключ из AI Studio правой кнопкой или Shift+Insert, затем Enter (скрытый ввод)' -AsSecureString
        }
        if ($null -eq $secret) { Write-LauncherStatus 'cancelled' 'Ввод отменён.'; Write-Host 'Запуск отменён.'; return }
        try { $key = [System.Net.NetworkCredential]::new('', $secret).Password.Trim() }
        finally { $secret.Dispose() }
        if (!$key) { Write-LauncherStatus 'cancelled' 'Поле осталось пустым.'; Write-Host 'Запуск отменён.'; return }
    }
    try {
        if ($key.Length -eq 1 -or $key -match '[\s\p{Cc}]') {
            throw [System.Security.Authentication.AuthenticationException]::new(
                'Вставка неполная или содержит пробелы. Скопируйте ключ целиком кнопкой Copy API key.'
            )
        }
        $catalog = @{}
        $missing = @()
        foreach ($id in @($env:CQA_MODEL, $env:CQA_FALLBACK_MODEL)) {
            Write-LauncherStatus 'checking' "Проверяется $id."
            try { $catalog[$id] = Get-ModelContext $id $key }
            catch [NotSupportedException] { $missing += $_.Exception.Message }
            catch [ApplicationException] {
                if (!$catalog.Count) { throw }
                $missing += $_.Exception.Message
            }
        }
        if (!$catalog.Count) { throw [ApplicationException]::new(($missing -join ' ')) }
        if ($catalog.Count -eq 1) {
            $env:CQA_MODEL = @($catalog.Keys)[0]
            Remove-Item Env:CQA_FALLBACK_PROVIDER, Env:CQA_FALLBACK_MODEL, Env:CQA_FALLBACK_CONTEXT_TOKENS -ErrorAction SilentlyContinue
            Write-Warning "$missing Запуск с одной моделью: $env:CQA_MODEL."
        } else {
            $env:CQA_FALLBACK_CONTEXT_TOKENS = $catalog[$env:CQA_FALLBACK_MODEL]
        }
        $env:CQA_CONTEXT_TOKENS = $catalog[$env:CQA_MODEL]
        break
    } catch [System.Security.Authentication.AuthenticationException] {
        $notice = $_.Exception.Message
        Write-Warning $notice
        Remove-Item Env:GEMINI_API_KEY -ErrorAction SilentlyContinue
        $key = $null
    }
}
$profileMessage = "Доступна $env:CQA_MODEL."
if ($missing.Count) { $profileMessage += ' ' + ($missing -join ' ') }
Write-LauncherStatus 'catalog_checked' $profileMessage
if ($CheckOnly) { return }
$env:GEMINI_API_KEY = $key
Push-Location $repo
try {
    & $python -m uvicorn backend.app.model_app:create_configured_app --factory --host 127.0.0.1 --port 8765
    if ($LASTEXITCODE -ne 0) { throw [ApplicationException]::new("Сервер завершился с кодом $LASTEXITCODE. Проверьте, свободен ли порт 8765.") }
    Write-LauncherStatus 'stopped' 'Сервер остановлен.'
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
