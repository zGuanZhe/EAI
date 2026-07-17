$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$AuditRoot = Join-Path $env:TEMP ("eai-web-e2e-" + [guid]::NewGuid().ToString("N"))
$Personal = Join-Path $AuditRoot "personal"
$Logs = Join-Path $AuditRoot "logs"
$Secrets = Join-Path $AuditRoot "secrets.json"

function Get-FreePort {
    $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
    $listener.Start()
    $port = ([System.Net.IPEndPoint]$listener.LocalEndpoint).Port
    $listener.Stop()
    return $port
}

function Stop-ProcessTree([int]$ProcessId) {
    if (-not $ProcessId) { return }
    $children = Get-CimInstance Win32_Process -Filter "ParentProcessId=$ProcessId" -ErrorAction SilentlyContinue
    foreach ($child in $children) { Stop-ProcessTree ([int]$child.ProcessId) }
    Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
}

New-Item -ItemType Directory -Force -Path $Personal, $Logs | Out-Null
$Snapshot = Join-Path $Root "migration\personal-snapshot"
if (Test-Path $Snapshot) { Copy-Item -Path (Join-Path $Snapshot "*") -Destination $Personal -Recurse -Force -ErrorAction SilentlyContinue }
Set-Content -LiteralPath $Secrets -Encoding UTF8 -Value "{}"

$ApiPort = Get-FreePort
$WebPort = Get-FreePort
$Python = Join-Path $Root "services\api\.venv\Scripts\python.exe"
$Backend = $null
$Frontend = $null
$OldOpenAI = $env:OPENAI_API_KEY
$OldOpenRouter = $env:OPENROUTER_API_KEY
$OldMockResponse = $env:EAI_VNEXT_MOCK_OPENAI_RESPONSE
$OldCapabilityDelay = $env:EAI_V2_MOCK_CAPABILITY_DELAY_MS

try {
    Remove-Item Env:OPENAI_API_KEY -ErrorAction SilentlyContinue
    Remove-Item Env:OPENROUTER_API_KEY -ErrorAction SilentlyContinue
    $env:EAI_PERSONAL_DIR = $Personal
    $env:EAI_ATLAS_CACHE_DIR = Join-Path $Root "resources\atlas-cache"
    $env:EAI_VNEXT_ROOT = $Root
    $env:EAI_VNEXT_SECRETS = $Secrets
    $env:VITE_EAI_API_TARGET = "http://127.0.0.1:$ApiPort"
    # Keep this script ASCII-compatible because Windows PowerShell 5.1 may read UTF-8
    # files without a BOM using the active ANSI code page.
    $env:EAI_VNEXT_MOCK_OPENAI_RESPONSE = "Agent Runtime v2 natural response."
    $env:EAI_V2_MOCK_CAPABILITY_DELAY_MS = "350"

    $Backend = Start-Process -FilePath $Python -ArgumentList "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", $ApiPort -WorkingDirectory (Join-Path $Root "services\api") -RedirectStandardOutput (Join-Path $Logs "backend.out.log") -RedirectStandardError (Join-Path $Logs "backend.err.log") -WindowStyle Hidden -PassThru
    $Frontend = Start-Process -FilePath (Get-Command npm.cmd).Source -ArgumentList "--workspace", "@eai/web", "run", "dev", "--", "--host", "127.0.0.1", "--port", $WebPort -WorkingDirectory $Root -RedirectStandardOutput (Join-Path $Logs "web.out.log") -RedirectStandardError (Join-Path $Logs "web.err.log") -WindowStyle Hidden -PassThru

    $ready = $false
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        try {
            Invoke-RestMethod "http://127.0.0.1:$ApiPort/api/vnext/health" -TimeoutSec 1 | Out-Null
            Invoke-WebRequest "http://127.0.0.1:$WebPort" -UseBasicParsing -TimeoutSec 1 | Out-Null
            $ready = $true
            break
        } catch { Start-Sleep -Milliseconds 300 }
    }
    if (-not $ready) { throw "E2E servers did not become ready. Logs: $Logs" }

    $env:EAI_UI_URL = "http://127.0.0.1:$WebPort"
    $env:EAI_API_URL = "http://127.0.0.1:$ApiPort/api/vnext"
    & $Python (Join-Path $Root "apps\web\tests\agent_flow_ui.py")
    if ($LASTEXITCODE -ne 0) { throw "Playwright web E2E failed. Logs: $Logs" }
} finally {
    if ($Frontend) { Stop-ProcessTree $Frontend.Id }
    if ($Backend) { Stop-ProcessTree $Backend.Id }
    if ($Frontend) { Wait-Process -Id $Frontend.Id -Timeout 5 -ErrorAction SilentlyContinue }
    if ($Backend) { Wait-Process -Id $Backend.Id -Timeout 5 -ErrorAction SilentlyContinue }
    if ($null -ne $OldOpenAI) { $env:OPENAI_API_KEY = $OldOpenAI } else { Remove-Item Env:OPENAI_API_KEY -ErrorAction SilentlyContinue }
    if ($null -ne $OldOpenRouter) { $env:OPENROUTER_API_KEY = $OldOpenRouter } else { Remove-Item Env:OPENROUTER_API_KEY -ErrorAction SilentlyContinue }
    if ($null -ne $OldMockResponse) { $env:EAI_VNEXT_MOCK_OPENAI_RESPONSE = $OldMockResponse } else { Remove-Item Env:EAI_VNEXT_MOCK_OPENAI_RESPONSE -ErrorAction SilentlyContinue }
    if ($null -ne $OldCapabilityDelay) { $env:EAI_V2_MOCK_CAPABILITY_DELAY_MS = $OldCapabilityDelay } else { Remove-Item Env:EAI_V2_MOCK_CAPABILITY_DELAY_MS -ErrorAction SilentlyContinue }
    if (Test-Path $AuditRoot) {
        $resolved = (Resolve-Path -LiteralPath $AuditRoot).Path
        $temp = (Resolve-Path -LiteralPath $env:TEMP).Path
        if ($resolved.StartsWith($temp, [System.StringComparison]::OrdinalIgnoreCase) -and (Split-Path -Leaf $resolved) -like "eai-web-e2e-*") {
            for ($attempt = 0; $attempt -lt 5; $attempt++) {
                try {
                    Remove-Item -LiteralPath $resolved -Recurse -Force -ErrorAction Stop
                    break
                } catch {
                    if ($attempt -eq 4) { Write-Warning "Could not remove isolated E2E directory: $resolved" }
                    Start-Sleep -Milliseconds 250
                }
            }
        }
    }
}
