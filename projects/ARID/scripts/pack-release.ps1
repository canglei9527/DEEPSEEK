<#
.SYNOPSIS
  ARID dual-model release packager (PowerShell 5.1 compatible, pure ASCII).

.DESCRIPTION
  Reads the version from package.json, assembles dist/ARID-dualmodel-v<ver>/
  (workspace + preset + config-gui + install.bat/configure.bat/start-arid.bat
  + the Chinese README), then compresses it into dist/ARID-dualmodel-v<ver>.zip.

  Run from the repository root:
    powershell -ExecutionPolicy Bypass -File scripts\pack-release.ps1

  NOTE: file content stays pure ASCII. Chinese strings are built at runtime from
  Unicode code points (see the $USAGE_NAME / $PACK_NAME constants). Do not paste
  non-ASCII bytes into this script.
#>

$ErrorActionPreference = 'Stop'

# Chinese file names built from code points so this source stays ASCII.
$USAGE_NAME = (-join [char[]](0x4f7f, 0x7528, 0x8bf4, 0x660e)) + '.md'
$PACK_NAME  = (-join [char[]](0x6253, 0x5305, 0x8bf4, 0x660e)) + '.md'

# ---------------------------------------------------------------
# 0) Detect repository root = parent of this script's folder
# ---------------------------------------------------------------
$here  = Split-Path -Parent $MyInvocation.MyCommand.Path
$repo  = Split-Path -Parent $here
Set-Location $repo

# ---------------------------------------------------------------
# 1) Version from package.json
# ---------------------------------------------------------------
if (-not (Test-Path 'package.json')) {
  Write-Host '[pack] ERROR: package.json not found in repo root.' -ForegroundColor Red
  exit 1
}
$pkg = Get-Content 'package.json' -Raw -Encoding UTF8 | ConvertFrom-Json
$ver = $null
if ($pkg) { $ver = $pkg.version }
if (-not $ver) { $ver = '0.0.0' }
Write-Host "[pack] ARID version: $ver"

# ---------------------------------------------------------------
# 2) Output dirs
# ---------------------------------------------------------------
$outRoot = Join-Path $repo 'dist'
$stage   = Join-Path $outRoot "ARID-dualmodel-v$ver"
if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
New-Item -ItemType Directory -Force -Path $stage | Out-Null

# ---------------------------------------------------------------
# 3) Workspace copy: exclude heavy / local-only paths
# ---------------------------------------------------------------
$ws = Join-Path $stage 'workspace'
New-Item -ItemType Directory -Force -Path $ws | Out-Null
# Exact-name exclusions.
$excludeExact = @('node_modules', 'dist', 'kb-installer', '.git', '.env',
             '_gui_test', 'dinosaur.c', 'dinosaur.exe', 'dragon.png', 'dragon.svg',
             'draw-dragon.ps1', '%TEMP%', 'config-gui', 'scripts',
             $USAGE_NAME, $PACK_NAME, 'install.bat', 'configure.bat',
             'feishu-worker')
# Prefix exclusions (wildcard): any ".aider*" artifact.
$excludePrefix = @('.aider')
Get-ChildItem -LiteralPath $repo -Force | ForEach-Object {
  $name = $_.Name
  $skip = $name -in $excludeExact
  if (-not $skip) {
    foreach ($p in $excludePrefix) {
      if ($name.StartsWith($p, [System.StringComparison]::Ordinal)) { $skip = $true; break }
    }
  }
  if ($skip) { return }
  Copy-Item -LiteralPath $_.FullName -Destination $ws -Recurse -Force
}
Write-Host '[pack] workspace files copied.'
# Post-clean: never ship credential baselines (may hold plaintext API keys).
Remove-Item -LiteralPath (Join-Path $ws 'tools\dual-model-gui\defaults\credentials.default.yaml') -Force -ErrorAction SilentlyContinue
Write-Host '[pack] credential baselines stripped from workspace copy.'

# ---------------------------------------------------------------
# 4) Preset files from the local agent-presets dir
# ---------------------------------------------------------------
$presetSrc = if ($env:DSH_HOME) {
  Join-Path $env:DSH_HOME '.agent-presets\arid-dualmodel'
} else {
  Join-Path $env:USERPROFILE '.dsh\.agent-presets\arid-dualmodel'
}
$presetDst = Join-Path $stage 'preset'
if (-not (Test-Path $presetSrc)) {
  Write-Host "[pack] ERROR: preset dir not found: $presetSrc" -ForegroundColor Red
  Write-Host '[pack] Expected: preset.yml, agent.cordis.yml, arid-dualmodel-ext6.cjs, arid-tavily.cjs'
  exit 1
}
Copy-Item -LiteralPath $presetSrc -Destination $presetDst -Recurse -Force
foreach ($f in @('preset.yml', 'agent.cordis.yml', 'arid-dualmodel-ext6.cjs', 'arid-tavily.cjs', 'kbtool.cjs')) {
  if (-not (Test-Path (Join-Path $presetDst $f))) {
    Write-Host "[pack] ERROR: preset file missing: $f" -ForegroundColor Red
    exit 1
  }
}
Write-Host '[pack] preset files copied (4).'

# ---------------------------------------------------------------
# 5) config-gui (server.mjs, index.html, vendor/)
# ---------------------------------------------------------------
$guiDst = Join-Path $stage 'config-gui'
New-Item -ItemType Directory -Force -Path $guiDst | Out-Null
Copy-Item -LiteralPath (Join-Path $repo 'config-gui\server.mjs') -Destination $guiDst -Force
Copy-Item -LiteralPath (Join-Path $repo 'config-gui\index.html') -Destination $guiDst -Force
Copy-Item -LiteralPath (Join-Path $repo 'config-gui\feishu-env.mjs') -Destination $guiDst -Force
if (-not (Test-Path 'config-gui\vendor\yaml\dist\index.js')) {
  Write-Host '[pack] ERROR: vendored yaml missing at config-gui\vendor\yaml\dist\index.js' -ForegroundColor Red
  Write-Host '[pack] Re-vendor: copy node_modules\yaml\dist and package.json into config-gui\vendor\yaml\.'
  exit 1
}
Copy-Item -LiteralPath (Join-Path $repo 'config-gui\vendor') -Destination $guiDst -Recurse -Force
Write-Host '[pack] config-gui copied.'

# ---------------------------------------------------------------
# 5b) feishu-worker (independent deploy unit)
#   package.json + .env.example + start-feishu.bat + src\feishu\*.ts + src\roles.ts
# ---------------------------------------------------------------
$fwSrc  = Join-Path $repo 'feishu-worker'
$fwDst  = Join-Path $stage 'feishu-worker'
$fwTsDst = Join-Path $fwDst 'src\feishu'
New-Item -ItemType Directory -Force -Path $fwDst | Out-Null
foreach ($f in @('package.json', '.env.example', 'start-feishu.bat')) {
  if (-not (Test-Path (Join-Path $fwSrc $f))) {
    Write-Host "[pack] ERROR: feishu-worker\$f missing." -ForegroundColor Red
    exit 1
  }
  Copy-Item -LiteralPath (Join-Path $fwSrc $f) -Destination (Join-Path $fwDst $f) -Force
}
New-Item -ItemType Directory -Force -Path $fwTsDst | Out-Null
$fwTsFiles = @('bot.ts', 'text.ts', 'worker-entry.ts', 'worker-main.ts',
               'lark-transport.ts', 'dsh-executor.ts', 'env.ts')
foreach ($f in $fwTsFiles) {
  $src = Join-Path $repo "src\feishu\$f"
  if (-not (Test-Path $src)) {
    Write-Host "[pack] ERROR: src\feishu\$f missing." -ForegroundColor Red
    exit 1
  }
  Copy-Item -LiteralPath $src -Destination $fwTsDst -Force
}
# src\roles.ts (needed by worker-*.ts / env.ts path resolution)
if (-not (Test-Path (Join-Path $repo 'src\roles.ts'))) {
  Write-Host '[pack] ERROR: src\roles.ts missing.' -ForegroundColor Red
  exit 1
}
Copy-Item -LiteralPath (Join-Path $repo 'src\roles.ts') -Destination (Join-Path $fwDst 'src') -Force
# Validate: package.json + all ts files present.
Write-Host '[pack] feishu-worker files copied:'
foreach ($f in $(@($fwTsFiles) + @('roles.ts'))) {
  $p = Join-Path $fwDst "src\feishu\$f"
  if ($f -eq 'roles.ts') { $p = Join-Path $fwDst "src\roles.ts" }
  if (-not (Test-Path $p)) {
    Write-Host "[pack] ERROR: feishu-worker staged file missing: $p" -ForegroundColor Red
    exit 1
  }
  Write-Host "[pack]   + $p"
}
if (-not (Test-Path (Join-Path $fwDst 'package.json'))) {
  Write-Host '[pack] ERROR: feishu-worker\package.json missing after staging.' -ForegroundColor Red
  exit 1
}
Write-Host '[pack] feishu-worker staged.'

# ---------------------------------------------------------------
# 6) Launchers + README
# ---------------------------------------------------------------
Copy-Item -LiteralPath (Join-Path $repo 'start-arid.bat') -Destination $stage -Force
Copy-Item -LiteralPath (Join-Path $repo 'install.bat')      -Destination $stage -Force
Copy-Item -LiteralPath (Join-Path $repo 'configure.bat')    -Destination $stage -Force
Write-Host '[pack] launcher bat files copied.'

$usageSrc = Join-Path $repo $USAGE_NAME
if (-not (Test-Path $usageSrc)) {
  Write-Host '[pack] ERROR: README not present at repo root.' -ForegroundColor Red
  exit 1
}
Copy-Item -LiteralPath $usageSrc -Destination (Join-Path $stage $USAGE_NAME) -Force
Write-Host '[pack] README copied.'

# ---------------------------------------------------------------
# 7) Compress
# ---------------------------------------------------------------
$zip = Join-Path $outRoot "ARID-dualmodel-v$ver.zip"
if (Test-Path $zip) { Remove-Item $zip -Force }
Compress-Archive -Path $stage -DestinationPath $zip -CompressionLevel Optimal
$size = (Get-Item $zip).Length
Write-Host ''
Write-Host '[pack] DONE.'
Write-Host "[pack] package dir : $stage"
Write-Host "[pack] zip          : $zip"
Write-Host ([string]::Format('[pack] zip size     : {0:N1} KB', $size / 1KB))
