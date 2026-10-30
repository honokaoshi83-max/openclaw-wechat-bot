[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$InstallRoot = 'D:\Weixin',
    [string]$StatePath,
    [string[]]$ProfileRoots = @(),
    [switch]$Apply
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ([string]::IsNullOrWhiteSpace($StatePath)) {
    $StatePath = Join-Path $PSScriptRoot 'wechat_update_block_state.json'
}

function Get-WeChatUpdateTargets {
    param([Parameter(Mandatory = $true)][string]$InstallRoot)

    if (-not (Test-Path -LiteralPath $InstallRoot -PathType Container)) {
        throw "WeChat install directory not found: $InstallRoot"
    }

    @(Get-ChildItem -LiteralPath $InstallRoot -File -Recurse -Filter 'WeixinUpdate.exe' -ErrorAction Stop |
        ForEach-Object { $_.FullName })
}

function Get-UpdateRuleName {
    param([Parameter(Mandatory = $true)][string]$ProgramPath)

    $sha1 = [Security.Cryptography.SHA1]::Create()
    try {
        $bytes = [Text.Encoding]::UTF8.GetBytes($ProgramPath.ToLowerInvariant())
        $digest = ([BitConverter]::ToString($sha1.ComputeHash($bytes))).Replace('-', '').Substring(0, 12)
    }
    finally {
        $sha1.Dispose()
    }
    "WeChatAI Block WeixinUpdate $digest"
}

function Get-WeChatUpdateBlockPlan {
    param([Parameter(Mandatory = $true)][string]$InstallRoot)

    @(Get-WeChatUpdateTargets -InstallRoot $InstallRoot | ForEach-Object {
        [pscustomobject]@{
            ProgramPath = $_
            RuleName = Get-UpdateRuleName -ProgramPath $_
        }
    })
}

function Get-ProfileRoots {
    param([string[]]$ConfiguredRoots = @())

    if ($ConfiguredRoots.Count -gt 0) {
        return @($ConfiguredRoots | ForEach-Object { [IO.Path]::GetFullPath($_) } | Select-Object -Unique)
    }
    $profileList = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList'
    @(Get-ChildItem -LiteralPath $profileList -ErrorAction SilentlyContinue | ForEach-Object {
        $profile = Get-ItemProperty -LiteralPath $_.PSPath -Name ProfileImagePath -ErrorAction SilentlyContinue
        $value = if ($null -ne $profile) { $profile.ProfileImagePath } else { $null }
        if ($value) { [Environment]::ExpandEnvironmentVariables($value) }
    } | Where-Object { $_ -and (Test-Path -LiteralPath $_ -PathType Container -ErrorAction SilentlyContinue) } |
        Select-Object -Unique)
}

function Get-WeChatUpdateCacheTargets {
    param([string[]]$ProfileRoots = @())

    @(Get-ProfileRoots -ConfiguredRoots $ProfileRoots | ForEach-Object {
        $path = Join-Path $_ 'AppData\Roaming\Tencent\xwechat\update'
        if (Test-Path -LiteralPath $path -PathType Container -ErrorAction SilentlyContinue) {
            [IO.Path]::GetFullPath($path)
        }
    } | Select-Object -Unique)
}

function Test-IsAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function New-WorldExecuteDenyRule {
    $world = [Security.Principal.SecurityIdentifier]::new(
        [Security.Principal.WellKnownSidType]::WorldSid,
        $null
    )
    [Security.AccessControl.FileSystemAccessRule]::new(
        $world,
        [Security.AccessControl.FileSystemRights]::ExecuteFile,
        [Security.AccessControl.InheritanceFlags]::None,
        [Security.AccessControl.PropagationFlags]::None,
        [Security.AccessControl.AccessControlType]::Deny
    )
}

function New-WorldUpdateCacheDenyRule {
    $world = [Security.Principal.SecurityIdentifier]::new(
        [Security.Principal.WellKnownSidType]::WorldSid,
        $null
    )
    $rights = [Security.AccessControl.FileSystemRights]::Write -bor
        [Security.AccessControl.FileSystemRights]::Delete -bor
        [Security.AccessControl.FileSystemRights]::DeleteSubdirectoriesAndFiles
    $inheritance = [Security.AccessControl.InheritanceFlags]::ContainerInherit -bor
        [Security.AccessControl.InheritanceFlags]::ObjectInherit
    [Security.AccessControl.FileSystemAccessRule]::new(
        $world,
        $rights,
        $inheritance,
        [Security.AccessControl.PropagationFlags]::None,
        [Security.AccessControl.AccessControlType]::Deny
    )
}

function Add-UpdateExecutionBlock {
    param([Parameter(Mandatory = $true)][string]$ProgramPath)

    $acl = Get-Acl -LiteralPath $ProgramPath
    $rule = New-WorldExecuteDenyRule
    $worldSid = $rule.IdentityReference.Value
    $alreadyBlocked = @($acl.Access | Where-Object {
        $_.AccessControlType -eq [Security.AccessControl.AccessControlType]::Deny -and
        $_.IdentityReference.Value -eq $worldSid -and
        (($_.FileSystemRights -band [Security.AccessControl.FileSystemRights]::ExecuteFile) -ne 0)
    }).Count -gt 0

    if (-not $alreadyBlocked) {
        $acl.AddAccessRule($rule)
        Set-Acl -LiteralPath $ProgramPath -AclObject $acl
    }
    $alreadyBlocked -eq $false
}

function Add-UpdateCacheBlock {
    param([Parameter(Mandatory = $true)][string]$CachePath)

    $acl = Get-Acl -LiteralPath $CachePath
    $originalSddl = $acl.Sddl
    $rule = New-WorldUpdateCacheDenyRule
    $worldSid = $rule.IdentityReference.Value
    $alreadyBlocked = @($acl.Access | Where-Object {
        $_.AccessControlType -eq [Security.AccessControl.AccessControlType]::Deny -and
        $_.IdentityReference.Value -eq $worldSid -and
        (($_.FileSystemRights -band [Security.AccessControl.FileSystemRights]::Write) -ne 0)
    }).Count -gt 0
    if (-not $alreadyBlocked) {
        $acl.AddAccessRule($rule)
        Set-Acl -LiteralPath $CachePath -AclObject $acl
    }
    [pscustomobject]@{
        Path = $CachePath
        OriginalSddl = $originalSddl
        AclChanged = ($alreadyBlocked -eq $false)
    }
}

function Invoke-WeChatUpdateBlock {
    param(
        [Parameter(Mandatory = $true)][string]$InstallRoot,
        [Parameter(Mandatory = $true)][string]$StatePath
    )

    if (-not (Test-IsAdministrator)) {
        throw 'Run this script from an elevated PowerShell window.'
    }

    $plan = @(Get-WeChatUpdateBlockPlan -InstallRoot $InstallRoot)
    if ($plan.Count -eq 0) {
        throw "No WeixinUpdate.exe found below $InstallRoot"
    }

    Stop-Process -Name 'WeixinUpdate' -Force -ErrorAction SilentlyContinue
    $entries = @()
    foreach ($item in $plan) {
        $aclChanged = Add-UpdateExecutionBlock -ProgramPath $item.ProgramPath
        $firewallCreated = $false
        if (Get-Command -Name 'New-NetFirewallRule' -ErrorAction SilentlyContinue) {
            $existing = @(Get-NetFirewallRule -DisplayName $item.RuleName -ErrorAction SilentlyContinue)
            if ($existing.Count -eq 0) {
                New-NetFirewallRule -DisplayName $item.RuleName -Direction Outbound -Action Block -Program $item.ProgramPath -Profile Any -Description 'OpenClaw WeChat AI: block WeixinUpdate.exe; undo with unblock_wechat_updates.ps1' | Out-Null
                $firewallCreated = $true
            }
        }
        $entries += [pscustomobject]@{
            ProgramPath = $item.ProgramPath
            RuleName = $item.RuleName
            AclChanged = $aclChanged
            FirewallCreated = $firewallCreated
        }
        Write-Output "Blocked: $($item.ProgramPath)"
    }

    $cacheEntries = @()
    foreach ($cachePath in @(Get-WeChatUpdateCacheTargets -ProfileRoots $ProfileRoots)) {
        $cacheEntries += Add-UpdateCacheBlock -CachePath $cachePath
        Write-Output "Blocked update cache: $cachePath"
    }

    $state = [pscustomobject]@{
        Version = 2
        Active = $true
        InstallRoot = $InstallRoot
        CreatedAt = [DateTime]::UtcNow.ToString('o')
        Entries = $entries
        CacheEntries = $cacheEntries
    }
    $parent = Split-Path -Parent $StatePath
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
    $state | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $StatePath -Encoding UTF8
    Write-Output "State: $StatePath"
}

if ($Apply) {
    if ($PSCmdlet.ShouldProcess($InstallRoot, 'Block WeixinUpdate.exe execution and outbound traffic')) {
        Invoke-WeChatUpdateBlock -InstallRoot $InstallRoot -StatePath $StatePath
    }
}
else {
    Get-WeChatUpdateBlockPlan -InstallRoot $InstallRoot | Format-Table -AutoSize
    Get-WeChatUpdateCacheTargets -ProfileRoots $ProfileRoots | ForEach-Object {
        Write-Output "Update cache: $_"
    }
    Write-Output 'Dry run only. Add -Apply from an elevated PowerShell window to enforce the block.'
}
