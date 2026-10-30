[CmdletBinding()]
param(
    [string]$WeixinExe = 'D:\Weixin\Weixin.exe',
    [Alias('PersonalPort')][int]$AiPort = 30001,
    [Alias('BotPort')][int]$MainPort = 30002,
    [switch]$Restart
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$exe = (Resolve-Path -LiteralPath $WeixinExe).Path
$currentSession = (Get-Process -Id $PID).SessionId
$running = @(Get-Process -Name 'Weixin' -ErrorAction SilentlyContinue |
    Where-Object SessionId -eq $currentSession)
if ($running.Count -gt 0 -and -not $Restart) {
    throw 'Weixin is already running in this Windows session. Use -Restart to close it and launch both instances together.'
}
if ($running.Count -gt 0) {
    $running | Stop-Process -Force
    Start-Sleep -Seconds 2
}

$firstInfo = [Diagnostics.ProcessStartInfo]::new($exe, "StartPort=$AiPort")
$firstInfo.UseShellExecute = $true
$secondInfo = [Diagnostics.ProcessStartInfo]::new($exe, "StartPort=$MainPort")
$secondInfo.UseShellExecute = $true
$first = [Diagnostics.Process]::Start($firstInfo)
$second = [Diagnostics.Process]::Start($secondInfo)

$deadline = [DateTime]::UtcNow.AddSeconds(15)
do {
    Start-Sleep -Milliseconds 500
    $ports = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
        Where-Object LocalPort -in @($AiPort, $MainPort) |
        Select-Object -ExpandProperty LocalPort -Unique)
} while ($ports.Count -lt 2 -and [DateTime]::UtcNow -lt $deadline)

$result = [pscustomobject]@{
    AiPort = $AiPort
    AiPortListening = ($AiPort -in $ports)
    MainPort = $MainPort
    MainPortListening = ($MainPort -in $ports)
    InitialPids = @($first.Id, $second.Id)
}
$result | Format-List
if ($ports.Count -lt 2) {
    throw 'Dual launch did not produce two Hook listeners. Close WeChat and run this script again.'
}
