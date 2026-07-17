$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$env:Path = "$env:USERPROFILE\.cargo\bin;$env:Path"
$Venv = Join-Path $Root "services\api\.venv"

if (-not (Test-Path (Join-Path $Venv "Scripts\python.exe"))) {
    $Python313 = (& py -0p 2>$null | Select-String -Pattern '-V:3\.13\s+(.+)$').Matches.Groups[1].Value.Trim()
    if ($Python313 -and (Test-Path -LiteralPath $Python313)) {
        & $Python313 -m venv $Venv
    } else {
        Write-Host "Python 3.13 is unavailable; falling back to the active Python runtime."
        & python -m venv $Venv
    }
}
& (Join-Path $Venv "Scripts\python.exe") --version
& (Join-Path $Venv "Scripts\python.exe") -m pip install --index-url https://pypi.org/simple --upgrade pip
& (Join-Path $Venv "Scripts\python.exe") -m pip install --index-url https://pypi.org/simple -r (Join-Path $Root "services\api\requirements-dev.txt")
& (Join-Path $Venv "Scripts\python.exe") -m playwright install chromium
if ($LASTEXITCODE -ne 0) { throw "Playwright Chromium installation failed with exit code $LASTEXITCODE" }
Push-Location $Root
try { npm ci } finally { Pop-Location }
