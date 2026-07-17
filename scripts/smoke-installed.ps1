param(
    [string]$InstallerPath = "",
    [int]$StartupTimeoutSeconds = 45
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$TempBase = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
$RunId = [guid]::NewGuid().ToString("N")
$SmokeRoot = Join-Path $TempBase "eai-desktop-smoke-$RunId"
$InstallRoot = Join-Path $SmokeRoot "install"
$TestDataRoot = Join-Path $SmokeRoot "app-data"
$RealDataRoot = Join-Path $env:APPDATA "com.eai.desktop"
$AppProcess = $null
$Installed = $false
$OriginalTestMode = $env:EAI_DESKTOP_TEST_MODE
$OriginalDataOverride = $env:EAI_DESKTOP_APP_DATA_DIR

function Get-TreeManifest([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return @() }
    $base = [IO.Path]::GetFullPath($Path).TrimEnd('\') + '\'
    return @(
        Get-ChildItem -LiteralPath $Path -Recurse -File -Force |
            Where-Object { -not ($_.Attributes -band [IO.FileAttributes]::ReparsePoint) } |
            Sort-Object FullName |
            ForEach-Object {
                [pscustomobject]@{
                    Path = $_.FullName.Substring($base.Length).Replace('\', '/')
                    Length = $_.Length
                    Sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash
                }
            }
    )
}

function Get-SidecarProcesses {
    return @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $_.Name -like "eai-service*.exe"
    })
}

function Invoke-CheckedProcess([string]$FilePath, [string[]]$Arguments) {
    $process = Start-Process -FilePath $FilePath -ArgumentList $Arguments -PassThru -Wait -WindowStyle Hidden
    if ($process.ExitCode -ne 0) {
        throw "$FilePath failed with exit code $($process.ExitCode)"
    }
}

function Remove-SmokeRoot {
    $resolved = [IO.Path]::GetFullPath($SmokeRoot)
    if (-not $resolved.StartsWith($TempBase, [StringComparison]::OrdinalIgnoreCase) -or
        -not ([IO.Path]::GetFileName($resolved)).StartsWith("eai-desktop-smoke-")) {
        throw "Refusing to remove unexpected smoke path: $resolved"
    }
    if (Test-Path -LiteralPath $resolved) {
        Remove-Item -LiteralPath $resolved -Recurse -Force
    }
}

if (-not $InstallerPath) {
    $bundle = Join-Path $Root "apps\desktop\src-tauri\target\release\bundle\nsis"
    $InstallerPath = Get-ChildItem -LiteralPath $bundle -Filter "*.exe" -File -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTimeUtc -Descending |
        Select-Object -First 1 -ExpandProperty FullName
}
if (-not $InstallerPath -or -not (Test-Path -LiteralPath $InstallerPath)) {
    throw "NSIS installer not found. Run npm run desktop:build first or pass -InstallerPath."
}
$InstallerPath = [IO.Path]::GetFullPath($InstallerPath)

$BeforeRealData = Get-TreeManifest $RealDataRoot
$BeforeSidecarIds = @(Get-SidecarProcesses | ForEach-Object ProcessId)
New-Item -ItemType Directory -Force -Path $InstallRoot, $TestDataRoot | Out-Null

try {
    Invoke-CheckedProcess $InstallerPath @("/S", "/D=$InstallRoot")
    $Installed = $true

    $DesktopExe = Get-ChildItem -LiteralPath $InstallRoot -Recurse -Filter "*.exe" -File |
        Where-Object { $_.Name -notmatch "(?i)unins|uninstall|eai-service" } |
        Sort-Object @{ Expression = { if ($_.BaseName -eq "EAI Desktop") { 0 } else { 1 } } }, FullName |
        Select-Object -First 1 -ExpandProperty FullName
    if (-not $DesktopExe) { throw "Installed desktop executable was not found in $InstallRoot" }

    $env:EAI_DESKTOP_TEST_MODE = "1"
    $env:EAI_DESKTOP_APP_DATA_DIR = $TestDataRoot
    $AppProcess = Start-Process -FilePath $DesktopExe -PassThru

    $deadline = [DateTime]::UtcNow.AddSeconds($StartupTimeoutSeconds)
    $ReadyFile = $null
    while ([DateTime]::UtcNow -lt $deadline) {
        if ($AppProcess.HasExited) { throw "EAI Desktop exited before backend readiness" }
        $ReadyFile = Get-Item -LiteralPath (Join-Path $TestDataRoot "logs\desktop-ready.json") -ErrorAction SilentlyContinue
        if ($ReadyFile) { break }
        Start-Sleep -Milliseconds 250
    }
    if (-not $ReadyFile) { throw "Backend did not become ready within $StartupTimeoutSeconds seconds" }
    if (-not (Test-Path -LiteralPath (Join-Path $TestDataRoot "data\personal"))) {
        throw "Isolated personal data directory was not initialized"
    }

    if (-not $AppProcess.CloseMainWindow()) {
        throw "Desktop window did not accept a normal close request"
    }
    if (-not $AppProcess.WaitForExit(15000)) {
        throw "Desktop process did not exit within 15 seconds"
    }
    $AppProcess = $null

    $sidecarDeadline = [DateTime]::UtcNow.AddSeconds(10)
    do {
        $NewSidecars = @(Get-SidecarProcesses | Where-Object { $_.ProcessId -notin $BeforeSidecarIds })
        if ($NewSidecars.Count -eq 0) { break }
        Start-Sleep -Milliseconds 250
    } while ([DateTime]::UtcNow -lt $sidecarDeadline)
    if ($NewSidecars.Count -ne 0) {
        throw "Sidecar processes remained after desktop exit: $($NewSidecars.ProcessId -join ', ')"
    }

    $Uninstaller = Get-ChildItem -LiteralPath $InstallRoot -Recurse -Filter "*.exe" -File |
        Where-Object { $_.Name -match "(?i)unins|uninstall" } |
        Select-Object -First 1 -ExpandProperty FullName
    if (-not $Uninstaller) { throw "NSIS uninstaller was not found" }
    Invoke-CheckedProcess $Uninstaller @("/S")
    $Installed = $false

    $AfterRealData = Get-TreeManifest $RealDataRoot
    $BeforeJson = ConvertTo-Json @($BeforeRealData) -Depth 4 -Compress
    $AfterJson = ConvertTo-Json @($AfterRealData) -Depth 4 -Compress
    if ($BeforeJson -cne $AfterJson) {
        throw "Real AppData changed during isolated installed-app smoke: $RealDataRoot"
    }

    Write-Host "Installed-app smoke passed."
    Write-Host "Installer: $InstallerPath"
    Write-Host "Isolated AppData: $TestDataRoot"
    Write-Host "Real AppData unchanged: $RealDataRoot"
} catch {
    Write-Warning "Installed-app smoke failed: $($_.Exception.Message)"
    Write-Host "Installed files:"
    Get-ChildItem -LiteralPath $InstallRoot -Recurse -File -ErrorAction SilentlyContinue |
        Select-Object FullName, Length | Format-Table -AutoSize | Out-String | Write-Host
    Write-Host "Isolated AppData files and logs:"
    Get-ChildItem -LiteralPath $TestDataRoot -Recurse -File -ErrorAction SilentlyContinue |
        Select-Object FullName, Length | Format-Table -AutoSize | Out-String | Write-Host
    Get-ChildItem -LiteralPath (Join-Path $TestDataRoot "logs") -File -ErrorAction SilentlyContinue |
        ForEach-Object {
            Write-Host "--- $($_.FullName) ---"
            Get-Content -LiteralPath $_.FullName -Encoding UTF8 -Tail 120 -ErrorAction SilentlyContinue
        }
    throw
} finally {
    if ($AppProcess -and -not $AppProcess.HasExited) {
        Stop-Process -Id $AppProcess.Id -Force -ErrorAction SilentlyContinue
        $AppProcess.WaitForExit(5000) | Out-Null
    }
    Get-SidecarProcesses | Where-Object { $_.ProcessId -notin $BeforeSidecarIds } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    if ($Installed -and (Test-Path -LiteralPath $InstallRoot)) {
        $Uninstaller = Get-ChildItem -LiteralPath $InstallRoot -Recurse -Filter "*.exe" -File -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -match "(?i)unins|uninstall" } | Select-Object -First 1
        if ($Uninstaller) {
            Start-Process -FilePath $Uninstaller.FullName -ArgumentList "/S" -Wait -WindowStyle Hidden -ErrorAction SilentlyContinue
        }
    }
    if ($null -eq $OriginalTestMode) { Remove-Item Env:EAI_DESKTOP_TEST_MODE -ErrorAction SilentlyContinue }
    else { $env:EAI_DESKTOP_TEST_MODE = $OriginalTestMode }
    if ($null -eq $OriginalDataOverride) { Remove-Item Env:EAI_DESKTOP_APP_DATA_DIR -ErrorAction SilentlyContinue }
    else { $env:EAI_DESKTOP_APP_DATA_DIR = $OriginalDataOverride }
    Remove-SmokeRoot
}
