Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "portable_common.ps1")

try {
    $Root = Get-PortablePackageRoot
    $ExpectedPython = Join-Path $Root "runtime\python\python.exe"
    $PidFiles = @(
        (Join-Path $Root "runtime\ev_agent_runtime.json"),
        (Join-Path $Root "runtime\ev_agent_launcher.json")
    )
    $Stopped = @()
    foreach ($File in $PidFiles) {
        if (-not (Test-Path -LiteralPath $File -PathType Leaf)) { continue }
        $Record = Read-PortableJson $File
        $PidProperty = $Record.PSObject.Properties['pid']
        $LauncherPidProperty = $Record.PSObject.Properties['launcher_pid']
        $PidValue = if ($null -ne $PidProperty -and $PidProperty.Value) {
            [int]$PidProperty.Value
        } elseif ($null -ne $LauncherPidProperty -and $LauncherPidProperty.Value) {
            [int]$LauncherPidProperty.Value
        } else {
            0
        }
        if ($PidValue -le 0 -or $Stopped -contains $PidValue) { continue }
        $Process = Get-CimInstance Win32_Process -Filter "ProcessId=$PidValue" -ErrorAction SilentlyContinue
        if ($Process) {
            $ActualExecutable = [string]$Process.ExecutablePath
            if ($ActualExecutable -and -not $ActualExecutable.Equals($ExpectedPython, [StringComparison]::OrdinalIgnoreCase)) {
                throw "RECORDED_PID_EXECUTABLE_MISMATCH:$PidValue"
            }
            Stop-Process -Id $PidValue -Force -ErrorAction SilentlyContinue
            $Stopped += $PidValue
        }
    }
    Start-Sleep -Milliseconds 800
    foreach ($Port in @(8009, 8090)) {
        if (Test-LocalPortListening -Port $Port) { throw "PORT_${Port}_STILL_LISTENING" }
    }
    foreach ($File in $PidFiles) {
        if (Test-Path -LiteralPath $File -PathType Leaf) { Remove-Item -LiteralPath $File -Force }
    }
    Write-Output ("PORTABLE_STOPPED pids=" + (($Stopped | Select-Object -Unique) -join ","))
    exit 0
} catch {
    Write-Error $_.Exception.Message
    exit 2
}
