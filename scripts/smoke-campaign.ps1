$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Fixture = Join-Path $Root "tests\fixtures\campaign"

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker CLI is required for the Campaign smoke test."
}

$output = & docker run --rm --network none --cpus 1 --memory 512m `
    --mount "type=bind,src=$Fixture,dst=/workspace,readonly" `
    --workdir /workspace python:3.11-slim python runfile.py
if ($LASTEXITCODE -ne 0) { throw "Campaign Docker fixture failed with exit code $LASTEXITCODE" }
if (-not ($output -match '^EAI_METRIC:\{')) { throw "Campaign Docker fixture did not emit a structured metric." }
Write-Output $output
