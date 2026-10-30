$ErrorActionPreference = 'Stop'
$here = Split-Path $PSScriptRoot -Parent
$source = Join-Path $here 'controller\BotSwitch.cs'
$build = Join-Path $here 'build_bot_switch.ps1'
$executable = Join-Path $here 'BotSwitch.exe'
if (-not (Test-Path -LiteralPath $source)) { throw 'BotSwitch.cs is missing' }
if (-not (Test-Path -LiteralPath $build)) { throw 'build_bot_switch.ps1 is missing' }

$sourceText = Get-Content -LiteralPath $source -Raw -Encoding UTF8
foreach ($label in @('启动整个程序', '关闭整个程序', '微信 AI 机器人')) {
    if ($sourceText -notmatch [regex]::Escape($label)) { throw "GUI is missing label: $label" }
}
if ($sourceText -notmatch 'CreateNoWindow\s*=\s*true' -or $sourceText -notmatch 'UseShellExecute\s*=\s*false') {
    throw 'GUI child PowerShell must run without a console window'
}
if ($sourceText -notmatch 'current\.AnyActive\s*\?\s*"Stop"\s*:\s*"Start"') {
    throw 'Toggle action must match whether any bot service is active'
}

& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $build
if ($LASTEXITCODE -ne 0) { throw "GUI build failed with exit code $LASTEXITCODE" }
if (-not (Test-Path -LiteralPath $executable)) { throw 'BotSwitch.exe was not produced' }

$reader = [IO.BinaryReader]::new([IO.File]::OpenRead($executable))
try {
    if ($reader.ReadUInt16() -ne 0x5A4D) { throw 'Executable has no DOS header' }
    $reader.BaseStream.Position = 0x3c
    $peOffset = $reader.ReadInt32()
    $reader.BaseStream.Position = $peOffset
    if ($reader.ReadUInt32() -ne 0x00004550) { throw 'Executable has no PE signature' }
    $reader.BaseStream.Position = $peOffset + 24 + 68
    if ($reader.ReadUInt16() -ne 2) { throw 'Executable is not built for the Windows GUI subsystem' }
} finally { $reader.Dispose() }

$process = Start-Process -FilePath $executable -PassThru
try {
    $deadline = (Get-Date).AddSeconds(15)
    do {
        Start-Sleep -Milliseconds 250
        $process.Refresh()
        if ($process.HasExited) { throw 'GUI exited before displaying its window' }
    } while ($process.MainWindowHandle -eq [IntPtr]::Zero -and (Get-Date) -lt $deadline)
    if ($process.MainWindowHandle -eq [IntPtr]::Zero) { throw 'GUI did not display a window' }
} finally {
    if (-not $process.HasExited) {
        [void]$process.CloseMainWindow()
        if (-not $process.WaitForExit(4000)) { Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue }
    }
}
Write-Output 'bot switch GUI build and window: PASS'
