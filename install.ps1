param([switch]$SkipRagRebuild)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "portable_common.ps1")

try {
    $Root = Get-PortablePackageRoot
    $Manifest = Read-PortableJson (Join-Path $Root "package_manifest.json")
    if ($Manifest.package_type -ne "PORTABLE_FULL_PROJECT") { throw "PORTABLE_PACKAGE_TYPE_INVALID" }
    if (-not [Environment]::Is64BitOperatingSystem) { throw "WINDOWS_X64_REQUIRED" }
    $Tar = Join-Path $env:SystemRoot "System32\tar.exe"
    if (-not (Test-Path -LiteralPath $Tar -PathType Leaf)) { throw "REQUIRED_WINDOWS_TAR_NOT_FOUND" }

    $PowerShellHost = Get-PortablePowerShellHost
    & $PowerShellHost -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Root "verify_package.ps1") | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "STATIC_PACKAGE_VERIFICATION_FAILED" }

    $Archive = Join-Path $Root "payload\python\py311-win64.tar.gz"
    $RuntimeRoot = Join-Path $Root "runtime"
    $PythonRoot = Join-Path $RuntimeRoot "python"
    $RequiredBytes = [int64]$Manifest.python_archive_unpacked_size + 536870912
    $Drive = [System.IO.DriveInfo]::new([System.IO.Path]::GetPathRoot($Root))
    if ($Drive.AvailableFreeSpace -lt $RequiredBytes) { throw "INSUFFICIENT_INSTALL_DISK_SPACE" }

    New-Item -ItemType Directory -Force -Path $RuntimeRoot | Out-Null
    if (-not (Test-Path -LiteralPath (Join-Path $PythonRoot "python.exe") -PathType Leaf)) {
        if (Test-Path -LiteralPath $PythonRoot) {
            $Existing = @(Get-ChildItem -LiteralPath $PythonRoot -Force -ErrorAction SilentlyContinue)
            if ($Existing.Count -gt 0) { throw "BUNDLED_PYTHON_TARGET_NOT_EMPTY" }
        } else {
            New-Item -ItemType Directory -Path $PythonRoot | Out-Null
        }
        & $Tar -xzf $Archive -C $PythonRoot
        if ($LASTEXITCODE -ne 0) { throw "BUNDLED_PYTHON_EXTRACTION_FAILED" }
    }
    $Python = Join-Path $PythonRoot "python.exe"
    $CondaUnpack = Join-Path $PythonRoot "Scripts\conda-unpack.exe"
    if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { throw "BUNDLED_PYTHON_EXECUTABLE_MISSING" }
    if (-not (Test-Path -LiteralPath $CondaUnpack -PathType Leaf)) { throw "CONDA_UNPACK_MISSING" }
    & $CondaUnpack
    if ($LASTEXITCODE -ne 0) { throw "CONDA_UNPACK_FAILED" }

    Set-PortableEnvironment -CreateDirectories
    $Probe = Join-Path $Root "tools\portable_probe.py"
    $VerificationRoot = Join-Path $RuntimeRoot "verification"
    & $Python $Probe python-runtime --package-root $Root --output (Join-Path $VerificationRoot "python_runtime.json")
    if ($LASTEXITCODE -ne 0) { throw "BUNDLED_PYTHON_VALIDATION_FAILED" }
    & $Python $Probe model --package-root $Root --output (Join-Path $VerificationRoot "model.json")
    if ($LASTEXITCODE -ne 0) { throw "BGE_OFFLINE_VALIDATION_FAILED" }

    if (-not $SkipRagRebuild) {
        & $PowerShellHost -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Root "rebuild_rag.ps1")
        if ($LASTEXITCODE -ne 0) { throw "RAG_REBUILD_FAILED" }
    }
    & $Python $Probe installed-manifest --package-root $Root --output (Join-Path $Root "installed_manifest.json")
    if ($LASTEXITCODE -ne 0) { throw "INSTALLED_MANIFEST_FAILED" }
    & $PowerShellHost -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Root "verify_installation.ps1") | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "INSTALLATION_VERIFICATION_FAILED" }
    Write-Output "FIRST_RUN_READY"
    exit 0
} catch {
    Write-Error $_.Exception.Message
    exit 2
}
