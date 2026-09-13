$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$RuntimeFile = Join-Path $ProjectRoot "runtime\ev_agent_runtime.json"
$LauncherFile = Join-Path $ProjectRoot "runtime\ev_agent_launcher.json"
$Stopped = @()
foreach ($File in @($RuntimeFile, $LauncherFile)) {
    if (-not (Test-Path -LiteralPath $File)) { continue }
    $Record = Get-Content -LiteralPath $File -Raw | ConvertFrom-Json
    $PidValue = if ($Record.pid) { [int]$Record.pid } else { [int]$Record.launcher_pid }
    if ($PidValue -gt 0 -and (Get-Process -Id $PidValue -ErrorAction SilentlyContinue)) {
        Stop-Process -Id $PidValue -Force
        $Stopped += $PidValue
    }
}
Start-Sleep -Milliseconds 500
Write-Output ("Stopped recorded project processes: " + (($Stopped | Select-Object -Unique) -join ", "))
