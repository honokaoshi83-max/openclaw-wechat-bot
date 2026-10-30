$ErrorActionPreference = 'Stop'

$script = Join-Path $PSScriptRoot '..\block_wechat_updates.ps1'
if (-not (Test-Path -LiteralPath $script)) {
    throw "Expected update blocker script: $script"
}

$tempRoot = Join-Path $env:TEMP ("wechat-update-block-test-" + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Force -Path (Join-Path $tempRoot '4.1.10.27') | Out-Null
New-Item -ItemType File -Force -Path (Join-Path $tempRoot '4.1.10.27\WeixinUpdate.exe') | Out-Null
New-Item -ItemType File -Force -Path (Join-Path $tempRoot 'Weixin.exe') | Out-Null
$tempProfile = Join-Path $tempRoot 'UserProfile'
$tempUpdate = Join-Path $tempProfile 'AppData\Roaming\Tencent\xwechat\update'
New-Item -ItemType Directory -Force -Path $tempUpdate | Out-Null

try {
    . $script -InstallRoot $tempRoot
    $targets = @(Get-WeChatUpdateTargets -InstallRoot $tempRoot)
    if ($targets.Count -ne 1) {
        throw "Expected exactly one updater target, got $($targets.Count)"
    }
    if ([IO.Path]::GetFileName($targets[0]) -ne 'WeixinUpdate.exe') {
        throw "Unexpected target: $($targets[0])"
    }
    $plan = @(Get-WeChatUpdateBlockPlan -InstallRoot $tempRoot)
    if ($plan.Count -ne 1 -or $plan[0].ProgramPath -ne $targets[0]) {
        throw 'Block plan did not preserve the updater path'
    }
    $cacheTargets = @(Get-WeChatUpdateCacheTargets -ProfileRoots @($tempProfile))
    if ($cacheTargets.Count -ne 1 -or $cacheTargets[0] -ne [IO.Path]::GetFullPath($tempUpdate)) {
        throw 'Update cache discovery did not preserve the profile path'
    }
    Write-Output 'PASS: update blocker executable and cache target discovery'
}
finally {
    Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
}
