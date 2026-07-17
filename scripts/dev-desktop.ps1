$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$env:Path = "$env:USERPROFILE\.cargo\bin;$env:Path"
$Binary = Join-Path $Root "apps\desktop\src-tauri\binaries\eai-service-x86_64-pc-windows-msvc.exe"
if (-not (Test-Path $Binary)) { & (Join-Path $PSScriptRoot "build-sidecar.ps1") }

$Web = Start-Process -FilePath "npm.cmd" -ArgumentList "--prefix",(Join-Path $Root "apps\web"),"run","dev" -WorkingDirectory $Root -WindowStyle Hidden -PassThru
try {
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:5173" -TimeoutSec 1
            if ($response.StatusCode -eq 200) { break }
        } catch { Start-Sleep -Milliseconds 250 }
    }
    & (Join-Path $PSScriptRoot "tauri-command.ps1") dev
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} finally {
    if ($Web -and -not $Web.HasExited) { Stop-Process -Id $Web.Id -Force }
}
