Set-StrictMode -Version Latest

function Get-BotBootIdentity {
    $os = Get-CimInstance -ClassName Win32_OperatingSystem -ErrorAction Stop
    if (-not $os.LastBootUpTime) { throw 'Windows did not report its last boot time' }
    return ([datetime]$os.LastBootUpTime).ToUniversalTime().ToString(
        'o', [Globalization.CultureInfo]::InvariantCulture)
}

function Get-BotDisabledMarkerPath([string]$RuntimeDirectory) {
    return Join-Path $RuntimeDirectory 'bot_disabled.json'
}

function Test-BotDisabledThisBoot([string]$RuntimeDirectory) {
    $markerPath = Get-BotDisabledMarkerPath $RuntimeDirectory
    if (-not (Test-Path -LiteralPath $markerPath -PathType Leaf)) { return $false }
    try {
        $marker = Get-Content -LiteralPath $markerPath -Raw -Encoding UTF8 | ConvertFrom-Json -ErrorAction Stop
        if (-not $marker.boot_identity) { return $false }
        return [string]::Equals([string]$marker.boot_identity, (Get-BotBootIdentity),
            [StringComparison]::Ordinal)
    } catch {
        # Invalid state must not block the pre-existing autostart path.
        return $false
    }
}

function Set-BotDisabledThisBoot([string]$RuntimeDirectory) {
    New-Item -ItemType Directory -Force -Path $RuntimeDirectory | Out-Null
    $markerPath = Get-BotDisabledMarkerPath $RuntimeDirectory
    $temporaryPath = "$markerPath.$PID.tmp"
    $marker = [ordered]@{ boot_identity = Get-BotBootIdentity }
    try {
        $json = ConvertTo-Json -InputObject $marker -Compress
        Set-Content -LiteralPath $temporaryPath -Value $json -Encoding UTF8
        Move-Item -LiteralPath $temporaryPath -Destination $markerPath -Force
    } finally {
        Remove-Item -LiteralPath $temporaryPath -Force -ErrorAction SilentlyContinue
    }
}

function Clear-BotDisabledThisBoot([string]$RuntimeDirectory) {
    $markerPath = Get-BotDisabledMarkerPath $RuntimeDirectory
    Remove-Item -LiteralPath $markerPath -Force -ErrorAction SilentlyContinue
}

Export-ModuleMember -Function Get-BotBootIdentity, Test-BotDisabledThisBoot,
    Set-BotDisabledThisBoot, Clear-BotDisabledThisBoot
