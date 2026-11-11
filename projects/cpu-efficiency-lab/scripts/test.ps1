$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$env:PYTHONPATH = (Join-Path $projectRoot 'src') + ';' + (Join-Path $projectRoot 'scripts')
Push-Location $projectRoot
try {
    python -m unittest discover -s tests -t . @args
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}