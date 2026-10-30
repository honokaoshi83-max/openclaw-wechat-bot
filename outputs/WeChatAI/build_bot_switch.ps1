$ErrorActionPreference = 'Stop'
$source = Join-Path $PSScriptRoot 'controller\BotSwitch.cs'
$output = Join-Path $PSScriptRoot 'BotSwitch.exe'
$compilerCandidates = @(
    (Join-Path $env:SystemRoot 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'),
    (Join-Path $env:SystemRoot 'Microsoft.NET\Framework\v4.0.30319\csc.exe')
)
$compiler = $compilerCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $compiler) { throw '.NET Framework C# compiler was not found' }
if (-not (Test-Path -LiteralPath $source)) { throw "GUI source is missing: $source" }

Remove-Item -LiteralPath $output -Force -ErrorAction SilentlyContinue
& $compiler /nologo /target:winexe /platform:anycpu /optimize+ `
    /reference:System.Windows.Forms.dll /reference:System.Drawing.dll /reference:System.Web.Extensions.dll `
    "/out:$output" $source
if ($LASTEXITCODE -ne 0) { throw "C# compiler failed with exit code $LASTEXITCODE" }
if (-not (Test-Path -LiteralPath $output)) { throw 'Compiler returned success but BotSwitch.exe was not created' }
Write-Output "Built GUI executable: $output"
