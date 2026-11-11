param(
    [Parameter(Mandatory = $true)]
    [string]$NdkRoot
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$compiler = Join-Path $NdkRoot 'toolchains/llvm/prebuilt/windows-x86_64/bin/aarch64-linux-android21-clang.cmd'
$source = Join-Path $projectRoot 'native/cpu_mix_bench.c'
$outputDir = Join-Path $projectRoot 'build'
$output = Join-Path $outputDir 'cpu_mix_bench'

if (-not (Test-Path $compiler)) { throw "Android NDK compiler not found: $compiler" }
if (-not (Test-Path $source)) { throw "Benchmark source not found: $source" }
New-Item -ItemType Directory -Force -Path $outputDir | Out-Null

& $compiler -O3 -std=c11 -Wall -Wextra -Werror -pthread $source -o $output
if ($LASTEXITCODE -ne 0) { throw "NDK compile failed with exit code $LASTEXITCODE" }
Write-Output "Built: $output"
