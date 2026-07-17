$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot

& (Join-Path $PSScriptRoot "build-sidecar.ps1")
if ($LASTEXITCODE -ne 0) { throw "Sidecar build failed with exit code $LASTEXITCODE" }

Push-Location $Root
try {
    & node (Join-Path $PSScriptRoot "smoke-sidecar.mjs")
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} finally {
    Pop-Location
}
