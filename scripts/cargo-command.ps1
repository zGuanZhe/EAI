$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Port = 4783
$Stdout = Join-Path $Root "runtime\cargo-proxy.log"
New-Item -ItemType Directory -Force -Path (Split-Path $Stdout) | Out-Null
$Proxy = Start-Process -FilePath "node.exe" -ArgumentList (Join-Path $PSScriptRoot "cargo-registry-proxy.mjs"),$Port -WindowStyle Hidden -RedirectStandardOutput $Stdout -PassThru
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
    New-Item -ItemType Directory -Force -Path $env:CARGO_HOME | Out-Null
    @"
[source.crates-io]
replace-with = "eai-proxy"

[source.eai-proxy]
registry = "sparse+http://127.0.0.1:$Port/"

[http]
timeout = 600
low-speed-limit = 1
"@ | Set-Content -LiteralPath (Join-Path $env:CARGO_HOME "config.toml") -Encoding UTF8
    $Toolchain = "$env:USERPROFILE\.rustup\toolchains\stable-x86_64-pc-windows-msvc\bin"
    $env:Path = "$Toolchain;$env:Path"
    $Cargo = Join-Path $Toolchain "cargo.exe"
    if (-not $args.Count) { throw "A Cargo command is required" }
    & $Cargo @args
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} finally {
    if ($Proxy -and -not $Proxy.HasExited) { Stop-Process -Id $Proxy.Id -Force }
}
