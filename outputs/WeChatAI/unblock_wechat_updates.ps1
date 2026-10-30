[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$StatePath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ([string]::IsNullOrWhiteSpace($StatePath)) {
    $StatePath = Join-Path $PSScriptRoot 'wechat_update_block_state.json'
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

if (-not (Test-Path -LiteralPath $StatePath -PathType Leaf)) {
    Write-Output "No active update block state: $StatePath"
    exit 0
}
if (-not (Test-IsAdministrator)) {
    throw 'Run this script from an elevated PowerShell window.'
}

$state = Get-Content -LiteralPath $StatePath -Raw | ConvertFrom-Json
if (-not $state.Active) {
    Write-Output 'Update block is already inactive.'
    exit 0
}

if ($PSCmdlet.ShouldProcess($StatePath, 'Remove WeixinUpdate execution and firewall blocks')) {
    foreach ($entry in @($state.Entries)) {
        if (Get-Command -Name 'Remove-NetFirewallRule' -ErrorAction SilentlyContinue) {
            Remove-NetFirewallRule -DisplayName $entry.RuleName -ErrorAction SilentlyContinue
        }
        if ($entry.AclChanged -and (Test-Path -LiteralPath $entry.ProgramPath -PathType Leaf)) {
            $acl = Get-Acl -LiteralPath $entry.ProgramPath
            $acl.RemoveAccessRuleSpecific((New-WorldExecuteDenyRule))
            Set-Acl -LiteralPath $entry.ProgramPath -AclObject $acl
        }
        Write-Output "Unblocked: $($entry.ProgramPath)"
    }
    if ($state.PSObject.Properties.Name -contains 'CacheEntries') {
        foreach ($entry in @($state.CacheEntries)) {
            if ($entry.AclChanged -and (Test-Path -LiteralPath $entry.Path -PathType Container)) {
                $acl = Get-Acl -LiteralPath $entry.Path
                $acl.SetSecurityDescriptorSddlForm($entry.OriginalSddl)
                Set-Acl -LiteralPath $entry.Path -AclObject $acl
            }
            Write-Output "Unblocked update cache: $($entry.Path)"
        }
    }
    $state.Active = $false
    $state.RemovedAt = [DateTime]::UtcNow.ToString('o')
    $state | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $StatePath -Encoding UTF8
    Write-Output "State retained (inactive): $StatePath"
}
