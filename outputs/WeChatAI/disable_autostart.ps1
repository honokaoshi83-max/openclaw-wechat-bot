$ErrorActionPreference = 'Stop'
$taskName = 'Codex-WeChat-AI-Bridge'
$task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($task) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    Write-Output "Autostart disabled: $taskName"
} else {
    Write-Output 'Autostart task not found.'
}

$stopWatchdog = Join-Path $PSScriptRoot 'stop_watchdog.ps1'
if (Test-Path -LiteralPath $stopWatchdog) {
    & $stopWatchdog | ForEach-Object { Write-Output $_ }
}
