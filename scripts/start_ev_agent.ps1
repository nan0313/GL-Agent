param(
    [string]$WebGLRoot = "",
    [string]$CondaEnvironment = "py311",
    [string]$PythonExecutable = "",
    [switch]$LocalAdminMode
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
if (-not $WebGLRoot) { $WebGLRoot = Join-Path (Split-Path -Parent $ProjectRoot) "EV-Globe-WebGL" }
$WebGLRoot = (Resolve-Path -LiteralPath $WebGLRoot).Path
$Descriptor = Join-Path $ProjectRoot "release\local_release.json"
$Manifest = Join-Path $WebGLRoot "release\webgl_runtime_manifest.json"
$Entry = Join-Path $WebGLRoot "ApplicationVue\dist\index_agent.html"
foreach ($Path in @($Descriptor, $Manifest, $Entry)) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "Required release file is missing: $Path" }
}

$RuntimeFile = Join-Path $ProjectRoot "runtime\ev_agent_runtime.json"
if (Test-Path -LiteralPath $RuntimeFile) {
    $Existing = Get-Content -LiteralPath $RuntimeFile -Raw | ConvertFrom-Json
    if (Get-Process -Id $Existing.pid -ErrorAction SilentlyContinue) {
        $Health = Invoke-RestMethod -Uri "http://127.0.0.1:8009/api/agent/health" -TimeoutSec 3
        Write-Output "WebGL Agent is already running. release_id=$($Health.release_id)"
        Write-Output "http://127.0.0.1:8090/ApplicationVue/dist/index_agent.html#/index"
        exit 0
    }
}
foreach ($Port in @(8009, 8090)) {
    if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
        throw "Port $Port is already occupied by an untracked process."
    }
}

if ($PythonExecutable) {
    $PythonExecutable = (Resolve-Path -LiteralPath $PythonExecutable).Path
} else {
    $Conda = (Get-Command conda -ErrorAction Stop).Source
    $CondaEnvironments = ((& $Conda env list --json) -join "`n" | ConvertFrom-Json).envs
    $EnvironmentRoot = $CondaEnvironments |
        Where-Object { (Split-Path -Leaf $_) -eq $CondaEnvironment } |
        Select-Object -First 1
    if (-not $EnvironmentRoot) { throw "Conda environment not found: $CondaEnvironment" }
    $PythonExecutable = Join-Path $EnvironmentRoot "python.exe"
}
if (-not (Test-Path -LiteralPath $PythonExecutable -PathType Leaf)) {
    throw "Python executable is unavailable: $PythonExecutable"
}
$env:EV_AGENT_RUNTIME_PROFILE = "local_release"
$env:EV_AGENT_MODE = if ($LocalAdminMode) { "developer" } else { "customer" }
$env:EV_AGENT_DEBUG_ROUTES = if ($LocalAdminMode) { "true" } else { "false" }
$env:EV_AGENT_LOCAL_ADMIN_MODE = if ($LocalAdminMode) { "true" } else { "false" }
$MutableKnowledgeRoot = Join-Path $ProjectRoot "runtime\knowledge_global"
$StaticKnowledgeRoot = Join-Path $ProjectRoot "docs\knowledge"
$env:RAG_KNOWLEDGE_ROOTS = $MutableKnowledgeRoot + [System.IO.Path]::PathSeparator + $StaticKnowledgeRoot
$env:RAG_DATABASE_PATH = Join-Path $ProjectRoot "runtime\rag\rag.sqlite3"
$env:RAG_INDEX_PATH = Join-Path $ProjectRoot "runtime\rag\indexes"
$env:RAG_BACKUP_PATH = Join-Path $ProjectRoot "runtime\rag\backups"
$env:CONVERSATION_SQLITE_PATH = Join-Path $ProjectRoot "runtime\data\conversations.sqlite3"
$env:CONVERSATION_RUNTIME_ROOT = Join-Path $ProjectRoot "runtime\conversations"
$Runner = Join-Path $ProjectRoot "scripts\local_release_runner.py"
$Arguments = @(
    ('"{0}"' -f $Runner), "--webgl-root", ('"{0}"' -f $WebGLRoot)
)
$Process = Start-Process -FilePath $PythonExecutable -ArgumentList $Arguments -WorkingDirectory $ProjectRoot -WindowStyle Hidden -PassThru
New-Item -ItemType Directory -Force -Path (Join-Path $ProjectRoot "runtime") | Out-Null
@{ launcher_pid = $Process.Id; started_at = [DateTime]::UtcNow.ToString("o") } |
    ConvertTo-Json | Set-Content -LiteralPath (Join-Path $ProjectRoot "runtime\ev_agent_launcher.json") -Encoding UTF8

$Health = $null
for ($Attempt = 0; $Attempt -lt 60; $Attempt++) {
    Start-Sleep -Milliseconds 500
    if ($Process.HasExited) {
        throw "WebGL Agent launcher exited before health check completed. exit_code=$($Process.ExitCode)"
    }
    try {
        $Health = Invoke-RestMethod -Uri "http://127.0.0.1:8009/api/agent/health" -TimeoutSec 2
        if ($Health.status -eq "ok") { break }
    } catch { }
}
if (-not $Health -or $Health.status -ne "ok") {
    Stop-Process -Id $Process.Id -Force -ErrorAction SilentlyContinue
    throw "WebGL Agent did not become healthy. See logs/local_release.log."
}
$Page = Invoke-WebRequest -Uri "http://127.0.0.1:8090/ApplicationVue/dist/index_agent.html" -UseBasicParsing -TimeoutSec 5
if ($Page.StatusCode -ne 200) { throw "Official WebGL entry is unavailable." }
Write-Output "WebGL Agent started. release_id=$($Health.release_id)"
Write-Output "http://127.0.0.1:8090/ApplicationVue/dist/index_agent.html#/index"
