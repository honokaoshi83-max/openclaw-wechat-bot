$ErrorActionPreference = 'Stop'
$taskName = 'Codex-WeChat-AI-Control'
$task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($task) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    Write-Output "Control window autostart removed: $taskName"
} else {
    Write-Output 'Control window autostart task was not registered.'
}
