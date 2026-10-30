[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$HookDll,
    [string]$InstallRoot = 'D:\Weixin',
    [Parameter(Mandatory = $true)][string]$ExpectedSha256
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Test-IsAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not (Test-IsAdministrator)) {
    throw 'Run this script from an elevated PowerShell window.'
}
$source = (Resolve-Path -LiteralPath $HookDll).Path
$root = (Resolve-Path -LiteralPath $InstallRoot).Path
$wechatExe = Join-Path $root 'Weixin.exe'
if (-not (Test-Path -LiteralPath $wechatExe -PathType Leaf)) {
    throw "Weixin.exe not found below $root"
}
$sourceHash = (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash
if ($sourceHash -ne $ExpectedSha256.ToUpperInvariant()) {
    throw "Hook DLL SHA256 mismatch: $sourceHash"
}

Get-Process -Name 'Weixin' -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Seconds 2
if (Get-Process -Name 'Weixin' -ErrorAction SilentlyContinue) {
    throw 'Weixin processes are still running; Hook DLL was not changed.'
}

$target = Join-Path $root 'version.dll'
if (Test-Path -LiteralPath $target -PathType Leaf) {
    $oldHash = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant()
    $backup = Join-Path $root "version.dll.$($oldHash.Substring(0, 12)).bak"
    if (-not (Test-Path -LiteralPath $backup)) {
        Copy-Item -LiteralPath $target -Destination $backup
    }
}
Copy-Item -LiteralPath $source -Destination $target -Force
$installedHash = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash
if ($installedHash -ne $ExpectedSha256.ToUpperInvariant()) {
    throw "Installed Hook DLL SHA256 mismatch: $installedHash"
}
Write-Output "Hook installed: $target"
Write-Output "SHA256: $installedHash"
