$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root "services\api\.venv\Scripts\python.exe"
$BinaryDir = Join-Path $Root "apps\desktop\src-tauri\binaries"
$Target = "x86_64-pc-windows-msvc"
$Name = "eai-service-$Target"

if (-not (Test-Path $Python)) { & (Join-Path $PSScriptRoot "bootstrap.ps1") }
& $Python -m pip install -r (Join-Path $Root "services\api\requirements.txt") "pyinstaller>=6.21.0"
if ($LASTEXITCODE -ne 0) {
    Write-Warning "Configured pip index failed; retrying with the official PyPI index."
    & $Python -m pip install --index-url https://pypi.org/simple -r (Join-Path $Root "services\api\requirements.txt") "pyinstaller>=6.21.0"
    if ($LASTEXITCODE -ne 0) { throw "Sidecar dependency installation failed with exit code $LASTEXITCODE" }
}
New-Item -ItemType Directory -Force -Path $BinaryDir | Out-Null
& $Python -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --noconsole `
    --name $Name `
    --paths (Join-Path $Root "services\api") `
    --distpath $BinaryDir `
    --workpath (Join-Path $Root "build\pyinstaller") `
    --specpath (Join-Path $Root "build") `
    (Join-Path $Root "services\api\desktop_entry.py")
if ($LASTEXITCODE -ne 0) { throw "PyInstaller sidecar build failed with exit code $LASTEXITCODE" }

$Output = Join-Path $BinaryDir "$Name.exe"
if (-not (Test-Path $Output)) { throw "PyInstaller completed without producing $Output" }
