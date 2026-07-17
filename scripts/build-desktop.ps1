$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$env:Path = "$env:USERPROFILE\.cargo\bin;$env:Path"
function Invoke-NpmScript([string]$Name) {
    & npm.cmd run $Name
    if ($LASTEXITCODE -ne 0) { throw "npm run $Name failed with exit code $LASTEXITCODE" }
}
Push-Location $Root
try {
    Invoke-NpmScript "check:mojibake"
    Invoke-NpmScript "check:architecture"
    Invoke-NpmScript "web:test"
    Invoke-NpmScript "web:build"
    Invoke-NpmScript "web:e2e"
    Invoke-NpmScript "service:test"
    Invoke-NpmScript "campaign:docker-test"
    Invoke-NpmScript "sidecar:test"
    Invoke-NpmScript "rust:fmt"
    Invoke-NpmScript "rust:clippy"
    Invoke-NpmScript "rust:test"
    & (Join-Path $PSScriptRoot "tauri-command.ps1") build
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} finally { Pop-Location }
