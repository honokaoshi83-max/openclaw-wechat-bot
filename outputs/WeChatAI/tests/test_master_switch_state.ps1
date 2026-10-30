$ErrorActionPreference = 'Stop'
$here = Split-Path $PSScriptRoot -Parent
$module = Join-Path $here 'master_switch.psm1'
if (-not (Test-Path -LiteralPath $module)) { throw 'master_switch.psm1 is missing' }
Import-Module $module -Force

$runtime = Join-Path ([IO.Path]::GetTempPath()) ("bot-switch-test-" + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $runtime | Out-Null
try {
    if (Test-BotDisabledThisBoot -RuntimeDirectory $runtime) { throw 'missing marker must mean enabled' }
    Set-BotDisabledThisBoot -RuntimeDirectory $runtime
    if (-not (Test-BotDisabledThisBoot -RuntimeDirectory $runtime)) { throw 'current boot marker was not recognized' }

    $markerPath = Join-Path $runtime 'bot_disabled.json'
    @{ boot_identity = '1900-01-01T00:00:00.0000000Z' } |
        ConvertTo-Json | Set-Content -LiteralPath $markerPath -Encoding utf8
    if (Test-BotDisabledThisBoot -RuntimeDirectory $runtime) { throw 'stale marker must be ignored after reboot' }

    Clear-BotDisabledThisBoot -RuntimeDirectory $runtime
    if (Test-Path -LiteralPath $markerPath) { throw 'clear must remove marker' }
} finally {
    Remove-Item -LiteralPath $runtime -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Module master_switch -ErrorAction SilentlyContinue
}
Write-Output 'master switch boot state: PASS'
