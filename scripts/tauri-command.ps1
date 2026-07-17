$ErrorActionPreference = "Stop"
$TauriArguments = @($args)
$Root = Split-Path -Parent $PSScriptRoot
$Port = 4783
$Log = Join-Path $Root "runtime\cargo-proxy.log"
New-Item -ItemType Directory -Force -Path (Split-Path $Log) | Out-Null
$Proxy = Start-Process -FilePath "node.exe" -ArgumentList (Join-Path $PSScriptRoot "cargo-registry-proxy.mjs"),$Port -WindowStyle Hidden -RedirectStandardOutput $Log -PassThru
try {
    $Ready = $false
    for ($attempt = 0; $attempt -lt 40; $attempt++) {
        try {
            Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$Port/config.json" -TimeoutSec 1 | Out-Null
            $Ready = $true
            break
        } catch { Start-Sleep -Milliseconds 100 }
    }
    if (-not $Ready) { throw "Local Cargo registry proxy failed to start" }
    $env:CARGO_HOME = Join-Path $Root "runtime\cargo-proxy-home-v2"
    $Toolchain = "$env:USERPROFILE\.rustup\toolchains\stable-x86_64-pc-windows-msvc\bin"
    $env:Path = "$Toolchain;$env:Path"
    Push-Location $Root
    try {
        & (Get-Command npx.cmd).Source tauri @TauriArguments --config apps/desktop/src-tauri/tauri.conf.json
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    } finally { Pop-Location }
} finally {
    if ($Proxy -and -not $Proxy.HasExited) { Stop-Process -Id $Proxy.Id -Force }
}
