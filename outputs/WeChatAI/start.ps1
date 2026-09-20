$ErrorActionPreference = 'Stop'
$bridge = Join-Path $PSScriptRoot 'bridge.py'
$root = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$python = Join-Path $root 'work\.venv\Scripts\python.exe'
$runtime = Join-Path $root 'work\wechat_ai_bridge_state'
$pidFile = Join-Path $runtime 'bridge.pid'
$stdoutLog = Join-Path $runtime 'bridge.out.log'
$stderrLog = Join-Path $runtime 'bridge.err.log'

if (-not (Test-Path -LiteralPath $python)) {
    throw "Python environment missing: $python"
}
# Start-Process can inherit a stale PowerShell environment after a user-level
# variable was added. Load the saved user key explicitly for the bridge child.
$deepseekKey = [Environment]::GetEnvironmentVariable('DEEPSEEK_API_KEY', 'User')
if ($deepseekKey) {
    $env:DEEPSEEK_API_KEY = $deepseekKey
}
New-Item -ItemType Directory -Force -Path $runtime | Out-Null
$existing = @()
if (Test-Path -LiteralPath $pidFile) {
    $recordedPid = [int](Get-Content -LiteralPath $pidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
    if ($recordedPid -gt 0) {
        $candidate = Get-Process -Id $recordedPid -ErrorAction SilentlyContinue
        if ($candidate -and $candidate.ProcessName -in @('python', 'pythonw')) { $existing = @($candidate) }
    }
}
if ($existing) {
    Write-Output "Bridge already running: PID $($existing[0].Id)"
    exit 0
}
# Also inspect the process table. A watchdog restart can race with this script
# before the PID file is rewritten, which would make two bridges share db_cache.
try {
    $running = @(Get-CimInstance Win32_Process -ErrorAction Stop | Where-Object {
        $_.Name -in @('python.exe', 'pythonw.exe') -and
        $_.CommandLine -and $_.CommandLine.Contains($bridge)
    })
    if ($running.Count -gt 0) {
        Write-Output "Bridge already running: PID $($running[0].ProcessId)"
        Set-Content -LiteralPath $pidFile -Value $running[0].ProcessId -Encoding ascii
        exit 0
    }
} catch {
    # PID-file checking above remains the fallback for restricted WMI access.
}

# Keep the previous process output so an unhandled traceback is never lost:
# stdout/stderr of a hidden window used to be discarded entirely.
foreach ($path in @($stdoutLog, $stderrLog)) {
    if (Test-Path -LiteralPath $path) {
        Move-Item -LiteralPath $path -Destination "$path.1" -Force -ErrorAction SilentlyContinue
    }
}

$process = Start-Process -FilePath $python -ArgumentList @("`"$bridge`"") -WorkingDirectory $root `
    -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput $stdoutLog -RedirectStandardError $stderrLog
Set-Content -LiteralPath $pidFile -Value $process.Id -Encoding ascii
Write-Output "Bridge started: PID $($process.Id)"
