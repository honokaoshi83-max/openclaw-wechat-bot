[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'Medium')]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('Status', 'Start', 'Stop')]
    [string]$Action,
    [string]$RuntimeDirectory
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$root = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
if (-not $RuntimeDirectory) { $RuntimeDirectory = Join-Path $root 'work\wechat_ai_bridge_state' }
$masterSwitchModule = Join-Path $PSScriptRoot 'master_switch.psm1'
Import-Module -Name $masterSwitchModule -Force -ErrorAction Stop
$errors = [Collections.Generic.List[string]]::new()

function Test-LocalPort([int]$Port) {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $async = $client.BeginConnect('127.0.0.1', $Port, $null, $null)
        if (-not $async.AsyncWaitHandle.WaitOne(350)) { return $false }
        $client.EndConnect($async)
        return $true
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

function Get-ProcessInventory {
    try { return @(Get-CimInstance -ClassName Win32_Process -ErrorAction Stop) }
    catch { $errors.Add("进程状态查询失败：$($_.Exception.Message)"); return @() }
}

function Stop-OwnedProcesses {
    [CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'Medium')]
    param([string]$Component, [object[]]$Processes)
    foreach ($item in $Processes) {
        $processId = [int]$item.ProcessId
        try {
            if ($PSCmdlet.ShouldProcess("$Component PID $processId", 'Stop project-owned process')) {
                Stop-Process -Id $processId -Force -ErrorAction Stop
                $deadline = (Get-Date).AddSeconds(8)
                while ((Get-Process -Id $processId -ErrorAction SilentlyContinue) -and (Get-Date) -lt $deadline) {
                    Start-Sleep -Milliseconds 150
                }
                if (Get-Process -Id $processId -ErrorAction SilentlyContinue) {
                    $errors.Add("$Component 未能停止（PID $processId）")
                }
            }
        } catch {
            $errors.Add("$Component 停止失败（PID ${processId}）：$($_.Exception.Message)")
        }
    }
}

function Get-CommandLineMatches([object[]]$Inventory, [string]$ProcessName, [string]$ExpectedPath) {
    return @($Inventory | Where-Object {
        $_.Name -ieq $ProcessName -and $_.CommandLine -and
        $_.CommandLine.IndexOf($ExpectedPath, [StringComparison]::OrdinalIgnoreCase) -ge 0
    })
}

function Get-BotStatus {
    $configPath = Join-Path $PSScriptRoot 'config.json'
    $hookPort = 30001
    try {
        $config = Get-Content -LiteralPath $configPath -Raw -Encoding UTF8 | ConvertFrom-Json -ErrorAction Stop
        $hookUri = [uri][string]$config.hook_url
        if ($hookUri.Port -gt 0) { $hookPort = $hookUri.Port }
    } catch {
        $errors.Add("微信 Hook 配置读取失败：$($_.Exception.Message)")
    }
    $inventory = Get-ProcessInventory
    $bridgePath = Join-Path $PSScriptRoot 'bridge.py'
    $bridge = @($inventory | Where-Object {
        $_.Name -in @('python.exe', 'pythonw.exe') -and $_.CommandLine -and
        $_.CommandLine.IndexOf($bridgePath, [StringComparison]::OrdinalIgnoreCase) -ge 0
    }).Count -gt 0
    return [ordered]@{
        disabled = Test-BotDisabledThisBoot -RuntimeDirectory $RuntimeDirectory
        hook = Test-LocalPort $hookPort
        bridge = $bridge
        openclaw = Test-LocalPort 18789
        errors = @($errors.ToArray())
    }
}

if ($Action -eq 'Start') {
    $canStart = $false
    if ($PSCmdlet.ShouldProcess('current Windows boot', 'Clear intentional-stop marker')) {
        try {
            Clear-BotDisabledThisBoot -RuntimeDirectory $RuntimeDirectory
            $canStart = -not (Test-BotDisabledThisBoot -RuntimeDirectory $RuntimeDirectory)
            if (-not $canStart) { $errors.Add('关闭标记仍有效，未启动后台') }
        } catch { $errors.Add("关闭标记清除失败：$($_.Exception.Message)") }
    } elseif ($WhatIfPreference) {
        $canStart = $true
    }
    $backendLauncher = Join-Path $PSScriptRoot 'startup_backend_hidden.vbs'
    $wscript = Join-Path $env:SystemRoot 'System32\wscript.exe'
    if ($canStart -and $PSCmdlet.ShouldProcess('WeChat bot backend', 'Run existing startup and recovery flow')) {
        try {
            $backendTask = Get-ScheduledTask -TaskName 'Codex-WeChat-AI-Bridge' -ErrorAction SilentlyContinue
            if ($backendTask) {
                if ($backendTask.State -ne 'Running') {
                    Start-ScheduledTask -TaskName 'Codex-WeChat-AI-Bridge' -ErrorAction Stop
                }
            } else {
                if (-not (Test-Path -LiteralPath $backendLauncher)) { throw 'Hidden backend launcher is missing' }
                $process = Start-Process -FilePath $wscript -ArgumentList @('//B', '//Nologo', "`"$backendLauncher`"") `
                    -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -PassThru -Wait
                if ($process.ExitCode -ne 0) { throw "Startup flow returned exit code $($process.ExitCode)" }
            }
        } catch { $errors.Add("启动失败：$($_.Exception.Message)") }
    }
}
elseif ($Action -eq 'Stop') {
    $canStop = $false
    if ($PSCmdlet.ShouldProcess('current Windows boot', 'Set intentional-stop marker')) {
        try {
            Set-BotDisabledThisBoot -RuntimeDirectory $RuntimeDirectory
            $canStop = Test-BotDisabledThisBoot -RuntimeDirectory $RuntimeDirectory
            if (-not $canStop) { $errors.Add('关闭标记未生效，未停止后台') }
        }
        catch { $errors.Add("无法设置关闭标记：$($_.Exception.Message)") }
    } elseif ($WhatIfPreference) {
        $canStop = $true
    }
    if (-not $canStop) {
        $status = Get-BotStatus
        $status.errors = @($errors.ToArray())
        $status | ConvertTo-Json -Compress -Depth 4
        exit 0
    }

    # Stop only a currently running instance; keep the scheduled task registered
    # so its one-minute check and next-boot autostart preference remain enabled.
    try {
        $task = Get-ScheduledTask -TaskName 'Codex-WeChat-AI-Bridge' -ErrorAction SilentlyContinue
        if ($task -and $task.State -eq 'Running' -and
            $PSCmdlet.ShouldProcess('Codex-WeChat-AI-Bridge', 'Stop current scheduled-task instance')) {
            Stop-ScheduledTask -TaskName 'Codex-WeChat-AI-Bridge' -ErrorAction Stop
        }
    } catch { $errors.Add("停止当前后台自检任务失败：$($_.Exception.Message)") }

    $inventory = Get-ProcessInventory
    $waiterPath = Join-Path $PSScriptRoot 'wait_for_ai_wechat.ps1'
    $startupPath = Join-Path $PSScriptRoot 'startup_backend.ps1'
    $hiddenLauncherPath = Join-Path $PSScriptRoot 'startup_backend_hidden.vbs'
    $bridgePath = Join-Path $PSScriptRoot 'bridge.py'
    $waiter = @($inventory | Where-Object {
        $_.Name -in @('powershell.exe', 'pwsh.exe') -and $_.CommandLine -and
        $_.CommandLine.IndexOf($waiterPath, [StringComparison]::OrdinalIgnoreCase) -ge 0
    })
    Stop-OwnedProcesses -Component '微信 watchdog' -Processes $waiter -WhatIf:$WhatIfPreference

    $startupProcesses = @($inventory | Where-Object {
        $_.Name -in @('powershell.exe', 'pwsh.exe') -and $_.CommandLine -and
        $_.CommandLine.IndexOf($startupPath, [StringComparison]::OrdinalIgnoreCase) -ge 0
    })
    Stop-OwnedProcesses -Component '后台启动检查' -Processes $startupProcesses -WhatIf:$WhatIfPreference

    $hiddenLaunchers = Get-CommandLineMatches -Inventory $inventory -ProcessName 'wscript.exe' -ExpectedPath $hiddenLauncherPath
    Stop-OwnedProcesses -Component '后台启动器' -Processes $hiddenLaunchers -WhatIf:$WhatIfPreference

    $bridgeProcesses = @($inventory | Where-Object {
        $_.Name -in @('python.exe', 'pythonw.exe') -and $_.CommandLine -and
        $_.CommandLine.IndexOf($bridgePath, [StringComparison]::OrdinalIgnoreCase) -ge 0
    })
    Stop-OwnedProcesses -Component '微信桥接' -Processes $bridgeProcesses -WhatIf:$WhatIfPreference

    if ($PSCmdlet.ShouldProcess('OpenClawGateway/openclaw-gateway.service', 'Stop user service in WSL')) {
        try {
            $wsl = Join-Path $env:SystemRoot 'System32\wsl.exe'
            $result = Start-Process -FilePath $wsl -ArgumentList @('-d', 'OpenClawGateway', '-u', 'openclaw', '--',
                'bash', '-lc', 'systemctl --user stop openclaw-gateway.service') -WindowStyle Hidden -PassThru -Wait
            if ($result.ExitCode -ne 0) { throw "WSL returned exit code $($result.ExitCode)" }
        } catch { $errors.Add("OpenClaw 停止失败：$($_.Exception.Message)") }
    }
}

$status = Get-BotStatus
$status.errors = @($errors.ToArray())
$status | ConvertTo-Json -Compress -Depth 4
