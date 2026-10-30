$ErrorActionPreference = 'Stop'
$taskName = 'Codex-WeChat-AI-Control'
$user = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$executable = Join-Path $PSScriptRoot 'BotSwitch.exe'
if (-not (Test-Path -LiteralPath $executable)) {
    throw "Control executable is missing. Build it first: $executable"
}

$action = New-ScheduledTaskAction -Execute $executable
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$trigger.Delay = 'PT5S'
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -Hidden `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Days 365)
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings -Force | Out-Null

$registered = Get-ScheduledTask -TaskName $taskName -ErrorAction Stop
if (-not $registered.Settings.Enabled -or $registered.Actions[0].Execute -ne $executable) {
    throw 'Control task registration did not verify'
}
Write-Output "Control window will open at sign-in for $user. Existing backend autostart remains enabled."
