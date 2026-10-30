[CmdletBinding()]
param()

# Stop the detached bridge supervisor. stop.ps1 calls this first, so a deliberate
# stop is not undone by the watchdog restarting the bridge seconds later.

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$root = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$runtime = Join-Path $root 'work\wechat_ai_bridge_state'
$lockPath = Join-Path $runtime 'watchdog.lock'
$script = Join-Path $PSScriptRoot 'wait_for_ai_wechat.ps1'

$stopped = $false
if (Test-Path -LiteralPath $lockPath) {
    $lockOwner = 0
    try {
        $lockOwner = [int](Get-Content -LiteralPath $lockPath -ErrorAction Stop | Select-Object -First 1)
    } catch {
        $lockOwner = 0
    }
    if ($lockOwner -gt 0) {
        $process = Get-Process -Id $lockOwner -ErrorAction SilentlyContinue
        if ($process) {
            Stop-Process -Id $lockOwner -Force -ErrorAction SilentlyContinue
            Write-Output "Watchdog stopped: PID $lockOwner"
            $stopped = $true
        }
    }
    Remove-Item -LiteralPath $lockPath -Force -ErrorAction SilentlyContinue
}

if (-not $stopped) {
    # fall back to command-line discovery when the lock file was lost
    $match = @()
    try {
        $match = @(Get-CimInstance Win32_Process -ErrorAction Stop | Where-Object {
            $_.Name -in @('powershell.exe', 'pwsh.exe') -and
            $_.CommandLine -and $_.CommandLine.Contains($script)
        })
    } catch {
        # restricted shells may deny WMI
    }
    foreach ($item in $match) {
        Stop-Process -Id $item.ProcessId -Force -ErrorAction SilentlyContinue
        Write-Output "Watchdog stopped: PID $($item.ProcessId)"
        $stopped = $true
    }
}

if (-not $stopped) { Write-Output 'Watchdog is not running.' }
