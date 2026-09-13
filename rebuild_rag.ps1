Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "portable_common.ps1")

try {
    $Root = Get-PortablePackageRoot
    $Python = Get-BundledPython
    Set-PortableEnvironment -CreateDirectories
    if (Test-LocalPortListening -Port 8009) { throw "STOP_AGENT_BEFORE_RAG_REBUILD" }
    $Tool = Join-Path $Root "tools\manage_rag_knowledge.py"
    $VerificationRoot = Join-Path $Root "runtime\verification"
    $RebuildRaw = (& $Python $Tool rebuild --mode full) -join "`n"
    if ($LASTEXITCODE -ne 0) { throw "RAG_FULL_REBUILD_COMMAND_FAILED" }
    $Rebuild = $RebuildRaw | ConvertFrom-Json
    if ($Rebuild.status -ne "success") { throw "RAG_FULL_REBUILD_NOT_SUCCESS" }
    $ValidateRaw = (& $Python $Tool validate-vector) -join "`n"
    if ($LASTEXITCODE -ne 0) { throw "RAG_VECTOR_VALIDATION_COMMAND_FAILED" }
    $Validate = $ValidateRaw | ConvertFrom-Json
    if ($Validate.status -ne "success" -or -not $Validate.vector_valid) { throw "RAG_VECTOR_INVALID" }
    $StatusRaw = (& $Python $Tool vector-status) -join "`n"
    if ($LASTEXITCODE -ne 0) { throw "RAG_VECTOR_STATUS_COMMAND_FAILED" }
    $Status = $StatusRaw | ConvertFrom-Json
    $Knowledge = Read-PortableJson (Join-Path $Root "knowledge_manifest.json")
    if ([int]$Status.source_count -ne [int]$Knowledge.source_count) { throw "RAG_SOURCE_COUNT_MISMATCH" }
    if ([int]$Status.document_count -le 0 -or [int]$Status.chunk_count -le 0) { throw "RAG_INDEX_EMPTY" }
    if ($null -eq $Validate.vector_details) { throw "RAG_VECTOR_VALIDATION_DETAILS_MISSING" }
    if ([int]$Validate.vector_details.item_count -ne [int]$Status.chunk_count) { throw "RAG_EMBEDDING_COUNT_MISMATCH" }
    if ([string]$Validate.vector_details.version -ne [string]$Status.active_index_version) { throw "RAG_VECTOR_VERSION_MISMATCH" }
    if (-not $Status.vector_valid -or $Status.vector_validation_status -ne "success") { throw "RAG_VECTOR_STATUS_INVALID" }
    if ([int]$Status.vector_item_count -ne [int]$Status.chunk_count) { throw "RAG_VECTOR_STATUS_ITEM_COUNT_MISMATCH" }
    if ([string]$Status.vector_index_version -ne [string]$Status.active_index_version) { throw "RAG_VECTOR_STATUS_VERSION_MISMATCH" }
    if ($Status.active_vector_backend -ne "hnsw") { throw "RAG_HNSW_NOT_ACTIVE" }
    if ($Status.vector_fallback_used) { throw "RAG_VECTOR_FALLBACK_USED" }
    if ([int]$Status.embedding_dimension -ne 512) { throw "RAG_EMBEDDING_DIMENSION_INVALID" }
    @{
        status = "success"
        rebuild = $Rebuild
        validation = $Validate
        vector_status = $Status
    } | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath (Join-Path $VerificationRoot "rag_rebuild.json") -Encoding UTF8
    Write-Output $StatusRaw
    exit 0
} catch {
    Write-Error $_.Exception.Message
    exit 2
}
