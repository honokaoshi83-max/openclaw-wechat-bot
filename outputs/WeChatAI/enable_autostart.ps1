$ErrorActionPreference = 'Stop'
$taskName = 'Codex-WeChat-AI-Bridge'
$user = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$launcher = Join-Path $PSScriptRoot 'startup_backend_hidden.vbs'
$wscript = Join-Path $env:SystemRoot 'System32\wscript.exe'
if (-not (Test-Path -LiteralPath $launcher)) {
    throw "Windowless backend launcher is missing: $launcher"
}
$action = New-ScheduledTaskAction -Execute $wscript -Argument "//B //Nologo `"$launcher`""

# Logon trigger (with the 20s delay) plus a separate one-minute repeating trigger:
# the repetition is what makes the stack self-healing when a component dies without
# anyone noticing. A repetition attached to the logon trigger itself is not honoured
# by the unified scheduling engine, so the repeat lives on its own time trigger.
$logonTrigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$logonTrigger.Delay = 'PT20S'
$repeatTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes 1)

$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -Hidden `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 15)
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger @($logonTrigger, $repeatTrigger) `
    -Principal $principal -Settings $settings -Force | Out-Null
Write-Output "Backend autostart and self-check enabled for $user : $taskName (every 1 minute)"

Start-ScheduledTask -TaskName $taskName
Write-Output 'Backend check started now.'
