$ErrorActionPreference = 'Stop'
$taskName = 'Codex-WeChat-AI-Bridge'
$task = Get-ScheduledTask -TaskName $taskName -ErrorAction Stop
$action = @($task.Actions)[0]

if ([IO.Path]::GetFileName($action.Execute) -ne 'wscript.exe') {
    throw "Autostart launches a console program directly: $($action.Execute)"
}
if (-not $task.Settings.Hidden) {
    throw 'Autostart task is visible instead of being registered as hidden'
}

Write-Output 'registered autostart window contract: PASS'
