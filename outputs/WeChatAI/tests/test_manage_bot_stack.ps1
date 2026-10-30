$ErrorActionPreference = 'Stop'
$here = Split-Path $PSScriptRoot -Parent
$scriptPath = Join-Path $here 'manage_bot_stack.ps1'
if (-not (Test-Path -LiteralPath $scriptPath)) { throw 'manage_bot_stack.ps1 is missing' }
$source = Get-Content -LiteralPath $scriptPath -Raw

$runtime = Join-Path ([IO.Path]::GetTempPath()) ("bot-switch-manager-test-" + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $runtime | Out-Null
try {
    $statusLine = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $scriptPath `
        -Action Status -RuntimeDirectory $runtime | Select-Object -Last 1
    $status = $statusLine | ConvertFrom-Json
    foreach ($field in @('disabled', 'hook', 'bridge', 'openclaw', 'errors')) {
        if ($null -eq $status.PSObject.Properties[$field]) { throw "status is missing field '$field'" }
    }

    Import-Module (Join-Path $here 'master_switch.psm1') -Force
    Set-BotDisabledThisBoot -RuntimeDirectory $runtime
    $plannedStart = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $scriptPath `
        -Action Start -RuntimeDirectory $runtime -WhatIf 2>&1 | Out-String
    if (-not (Test-BotDisabledThisBoot -RuntimeDirectory $runtime)) {
        throw 'WhatIf Start must not change the stop marker'
    }
    if ($source -notmatch 'startup_backend_hidden\.vbs') { throw 'Start must call the existing hidden backend launcher' }
    if ($source -notmatch "Start-ScheduledTask\s+-TaskName\s+'Codex-WeChat-AI-Bridge'") {
        throw 'Start must reuse the existing single-instance backend task when available'
    }
    if ($source -notmatch '\[Console\]::OutputEncoding\s*=.+UTF8Encoding') {
        throw 'Management JSON must use UTF-8 for the GUI process'
    }

    Clear-BotDisabledThisBoot -RuntimeDirectory $runtime
    $plannedStop = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $scriptPath `
        -Action Stop -RuntimeDirectory $runtime -WhatIf 2>&1 | Out-String
    if (Test-BotDisabledThisBoot -RuntimeDirectory $runtime) { throw 'WhatIf Stop must not write a marker' }
    if ($plannedStop -notmatch 'Set intentional-stop marker' -or
        $plannedStop -notmatch 'Stop user service in WSL') {
        throw 'WhatIf Stop must preview the stop sequence'
    }

    $markerCall = $source.IndexOf('Set-BotDisabledThisBoot')
    $watchdogStop = $source.IndexOf('Stop-OwnedProcesses -Component')
    if ($markerCall -lt 0 -or $watchdogStop -lt 0 -or $markerCall -gt $watchdogStop) {
        throw 'Stop must write the marker before stopping processes'
    }
    if ($source -match "Stop-Process\s+-Name\s+.{0,20}(python|node|wsl|docker)") {
        throw 'Stop must not kill processes by broad process name'
    }
    if ($source -match 'llama-server|8080|comfyMain' -or $source -notmatch 'OpenClawGateway') {
        throw 'Lifecycle must only manage the DeepSeek/OpenClaw bot services'
    }
} finally {
    Remove-Module master_switch -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $runtime -Recurse -Force -ErrorAction SilentlyContinue
}
Write-Output 'bot stack lifecycle contract: PASS'
