param([string]$OutputPath)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "portable_common.ps1")

try {
    $Root = Get-PortablePackageRoot
    $PowerShellHost = Get-PortablePowerShellHost
    & $PowerShellHost -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Root "verify_package.ps1") | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "STATIC_PACKAGE_VERIFICATION_FAILED" }
    $Python = Get-BundledPython
    Set-PortableEnvironment -CreateDirectories
    $Probe = Join-Path $Root "tools\portable_probe.py"
    $VerificationRoot = Join-Path $Root "runtime\verification"
    & $Python $Probe python-runtime --package-root $Root --output (Join-Path $VerificationRoot "python_runtime.json") | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "BUNDLED_PYTHON_VALIDATION_FAILED" }
    & $Python $Probe model --package-root $Root --output (Join-Path $VerificationRoot "model.json") | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "BGE_OFFLINE_VALIDATION_FAILED" }
    & $Python $Probe rag-status --package-root $Root --output (Join-Path $VerificationRoot "rag_status.json") | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "RAG_INSTALLATION_INVALID" }
    & $Python $Probe admin-region --package-root $Root --output (Join-Path $VerificationRoot "admin_region.json") | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "ADMIN_REGION_DATA_INVALID" }
    if (-not (Test-Path -LiteralPath (Join-Path $Root "installed_manifest.json") -PathType Leaf)) {
        throw "INSTALLED_MANIFEST_MISSING"
    }
    $Result = @{
        status = "success"
        runtime_python = "runtime/python/python.exe"
        bge = "models/bge-small-zh-v1.5"
        rag_backend = "hnsw"
        admin_region_data = "package_local"
    }
    $Json = $Result | ConvertTo-Json -Depth 8
    if ($OutputPath) {
        $Parent = Split-Path -Parent $OutputPath
        if ($Parent) { New-Item -ItemType Directory -Force -Path $Parent | Out-Null }
        $Json | Set-Content -LiteralPath $OutputPath -Encoding UTF8
    }
    Write-Output $Json
    exit 0
} catch {
    $Result = @{ status = "failed"; error_code = $_.Exception.Message }
    $Json = $Result | ConvertTo-Json
    if ($OutputPath) { $Json | Set-Content -LiteralPath $OutputPath -Encoding UTF8 }
    Write-Output $Json
    exit 2
}
