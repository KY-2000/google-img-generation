$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$out = Join-Path $root 'output\stock_manager'
$exe = Join-Path $out 'Adobe Stock Batch Manager.exe'
$icon = Join-Path $root 'config\stock_manager.ico'
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path -LiteralPath $compiler)) {
    throw 'Windows .NET Framework compiler not found.'
}
New-Item -ItemType Directory -Path $out -Force | Out-Null
& $compiler /nologo /target:winexe /reference:System.Windows.Forms.dll "/win32icon:$icon" "/out:$exe" (
    Join-Path $root 'tools\stock_manager_launcher.cs'
)
if ($LASTEXITCODE -ne 0) { throw "Launcher compilation failed ($LASTEXITCODE)." }
Write-Output $exe
