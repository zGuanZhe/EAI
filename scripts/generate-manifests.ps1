$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot

function Write-Manifest($Source, $Output) {
    $Resolved = (Resolve-Path $Source).Path
    $Rows = Get-ChildItem -LiteralPath $Resolved -Recurse -File | Sort-Object FullName | ForEach-Object {
        [pscustomobject]@{
            path = $_.FullName.Substring($Resolved.Length + 1).Replace("\", "/")
            bytes = $_.Length
            sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        }
    }
    $Rows | ConvertTo-Json -Depth 3 | Set-Content -LiteralPath $Output -Encoding UTF8
}

Write-Manifest (Join-Path $Root "resources\atlas-cache") (Join-Path $Root "resources\atlas-cache.manifest.json")
Write-Manifest (Join-Path $Root "migration\personal-snapshot") (Join-Path $Root "migration\personal-snapshot.manifest.json")
