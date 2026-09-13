Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$script:PortablePackageRoot = $PSScriptRoot

function Get-PortablePackageRoot {
    $Root = [System.IO.Path]::GetFullPath($script:PortablePackageRoot)
    if (-not (Test-Path -LiteralPath (Join-Path $Root "package_manifest.json") -PathType Leaf)) {
        throw "PORTABLE_PACKAGE_MANIFEST_MISSING"
    }
    return $Root
}

function Get-BundledPython {
    $Root = Get-PortablePackageRoot
    $Python = Join-Path $Root "runtime\python\python.exe"
    if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
        throw "BUNDLED_PYTHON_NOT_INSTALLED"
    }
    return $Python
}

function Get-PortablePowerShellHost {
    foreach ($Name in @("powershell.exe", "pwsh.exe")) {
        $Candidate = Join-Path $PSHOME $Name
        if (Test-Path -LiteralPath $Candidate -PathType Leaf) { return $Candidate }
    }
    throw "POWERSHELL_HOST_NOT_FOUND"
}

function Set-PortableEnvironment {
    param([switch]$CreateDirectories, [switch]$LocalAdminMode)

    $Root = Get-PortablePackageRoot
    $RuntimeRoot = Join-Path $Root "runtime"
    $PythonRoot = Join-Path $RuntimeRoot "python"
    $ModelRoot = Join-Path $Root "models\bge-small-zh-v1.5"
    $KnowledgeRoot = Join-Path $Root "docs\knowledge"
    $RagRoot = Join-Path $RuntimeRoot "rag"
    $MutableKnowledgeRoot = Join-Path $RuntimeRoot "knowledge_global"
    $ConversationRoot = Join-Path $RuntimeRoot "conversations"
    $CacheRoot = Join-Path $RuntimeRoot "cache"
    if ($CreateDirectories) {
        @(
            $RuntimeRoot,
            (Join-Path $RuntimeRoot "logs"),
            (Join-Path $RuntimeRoot "pids"),
            (Join-Path $RuntimeRoot "verification"),
            (Join-Path $RuntimeRoot "data"),
            $RagRoot,
            (Join-Path $RagRoot "indexes"),
            (Join-Path $RagRoot "backups"),
            (Join-Path $MutableKnowledgeRoot "active"),
            (Join-Path $MutableKnowledgeRoot "staging"),
            (Join-Path $MutableKnowledgeRoot "rejected"),
            $ConversationRoot,
            $CacheRoot,
            (Join-Path $CacheRoot "huggingface")
        ) | ForEach-Object { New-Item -ItemType Directory -Force -Path $_ | Out-Null }
    }

    $env:EV_AGENT_RUNTIME_PROFILE = "local_release"
    $env:EV_AGENT_MODE = if ($LocalAdminMode) { "developer" } else { "customer" }
    $env:EV_AGENT_DEBUG_ROUTES = if ($LocalAdminMode) { "true" } else { "false" }
    $env:EV_AGENT_LOCAL_ADMIN_MODE = if ($LocalAdminMode) { "true" } else { "false" }
    $env:CORS_ALLOW_ORIGINS = "http://127.0.0.1:8090,http://localhost:8090"
    $env:PYTHONNOUSERSITE = "1"
    $env:PYTHONDONTWRITEBYTECODE = "1"
    $env:PYTHONPATH = $Root
    $env:HF_HUB_OFFLINE = "1"
    $env:TRANSFORMERS_OFFLINE = "1"
    $env:HF_HOME = Join-Path $CacheRoot "huggingface"
    $env:HUGGINGFACE_HUB_CACHE = Join-Path $CacheRoot "huggingface\hub"
    $env:TRANSFORMERS_CACHE = Join-Path $CacheRoot "huggingface\transformers"
    $env:TOKENIZERS_PARALLELISM = "false"
    $env:RAG_ENABLED = "true"
    $env:RAG_KNOWLEDGE_ROOTS = $MutableKnowledgeRoot + [System.IO.Path]::PathSeparator + $KnowledgeRoot
    $env:RAG_DATABASE_PATH = Join-Path $RagRoot "rag.sqlite3"
    $env:RAG_INDEX_PATH = Join-Path $RagRoot "indexes"
    $env:RAG_BACKUP_PATH = Join-Path $RagRoot "backups"
    $env:RAG_LEXICAL_ENABLED = "true"
    $env:RAG_DENSE_ENABLED = "true"
    $env:RAG_EMBEDDING_PROVIDER = "sentence_transformers"
    $env:RAG_EMBEDDING_MODEL = $ModelRoot
    $env:RAG_LOCAL_MODEL_PATH = $ModelRoot
    $env:RAG_EMBEDDING_DEVICE = "cpu"
    $env:RAG_VECTOR_BACKEND = "hnsw"
    $env:RAG_HNSW_SPACE = "cosine"
    $env:RAG_HNSW_M = "16"
    $env:RAG_HNSW_EF_CONSTRUCTION = "200"
    $env:RAG_HNSW_EF_SEARCH = "64"
    $env:RAG_HYBRID_ENABLED = "true"
    $env:RAG_RERANK_ENABLED = "true"
    $env:RAG_RERANK_PROVIDER = "lightweight"
    $env:TRACE_STORE_PROVIDER = "sqlite"
    $env:TRACE_SQLITE_PATH = Join-Path $RuntimeRoot "data\traces.sqlite3"
    $env:CONVERSATION_SQLITE_PATH = Join-Path $RuntimeRoot "data\conversations.sqlite3"
    $env:CONVERSATION_RUNTIME_ROOT = $ConversationRoot
    $env:EV_AGENT_LOG_DIR = Join-Path $RuntimeRoot "logs"
    $env:ADMIN_REGION_SHP_PATH = Join-Path $Root "vendor\webgl\ApplicationVue\dist\datas\BOUNT_poly.shp"
    if (-not (Test-Path -LiteralPath (Join-Path $Root "config\local_llm_config.yaml") -PathType Leaf)) {
        $env:QWEN_ENABLED = "false"
        $env:LLM_PLANNER_PROVIDER = "lcel_mock"
    }
    if (Test-Path -LiteralPath $PythonRoot -PathType Container) {
        $WindowsRoot = $env:SystemRoot
        $env:PATH = @(
            $PythonRoot,
            (Join-Path $PythonRoot "Scripts"),
            (Join-Path $PythonRoot "Library\bin"),
            (Join-Path $WindowsRoot "System32"),
            $WindowsRoot
        ) -join ";"
    }
}

function Read-PortableJson {
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "PORTABLE_JSON_MISSING:$([System.IO.Path]::GetFileName($Path))"
    }
    return Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json
}

function Test-LocalPortListening {
    param([Parameter(Mandatory = $true)][int]$Port)
    return [bool](Get-NetTCPConnection -LocalAddress "127.0.0.1" -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
}
