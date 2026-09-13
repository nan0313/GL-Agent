param([switch]$LocalAdminMode)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "portable_common.ps1")

try {
    $Root = Get-PortablePackageRoot
    if (-not (Test-Path -LiteralPath (Join-Path $Root "installed_manifest.json") -PathType Leaf)) {
        throw "PORTABLE_NOT_INSTALLED"
    }
    $Python = Get-BundledPython
    Set-PortableEnvironment -CreateDirectories -LocalAdminMode:$LocalAdminMode
    try {
        $ExistingHealth = Invoke-RestMethod -Uri "http://127.0.0.1:8009/api/agent/health" -TimeoutSec 2
        if ($ExistingHealth.status -eq "ok") {
            $Manifest = Read-PortableJson (Join-Path $Root "package_manifest.json")
            if ($ExistingHealth.release_id -eq $Manifest.release_id) {
                Write-Output "PORTABLE_ALREADY_RUNNING release_id=$($ExistingHealth.release_id)"
                Write-Output "http://127.0.0.1:8090/ApplicationVue/dist/index_agent.html#/index"
                exit 0
            }
            throw "PORT_8009_RELEASE_MISMATCH"
        }
    } catch {
        if ($_.Exception.Message -eq "PORT_8009_RELEASE_MISMATCH") { throw }
    }
    foreach ($Port in @(8009, 8090)) {
        if (Test-LocalPortListening -Port $Port) { throw "PORT_${Port}_OCCUPIED" }
    }
    $Runner = Join-Path $Root "scripts\local_release_runner.py"
    $WebGLRoot = Join-Path $Root "vendor\webgl"
    $ArgumentList = @(
        ('"{0}"' -f $Runner)
        "--webgl-root"
        ('"{0}"' -f $WebGLRoot)
    )
    $LauncherStdout = Join-Path $Root "runtime\logs\launcher.stdout.log"
    $LauncherStderr = Join-Path $Root "runtime\logs\launcher.stderr.log"
    $Process = Start-Process -FilePath $Python -ArgumentList $ArgumentList -WorkingDirectory $Root -WindowStyle Hidden -PassThru -RedirectStandardOutput $LauncherStdout -RedirectStandardError $LauncherStderr
    @{
        launcher_pid = $Process.Id
        python_relative_path = "runtime/python/python.exe"
        started_at = [DateTime]::UtcNow.ToString("o")
    } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $Root "runtime\ev_agent_launcher.json") -Encoding UTF8
    $Health = $null
    for ($Attempt = 0; $Attempt -lt 120; $Attempt++) {
        Start-Sleep -Milliseconds 500
        if ($Process.HasExited) { throw "PORTABLE_LAUNCHER_EXITED:$($Process.ExitCode)" }
        try {
            $Health = Invoke-RestMethod -Uri "http://127.0.0.1:8009/api/agent/health" -TimeoutSec 2
            if ($Health.status -eq "ok") { break }
        } catch { }
    }
    if (-not $Health -or $Health.status -ne "ok") { throw "PORTABLE_AGENT_HEALTH_TIMEOUT" }
    $Page = Invoke-WebRequest -Uri "http://127.0.0.1:8090/ApplicationVue/dist/index_agent.html" -UseBasicParsing -TimeoutSec 10
    if ($Page.StatusCode -ne 200) { throw "PORTABLE_WEBGL_ENTRY_UNAVAILABLE" }
    Write-Output "PORTABLE_STARTED release_id=$($Health.release_id)"
    Write-Output "http://127.0.0.1:8090/ApplicationVue/dist/index_agent.html#/index"
    exit 0
} catch {
    Write-Error $_.Exception.Message
    exit 2
}
