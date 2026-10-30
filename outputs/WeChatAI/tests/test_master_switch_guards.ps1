$ErrorActionPreference = 'Stop'
$here = Split-Path $PSScriptRoot -Parent
$startupPath = Join-Path $here 'startup_backend.ps1'
$watchdogPath = Join-Path $here 'wait_for_ai_wechat.ps1'
$startup = Get-Content -LiteralPath $startupPath -Raw
$watchdog = Get-Content -LiteralPath $watchdogPath -Raw

if ($startup -notmatch '(?s)masterSwitchModule\s*=\s*Join-Path[^\r\n]+master_switch\.psm1[\s\S]*?Import-Module\s+-Name\s+\$masterSwitchModule') {
    throw 'startup backend must load master switch state'
}
if ($watchdog -notmatch '(?s)masterSwitchModule\s*=\s*Join-Path[^\r\n]+master_switch\.psm1[\s\S]*?Import-Module\s+-Name\s+\$masterSwitchModule') {
    throw 'watchdog must load master switch state'
}

$startupGuard = $startup.IndexOf('Test-BotDisabledThisBoot')
$startupLlama = $startup.IndexOf('# DeepSeek-only:')
if ($startupGuard -lt 0 -or $startupLlama -lt 0 -or $startupGuard -gt $startupLlama) {
    throw 'startup backend must check the stop marker before starting components'
}
$startupGuardCount = [regex]::Matches($startup, 'Test-BotDisabledThisBoot').Count
if ($startupGuardCount -lt 3) { throw 'startup backend must recheck the marker before each service start' }

$loopStart = $watchdog.IndexOf('while ($true)')
$watchdogGuard = $watchdog.IndexOf('Test-BotDisabledThisBoot', $loopStart)
$watchdogStart = $watchdog.IndexOf('& $startBridge', $loopStart)
$watchdogBackend = $watchdog.IndexOf('& $startBackend', $loopStart)
if ($loopStart -lt 0 -or $watchdogGuard -lt $loopStart -or
    ($watchdogStart -ge 0 -and $watchdogGuard -gt $watchdogStart) -or
    ($watchdogBackend -ge 0 -and $watchdogGuard -gt $watchdogBackend)) {
    throw 'watchdog must check the stop marker before restarting components on every pass'
}
if ($startup -match 'Weixin\.exe|start_dual_wechat' -or $watchdog -match 'Weixin\.exe|start_dual_wechat') {
    throw 'startup and watchdog must not launch WeChat'
}
Write-Output 'master switch startup guards: PASS'
