$ErrorActionPreference = 'Stop'
$here = Split-Path $PSScriptRoot -Parent
$startup = Join-Path $here 'startup_backend.ps1'
$waiter = Join-Path $here 'wait_for_ai_wechat.ps1'
$config = Join-Path $here 'config.json'
$startBridge = Join-Path $here 'start.ps1'
$stopBridge = Join-Path $here 'stop.ps1'

if (-not (Test-Path -LiteralPath $startup)) { throw 'startup_backend.ps1 is missing' }
if (-not (Test-Path -LiteralPath $waiter)) { throw 'wait_for_ai_wechat.ps1 is missing' }

$startupText = Get-Content -LiteralPath $startup -Raw
$waiterText = Get-Content -LiteralPath $waiter -Raw

if ($startupText -match 'Weixin\.exe|start_dual_wechat') {
    throw 'Backend autostart must not launch WeChat'
}
if ($startupText -notmatch 'OpenClawGateway') { throw 'OpenClaw startup is missing' }
# The bridge resolves the logged-in account at runtime. The supervisor only
# checks that the configured Hook endpoint is available and does not hardcode a wxid.
if ($waiterText -notmatch 'config\.hook_url') { throw 'Configured Hook port check is missing' }
$bytes = [IO.File]::ReadAllBytes($config)
if ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF) {
    throw 'config.json must not contain a UTF-8 BOM'
}
if ($waiterText -match 'config\.hook_url\s*=' -or $waiterText -match 'Move-Item.+config') {
    throw 'Guardian must not rewrite the verified Hook port'
}
if ((Get-Content -LiteralPath $startBridge -Raw) -notmatch 'WindowStyle\s+Hidden') {
    throw 'Bridge must start without a console window'
}
if ((Get-Content -LiteralPath $stopBridge -Raw) -notmatch 'Win32_Process') {
    throw 'Stop script must find every bridge process, not only a stale PID'
}

Write-Output 'backend autostart contract: PASS'

if ($startupText -match 'llama-server|8080' -or $waiterText -match '\$llamaOnline|8080') { throw 'Local model dependency must be absent' }
