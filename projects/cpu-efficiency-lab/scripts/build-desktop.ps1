param(
    [string]$OutputParent = ""
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
if (-not $OutputParent) {
    $OutputParent = Join-Path $projectRoot 'dist'
}
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw 'Python is not available on PATH.'
}
if (-not (Test-Path -LiteralPath $OutputParent)) {
    New-Item -ItemType Directory -Path $OutputParent | Out-Null
}
$buildName = 'desktop-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [guid]::NewGuid().ToString('N').Substring(0, 8)
$buildRoot = Join-Path $OutputParent $buildName
New-Item -ItemType Directory -Path $buildRoot | Out-Null
$sourceRoot = Join-Path $projectRoot 'src'
$entry = Join-Path $sourceRoot 'cpu_efficiency_lab\desktop.py'
python -m PyInstaller --noconfirm --onefile --console --name CPU-Efficiency-Lab `
    --paths $sourceRoot --distpath $buildRoot --workpath (Join-Path $buildRoot 'work') `
    --specpath $buildRoot $entry
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE. Output: $buildRoot"
}
Write-Output "Desktop executable: $(Join-Path $buildRoot 'CPU-Efficiency-Lab.exe')"
