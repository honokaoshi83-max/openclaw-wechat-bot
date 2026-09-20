[CmdletBinding()]
param(
    [switch]$KeepWatchdog
)

# Stop the bridge. Unless -KeepWatchdog is given (the watchdog itself uses that
# switch while WeChat is offline), the supervisor is stopped first so it cannot
# restart the bridge a few seconds later.

$ErrorActionPreference = 'Stop'
$root = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$pidFile = Join-Path $root 'work\wechat_ai_bridge_state\bridge.pid'

if (-not $KeepWatchdog) {
    $stopWatchdog = Join-Path $PSScriptRoot 'stop_watchdog.ps1'
    if (Test-Path -LiteralPath $stopWatchdog) {
        & $stopWatchdog | ForEach-Object { Write-Output $_ }
    }
}

$processes = @()
try {
    $bridge = Join-Path $PSScriptRoot 'bridge.py'
    $processes = @(Get-CimInstance Win32_Process -ErrorAction Stop | Where-Object {
        $_.Name -in @('python.exe', 'pythonw.exe') -and
        $_.CommandLine -and $_.CommandLine.Contains($bridge)
    } | ForEach-Object { Get-Process -Id $_.ProcessId -ErrorAction SilentlyContinue })
} catch {
    # Restricted shells may deny WMI. The PID file remains a safe fallback.
}
if (Test-Path -LiteralPath $pidFile) {
    $recordedPid = [int](Get-Content -LiteralPath $pidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
    if ($recordedPid -gt 0) {
        $candidate = Get-Process -Id $recordedPid -ErrorAction SilentlyContinue
        if ($candidate -and $candidate.ProcessName -in @('python', 'pythonw')) { $processes += $candidate }
    }
}
if ($processes.Count -eq 0) {
    Write-Output 'Bridge is not running.'
} else {
    foreach ($process in ($processes | Sort-Object Id -Unique)) {
        Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
        Write-Output "Bridge stopped: PID $($process.Id)"
    }
}
Remove-Item -LiteralPath $pidFile -ErrorAction SilentlyContinue
