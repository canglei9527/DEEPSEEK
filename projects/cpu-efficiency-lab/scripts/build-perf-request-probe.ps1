param(
    [Parameter(Mandatory = $true)]
    [string]$NdkRoot
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$compiler = Join-Path $NdkRoot 'toolchains/llvm/prebuilt/windows-x86_64/bin/aarch64-linux-android21-clang.cmd'
$source = Join-Path $projectRoot 'native/perf_request_probe.c'
$outputDir = Join-Path $projectRoot 'build'
$output = Join-Path $outputDir 'perf_request_probe'

if (-not (Test-Path $compiler)) { throw "Android NDK compiler not found: $compiler" }
if (-not (Test-Path $source)) { throw "Probe source not found: $source" }
New-Item -ItemType Directory -Force -Path $outputDir | Out-Null
& $compiler -O2 -std=c11 -Wall -Wextra -Werror $source -o $output -ldl
if ($LASTEXITCODE -ne 0) { throw "NDK compile failed with exit code $LASTEXITCODE" }
Write-Output "Built: $output"
