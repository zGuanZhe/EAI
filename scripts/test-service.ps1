$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root "services\api\.venv\Scripts\python.exe"
& $Python -m py_compile (Join-Path $Root "services\api\app\main.py") (Join-Path $Root "services\api\app\factory.py") (Join-Path $Root "services\api\desktop_entry.py")
if ($LASTEXITCODE -ne 0) { throw "Service py_compile failed with exit code $LASTEXITCODE" }
Push-Location (Join-Path $Root "services\api")
try {
    & $Python -m unittest discover -s tests -v
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} finally { Pop-Location }
