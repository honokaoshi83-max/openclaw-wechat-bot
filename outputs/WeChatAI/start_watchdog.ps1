[CmdletBinding()]
param()

# Start the bridge supervisor detached from the current shell.
#
# Win32_Process.Create hands the new process to WmiPrvSE.exe, so the watchdog is
# not a child of this PowerShell/agent session. A watchdog started any other way
# dies together with the agent that launched it (root cause of the 2026-09-19
# outage), leaving the bot unsupervised.

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$script = Join-Path $PSScriptRoot 'wait_for_ai_wechat.ps1'
$root = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$runtime = Join-Path $root 'work\wechat_ai_bridge_state'
$lockPath = Join-Path $runtime 'watchdog.lock'
$powershell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'

New-Item -ItemType Directory -Force -Path $runtime | Out-Null

if (Test-Path -LiteralPath $lockPath) {
    $lockOwner = 0
    try {
        $lockOwner = [int](Get-Content -LiteralPath $lockPath -ErrorAction Stop | Select-Object -First 1)
    } catch {
        $lockOwner = 0
    }
    if ($lockOwner -gt 0 -and (Get-Process -Id $lockOwner -ErrorAction SilentlyContinue)) {
        Write-Output "Watchdog already running: PID $lockOwner"
        exit 0
    }
}

$commandLine = "`"$powershell`" -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`""

$startedPid = 0
try {
    $result = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
        CommandLine = $commandLine
    }
    if ($result.ReturnValue -eq 0) { $startedPid = [int]$result.ProcessId }
} catch {
    Write-Warning "detached start failed: $($_.Exception.Message)"
}

if ($startedPid -gt 0) {
    Write-Output "Watchdog started detached: PID $startedPid"
    exit 0
}

$process = Start-Process -FilePath $powershell -WindowStyle Hidden -PassThru -ArgumentList @(
    '-NoProfile', '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden', '-File', "`"$script`""
)
Write-Output "Watchdog started (attached fallback): PID $($process.Id)"
Write-Output "WARNING: an attached watchdog can be killed with this shell; prefer the scheduled task"
