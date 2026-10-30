$ErrorActionPreference = 'Stop'
$here = Split-Path $PSScriptRoot -Parent
$installer = Join-Path $here 'install_bot_switch_autostart.ps1'
$taskName = 'Codex-WeChat-AI-Control'
$backendTaskName = 'Codex-WeChat-AI-Bridge'
if (-not (Test-Path -LiteralPath $installer)) { throw 'install_bot_switch_autostart.ps1 is missing' }

function Get-TaskContract([string]$Name) {
    $task = Get-ScheduledTask -TaskName $Name -ErrorAction Stop
    $actions = @($task.Actions | ForEach-Object {
        [ordered]@{ Execute = $_.Execute; Arguments = $_.Arguments; WorkingDirectory = $_.WorkingDirectory }
    })
    $triggers = @($task.Triggers | ForEach-Object {
        [ordered]@{
            Type = $_.CimClass.CimClassName
            StartBoundary = $_.StartBoundary
            Delay = $_.Delay
            UserId = $_.UserId
            RepetitionInterval = $_.Repetition.Interval
        }
    })
    return [ordered]@{
        Enabled = $task.Settings.Enabled
        Hidden = $task.Settings.Hidden
        MultipleInstances = $task.Settings.MultipleInstances
        PrincipalUser = $task.Principal.UserId
        PrincipalLogonType = $task.Principal.LogonType
        PrincipalRunLevel = $task.Principal.RunLevel
        Actions = $actions
        Triggers = $triggers
    } | ConvertTo-Json -Compress -Depth 8
}

$backendBefore = Get-TaskContract $backendTaskName
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $installer
if ($LASTEXITCODE -ne 0) { throw "Control task registration failed with exit code $LASTEXITCODE" }
$backendAfter = Get-TaskContract $backendTaskName
if ($backendBefore -cne $backendAfter) { throw 'Installing the control window changed the existing backend task' }

$control = Get-ScheduledTask -TaskName $taskName -ErrorAction Stop
$expectedExe = Join-Path $here 'BotSwitch.exe'
if ([IO.Path]::GetFileName($control.Actions[0].Execute) -ne 'BotSwitch.exe') {
    throw 'Control task does not launch BotSwitch.exe'
}
if (-not ($control.Triggers | Where-Object { $_.CimClass.CimClassName -match 'LogonTrigger' })) {
    throw 'Control task has no user logon trigger'
}
if ($control.Principal.LogonType -ne 'Interactive') { throw 'Control task must run in the interactive user session' }
if (-not $control.Settings.Hidden) { throw 'Control task should not open a console host' }
if (-not $control.Settings.Enabled) { throw 'Control task is not enabled' }
if (-not (Test-Path -LiteralPath $expectedExe)) { throw 'Control task executable is missing' }
Write-Output 'bot switch per-user autostart and backend preservation: PASS'
