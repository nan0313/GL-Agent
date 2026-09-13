$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$RuntimeFile = Join-Path $ProjectRoot "runtime\ev_agent_runtime.json"
$Runtime = if (Test-Path -LiteralPath $RuntimeFile) { Get-Content -LiteralPath $RuntimeFile -Raw | ConvertFrom-Json } else { $null }
$Health = $null
try { $Health = Invoke-RestMethod -Uri "http://127.0.0.1:8009/api/agent/health" -TimeoutSec 3 } catch { }
$WebGLOnline = $false
try {
    $Response = Invoke-WebRequest -Uri "http://127.0.0.1:8090/ApplicationVue/dist/index_agent.html" -Method Head -UseBasicParsing -TimeoutSec 3
    $WebGLOnline = $Response.StatusCode -eq 200
} catch { }
@{
    running = [bool]($Runtime -and (Get-Process -Id $Runtime.pid -ErrorAction SilentlyContinue))
    pid = if ($Runtime) { $Runtime.pid } else { $null }
    agent_online = [bool]$Health
    webgl_online = $WebGLOnline
    release_id = if ($Health) { $Health.release_id } else { $null }
    release_consistent = if ($Health) { $Health.release_consistent } else { $false }
    runtime_profile = if ($Health) { $Health.runtime_profile } else { $null }
    rag = if ($Health) { $Health.rag } else { $null }
    qwen = if ($Health) { $Health.qwen } else { $null }
    official_url = "http://127.0.0.1:8090/ApplicationVue/dist/index_agent.html#/index"
} | ConvertTo-Json -Depth 8
