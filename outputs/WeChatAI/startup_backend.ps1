[CmdletBinding()]
param()

# Idempotent "make the Windows side of the stack exist" script.
#
# It is the action of the Codex-WeChat-AI-Bridge scheduled task, which runs at
# logon and then repeats every minute. Running it from Task Scheduler (instead of
# from an agent shell) is what keeps the watchdog outside of any
# agent job object, so they survive agent/session restarts.

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$root = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$runtime = Join-Path $root 'work\wechat_ai_bridge_state'
$log = Join-Path $runtime 'startup.log'
$startWatchdog = Join-Path $PSScriptRoot 'start_watchdog.ps1'
New-Item -ItemType Directory -Force -Path $runtime | Out-Null

function Write-StartupLog([string]$Message) {
    try {
        if ((Test-Path -LiteralPath $log) -and (Get-Item -LiteralPath $log).Length -gt 2MB) {
            Move-Item -LiteralPath $log -Destination "$log.1" -Force
        }
    } catch {
        # rotation is best effort
    }
    Add-Content -LiteralPath $log -Encoding utf8 -Value "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $Message"
}

function Test-PortListening([int]$Port) {
    # A real TCP connect is used because WSL-forwarded ports (18789) are not listed
    # by Get-NetTCPConnection on this host.
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $async = $client.BeginConnect('127.0.0.1', $Port, $null, $null)
        if (-not $async.AsyncWaitHandle.WaitOne(500)) { return $false }
        $client.EndConnect($async)
        return $true
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

$masterSwitchModule = Join-Path $PSScriptRoot 'master_switch.psm1'
Import-Module -Name $masterSwitchModule -Force -ErrorAction Stop
if (Test-BotDisabledThisBoot -RuntimeDirectory $runtime) {
    Write-StartupLog 'intentional stop is active for this Windows boot; skipping backend startup'
    exit 0
}

# DeepSeek-only: no local model service is started.

if (Test-BotDisabledThisBoot -RuntimeDirectory $runtime) {
    Write-StartupLog 'intentional stop was requested during startup; skipping OpenClaw Gateway'
    exit 0
}
try {
    if (Test-PortListening 18789) {
        Write-StartupLog 'OpenClaw Gateway already listening on 18789'
    } else {
        & wsl.exe -d OpenClawGateway -u openclaw -- bash -lc 'systemctl --user start openclaw-gateway.service' 2>&1 |
            ForEach-Object { Write-StartupLog "OpenClaw: $_" }
        if ($LASTEXITCODE -ne 0) {
            Write-StartupLog "ERROR OpenClaw Gateway start returned exit code $LASTEXITCODE"
        } else {
            Write-StartupLog 'OpenClaw Gateway start verified by systemd'
        }
    }
} catch {
    Write-StartupLog "ERROR OpenClaw Gateway: $($_.Exception.Message)"
}

# --- bridge watchdog -------------------------------------------------------
if (Test-BotDisabledThisBoot -RuntimeDirectory $runtime) {
    Write-StartupLog 'intentional stop was requested during startup; skipping watchdog'
    exit 0
}
try {
    $output = & $startWatchdog 2>&1
    $output | ForEach-Object { Write-StartupLog "watchdog: $_" }
} catch {
    Write-StartupLog "ERROR watchdog: $($_.Exception.Message)"
}
