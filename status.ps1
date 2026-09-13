Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "portable_common.ps1")

$Root = Get-PortablePackageRoot
$Manifest = Read-PortableJson (Join-Path $Root "package_manifest.json")
$Installed = Test-Path -LiteralPath (Join-Path $Root "installed_manifest.json") -PathType Leaf
$Python = Join-Path $Root "runtime\python\python.exe"
$PythonVersion = $null
if (Test-Path -LiteralPath $Python -PathType Leaf) {
    Set-PortableEnvironment
    try { $PythonVersion = ((& $Python --version) -join " ").Trim() } catch { }
}
$Health = $null
try { $Health = Invoke-RestMethod -Uri "http://127.0.0.1:8009/api/agent/health" -TimeoutSec 3 } catch { }
$WebGLOnline = $false
try {
    $Response = Invoke-WebRequest -Uri "http://127.0.0.1:8090/ApplicationVue/dist/index_agent.html" -Method Head -UseBasicParsing -TimeoutSec 3
    $WebGLOnline = $Response.StatusCode -eq 200
} catch { }
$BackendReleaseId = if ($Health -and $Health.PSObject.Properties['release_id']) { $Health.release_id } else { $null }
$RuntimeProfile = if ($Health -and $Health.PSObject.Properties['runtime_profile']) { $Health.runtime_profile } else { "local_release" }
$RagStatus = if ($Health -and $Health.PSObject.Properties['rag']) { $Health.rag } else { $null }
$QwenStatus = if ($Health -and $Health.PSObject.Properties['qwen']) {
    $Health.qwen
} else {
    @{ configured = $false; available = $false; status = "NOT_EXPOSED_IN_CUSTOMER_MODE" }
}
$BackendReleaseConsistent = if ($Health -and $Health.PSObject.Properties['release_consistent']) {
    [bool]$Health.release_consistent
} else {
    [bool]($BackendReleaseId -and $BackendReleaseId -eq $Manifest.release_id)
}
$Result = [ordered]@{
    status = "success"
    installed = $Installed
    runtime_python = "bundled"
    python_path = "runtime/python/python.exe"
    python_version = $PythonVersion
    agent_online = [bool]$Health
    webgl_online = $WebGLOnline
    release_id = $Manifest.release_id
    backend_release_id = $BackendReleaseId
    release_consistent = [bool]($Health -and $BackendReleaseId -eq $Manifest.release_id -and $BackendReleaseConsistent)
    runtime_profile = $RuntimeProfile
    rag = $RagStatus
    qwen = $QwenStatus
    official_url = "http://127.0.0.1:8090/ApplicationVue/dist/index_agent.html#/index"
}
$Result | ConvertTo-Json -Depth 12
if (-not $Installed -or -not $Health -or -not $WebGLOnline -or -not $Result.release_consistent) { exit 1 }
exit 0
