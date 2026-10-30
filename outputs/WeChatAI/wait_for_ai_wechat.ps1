[CmdletBinding()]
param(
    [int]$PollSeconds = 10,
    [int]$BackendRetrySeconds = 60,
    [int]$HeartbeatSeconds = 300,
    [switch]$Once
)

# Supervisor for the WeChat AI bridge.
#
# Start it through start_watchdog.ps1 (WMI) or through the Codex-WeChat-AI-Bridge
# scheduled task -- never directly from an agent/terminal shell: processes started
# inside an agent job object are terminated together with that job, which is what
# silently killed the bridge on 2026-09-19 23:36.
#
# Responsibilities:
#   1. keep the bridge running while the WeChat Hook port is online;
#   2. hold the bridge stopped while WeChat itself is offline;
#   3. restart the OpenClaw Gateway (18789) when it is down.

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$root = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$runtime = Join-Path $root 'work\wechat_ai_bridge_state'
$log = Join-Path $runtime 'watchdog.log'
$lockPath = Join-Path $runtime 'watchdog.lock'
$pidFile = Join-Path $runtime 'bridge.pid'
$configPath = Join-Path $PSScriptRoot 'config.json'
$startBridge = Join-Path $PSScriptRoot 'start.ps1'
$stopBridge = Join-Path $PSScriptRoot 'stop.ps1'
$startBackend = Join-Path $PSScriptRoot 'startup_backend.ps1'
$masterSwitchModule = Join-Path $PSScriptRoot 'master_switch.psm1'
Import-Module -Name $masterSwitchModule -Force -ErrorAction Stop

New-Item -ItemType Directory -Force -Path $runtime | Out-Null

function Write-WatchdogLog([string]$Message) {
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
    # Windows PowerShell 5.1 and WSL-forwarded ports (18789) are not reported by
    # Get-NetTCPConnection, so probe with a real TCP connect instead.
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

function Get-BridgeProcess {
    if (-not (Test-Path -LiteralPath $pidFile)) { return $null }
    $recorded = 0
    try {
        $recorded = [int](Get-Content -LiteralPath $pidFile -ErrorAction Stop | Select-Object -First 1)
    } catch {
        return $null
    }
    if ($recorded -le 0) { return $null }
    $candidate = Get-Process -Id $recorded -ErrorAction SilentlyContinue
    if ($candidate -and $candidate.ProcessName -in @('python', 'pythonw')) { return $candidate }
    return $null
}

function Get-HookPort {
    $port = 30001
    try {
        # config.json is UTF-8 without BOM; Windows PowerShell 5.1 would otherwise
        # decode it as ANSI and fail to parse the Chinese mention names.
        $config = Get-Content -LiteralPath $configPath -Raw -Encoding UTF8 | ConvertFrom-Json
        $uri = [uri][string]$config.hook_url
        if ($uri.Port -gt 0) { $port = $uri.Port }
    } catch {
        Write-WatchdogLog "config.json unreadable; defaulting to hook port 30001"
    }
    return $port
}

if (Test-BotDisabledThisBoot -RuntimeDirectory $runtime) {
    Write-WatchdogLog 'intentional stop is active for this Windows boot; watchdog will not start'
    exit 0
}

# --- single instance -------------------------------------------------------
if (-not $Once -and (Test-Path -LiteralPath $lockPath)) {
    $lockOwner = 0
    try {
        $lockOwner = [int](Get-Content -LiteralPath $lockPath -ErrorAction Stop | Select-Object -First 1)
    } catch {
        $lockOwner = 0
    }
    if ($lockOwner -gt 0 -and (Get-Process -Id $lockOwner -ErrorAction SilentlyContinue)) {
        Write-Output "watchdog already running: PID $lockOwner"
        exit 0
    }
}
if (-not $Once) {
    Set-Content -LiteralPath $lockPath -Value $PID -Encoding ascii
}

$hookOfflineReported = $false
$profileReported = $false
$heartbeatAt = [datetime]::MinValue
$backendTriedAt = [datetime]::MinValue
Write-WatchdogLog "watchdog ready pid=$PID poll=${PollSeconds}s"

try {
    while ($true) {
        if (Test-BotDisabledThisBoot -RuntimeDirectory $runtime) {
            Write-WatchdogLog 'intentional stop is active for this Windows boot; exiting supervision loop'
            break
        }
        $now = Get-Date
        $hookPort = Get-HookPort
        $hookOnline = Test-PortListening $hookPort
        $bridge = Get-BridgeProcess
        $gatewayOnline = Test-PortListening 18789
        $bridgeRunning = [bool]$bridge

        if (-not $hookOnline) {
            if ($bridgeRunning) {
                & $stopBridge -KeepWatchdog | ForEach-Object { Write-WatchdogLog "bridge: $_" }
                $bridgeRunning = $false
            }
            if (-not $hookOfflineReported) {
                Write-WatchdogLog "hook port $hookPort offline; bridge held stopped until the AI WeChat account is logged in"
                $hookOfflineReported = $true
            }
        } else {
            if ($hookOfflineReported) {
                Write-WatchdogLog "hook port $hookPort online again; resuming bridge supervision"
                $hookOfflineReported = $false
            }
            if (-not $profileReported) {
                try {
                    $profile = Invoke-RestMethod -Uri "http://127.0.0.1:$hookPort/GetSelfProfile" `
                        -Method Post -ContentType 'application/json' -Body '{}' -TimeoutSec 2
                    Write-WatchdogLog "hook port $hookPort profile reported wxid=$($profile.wxid); routing remains fixed"
                } catch {
                    Write-WatchdogLog "hook port $hookPort is listening; profile query unavailable; routing remains fixed"
                }
                $profileReported = $true
            }
            if (-not $bridgeRunning) {
                & $startBridge | ForEach-Object { Write-WatchdogLog "bridge: $_" }
                $bridgeRunning = [bool](Get-BridgeProcess)
                if (-not $bridgeRunning) {
                    Write-WatchdogLog "bridge did not come up; retrying on the next poll"
                }
            }
        }

        if (-not $gatewayOnline) {
            if (($now - $backendTriedAt).TotalSeconds -ge $BackendRetrySeconds) {
                $backendTriedAt = $now
                Write-WatchdogLog "backend down (gateway=$gatewayOnline); running startup_backend.ps1"
                & $startBackend 2>&1 | ForEach-Object { Write-WatchdogLog "backend: $_" }
            }
        }

        if (($now - $heartbeatAt).TotalSeconds -ge $HeartbeatSeconds) {
            $heartbeatAt = $now
            Write-WatchdogLog "heartbeat hook=$hookOnline bridge=$bridgeRunning gateway=$gatewayOnline"
        }

        if ($Once) { break }
        Start-Sleep -Seconds $PollSeconds
    }
} finally {
    if (-not $Once -and (Test-Path -LiteralPath $lockPath)) {
        try {
            $lockOwner = [int](Get-Content -LiteralPath $lockPath -ErrorAction Stop | Select-Object -First 1)
            if ($lockOwner -eq $PID) { Remove-Item -LiteralPath $lockPath -Force -ErrorAction SilentlyContinue }
        } catch {
            # the lock file is rebuilt on the next start
        }
    }
}
