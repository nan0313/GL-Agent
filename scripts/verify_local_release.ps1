param(
    [string]$CondaEnvironment = "py311",
    [switch]$SkipAutomatedTests,
    [switch]$ReuseNodeEvidence
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$ReportPath = Join-Path $ProjectRoot "release\verification_report.json"
$PreviousReport = if (Test-Path -LiteralPath $ReportPath) {
    Get-Content -LiteralPath $ReportPath -Raw | ConvertFrom-Json
} else { $null }
$StartedAt = [DateTime]::UtcNow
$Checks = [ordered]@{}

function Add-Check([string]$Name, [bool]$Passed, [object]$Detail) {
    $Checks[$Name] = [ordered]@{ passed = $Passed; detail = $Detail }
}

Push-Location $ProjectRoot
try {
    $GitCommand = Get-Command git -ErrorAction SilentlyContinue
    $GitRepository = $null -ne $GitCommand -and (Test-Path -LiteralPath (Join-Path $ProjectRoot ".git"))
    if ($GitRepository) {
        $ScopedStatus = @(git status --porcelain -- app frontend config scripts release tests)
        Add-Check "git_release_scope_clean" ($ScopedStatus.Count -eq 0) $ScopedStatus
    } else {
        Add-Check "git_release_scope_clean" $true @("NOT_A_GIT_CHECKOUT")
    }

    $RequiredFiles = @(
        "release\local_release.json",
        "scripts\start_ev_agent.ps1",
        "scripts\stop_ev_agent.ps1",
        "scripts\status_ev_agent.ps1",
        "scripts\local_release_runner.py"
    )
    $Missing = @($RequiredFiles | Where-Object { -not (Test-Path -LiteralPath (Join-Path $ProjectRoot $_) -PathType Leaf) })
    Add-Check "required_agent_files" ($Missing.Count -eq 0) $Missing

    $Health = Invoke-RestMethod -Uri "http://127.0.0.1:8009/api/agent/health" -TimeoutSec 5
    Add-Check "agent_health" ($Health.status -eq "ok") @{ release_id = $Health.release_id; profile = $Health.runtime_profile }
    $Descriptor = Get-Content -LiteralPath (Join-Path $ProjectRoot "release\local_release.json") -Raw | ConvertFrom-Json
    Add-Check "release_consistency" ($Health.release_id -eq $Descriptor.release_id) @{ release_id = $Health.release_id; descriptor_release_id = $Descriptor.release_id }

    $WebGL = Invoke-WebRequest -Uri "http://127.0.0.1:8090/ApplicationVue/dist/index_agent.html" -UseBasicParsing -TimeoutSec 5
    Add-Check "official_webgl_entry" ($WebGL.StatusCode -eq 200 -and $WebGL.Content.Contains($Health.release_id)) @{ status = $WebGL.StatusCode }

    $OpenApi = $null
    try { $OpenApi = Invoke-RestMethod -Uri "http://127.0.0.1:8009/openapi.json" -TimeoutSec 5 } catch { }
    if ($Health.agent_mode -eq "customer") {
        Add-Check "api_schema" ($null -eq $OpenApi) @{ mode = "customer"; openapi_hidden = ($null -eq $OpenApi) }
    } else {
        $Paths = @($OpenApi.paths.PSObject.Properties.Name)
        $SchemaOk = $Paths -contains "/agent/chat" -and
            $Paths -contains "/api/agent/chat/stream" -and
            $Paths -contains "/health/rag" -and
            $Paths -contains "/api/conversations" -and
            $Paths -contains "/api/conversations/{conversation_id}/attachments" -and
            $Paths -contains "/api/rag/sources/upload"
        Add-Check "api_schema" $SchemaOk @{ path_count = $Paths.Count; mode = "developer" }
    }

    $Payload = @{
        session_id = "release-verification"
        user_id = "local-release"
        role = if ($Health.agent_mode -eq "customer") { "user" } else { "developer" }
        query = "飞到黑龙江省"
        gis_context = @{}
    } | ConvertTo-Json -Depth 5
    $Sse = Invoke-WebRequest -Uri "http://127.0.0.1:8009/api/agent/chat/stream" -Method Post -ContentType "application/json" -Headers @{ Accept = "text/event-stream" } -Body $Payload -UseBasicParsing -TimeoutSec 30
    $SseDone = if ($Health.agent_mode -eq "customer") {
        $Sse.StatusCode -eq 200 -and $Sse.Content -match "event:\s*answer_completed" -and $Sse.Content -match "event:\s*final_result" -and $Sse.Content -notmatch "tool_started|tool_result|planner|retrieval|trace_id"
    } else {
        $Sse.StatusCode -eq 200 -and $Sse.Content -match "event:\s*done"
    }
    Add-Check "sse_terminal" $SseDone @{ status = $Sse.StatusCode; customer_contract = ($Health.agent_mode -eq "customer") }

    if ($Health.agent_mode -eq "customer") {
        Add-Check "rag_health" $true @{ status = "internal_rag_health_hidden_from_customer" }
        Add-Check "rag_index_validate" $true @{ status = "admin_route_hidden_from_customer" }
        Add-Check "qwen_status_snapshot" $true @{ status = "internal_model_status_not_public" }
    } else {
        $RagHealth = Invoke-RestMethod -Uri "http://127.0.0.1:8009/health/rag" -TimeoutSec 10
        if (-not $Health.capabilities.local_admin_mode) {
            throw "Release verification requires scripts/start_ev_agent.ps1 -LocalAdminMode"
        }
        $RagValidate = Invoke-RestMethod -Uri "http://127.0.0.1:8009/rag/index/validate" -Method Post -ContentType "application/json" -Body '{"role":"admin","mode":"incremental"}' -TimeoutSec 20
        Add-Check "rag_health" ([bool]$RagHealth.lexical_available) @{
            source_count = $RagHealth.source_count
            document_count = $RagHealth.document_count
            chunk_count = $RagHealth.chunk_count
            dense_available = $RagHealth.dense_available
            active_vector_backend = $RagHealth.active_vector_backend
            model_status = $Health.rag.model_status
        }
        Add-Check "rag_index_validate" ($RagValidate.status -eq "success") @{
            vector_valid = $RagValidate.vector_valid
            error_code = $RagValidate.error_code
        }
        Add-Check "qwen_status_snapshot" ($Health.qwen.network_call -eq $false) @{
            provider_status = $Health.qwen.provider_status
            error_code = $Health.qwen.error_code
            circuit_state = $Health.qwen.circuit_state
        }
    }

    if ($GitRepository) {
        $TrackedRuntime = @(git ls-files -- data runtime logs "*.sqlite*" "*.hnsw" "*.log")
        Add-Check "runtime_artifacts_untracked" ($TrackedRuntime.Count -eq 0) $TrackedRuntime
        $SecretHits = @(git grep -n -I -E "(sk-[A-Za-z0-9_-]{12,}|Bearer[[:space:]]+[A-Za-z0-9._~-]{12,})" -- app frontend scripts release 2>$null)
        Add-Check "tracked_secret_scan" ($SecretHits.Count -eq 0) @($SecretHits | Select-Object -First 10)
    } else {
        Add-Check "runtime_artifacts_untracked" $true @("NOT_A_GIT_CHECKOUT")
        Add-Check "tracked_secret_scan" $true @("NOT_A_GIT_CHECKOUT")
    }

    if (-not $SkipAutomatedTests) {
        $env:PYTHONIOENCODING = "utf-8"
        $env:CONDA_REPORT_ERRORS = "false"
        & conda run -n $CondaEnvironment python -m pytest `
            tests/test_release_profile.py tests/test_qwen_user_fallback.py `
            tests/test_agent_api.py tests/agent_sse_backend_test.py `
            tests/test_dialogue_orchestration.py tests/test_deterministic_webgl_anchor_skill.py `
            tests/test_rag_hnsw_backend.py tests/test_rag_knowledge_management.py tests/test_rag_backup.py -q
        Add-Check "targeted_pytest" ($LASTEXITCODE -eq 0) @{ exit_code = $LASTEXITCODE }

        if ($ReuseNodeEvidence) {
            $PreviousNode = $PreviousReport.checks.node_tests
            if (-not $PreviousNode -or -not $PreviousNode.passed) { throw "No passing Node evidence is available to reuse." }
            Add-Check "node_tests" $true $PreviousNode.detail
        } else {
            $NodeResults = @()
            foreach ($Test in Get-ChildItem -LiteralPath (Join-Path $ProjectRoot "tests") -Filter "*_test.js" -File | Sort-Object Name) {
                & node $Test.FullName
                $NodeResults += @{ test = $Test.Name; exit_code = $LASTEXITCODE }
                if ($LASTEXITCODE -ne 0) { throw "Node test failed: $($Test.Name)" }
            }
            Add-Check "node_tests" $true @{ count = $NodeResults.Count; results = $NodeResults }
        }
    } else {
        Add-Check "targeted_pytest" $true @{ status = "SKIPPED_BY_OPERATOR" }
        Add-Check "node_tests" $true @{ status = "SKIPPED_BY_OPERATOR" }
    }

    Add-Check "browser_regression" $false @{
        status = "MANUAL_BROWSER_REQUIRED"
        checklist = @("GIS", "multi_turn", "RAG", "management", "console")
    }
} finally {
    Pop-Location
}

$FailedAutomated = @($Checks.GetEnumerator() | Where-Object { -not $_.Value.passed -and $_.Key -ne "browser_regression" })
$Report = [ordered]@{
    schema_version = "1.0"
    generated_at = [DateTime]::UtcNow.ToString("o")
    duration_ms = [int]([DateTime]::UtcNow - $StartedAt).TotalMilliseconds
    release_id = if ($Health) { $Health.release_id } else { $null }
    automated_status = if ($FailedAutomated.Count -eq 0) { "PASSED" } else { "FAILED" }
    checks = $Checks
}
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $ReportPath) | Out-Null
$Report | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $ReportPath -Encoding UTF8
$Report | ConvertTo-Json -Depth 6
if ($FailedAutomated.Count -gt 0) { exit 1 }
