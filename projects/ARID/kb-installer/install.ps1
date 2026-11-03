# One-click installer for the "Knowledge Base" (kbagent) agent preset.
# Usage:
#   .\install.ps1                 # install with defaults (KB at %USERPROFILE%\kb)
#   .\install.ps1 -KbRoot D:\kb   # install the KB at a custom location
# Or simply double-click install.bat.
param([string]$KbRoot = "")

$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path

if ([string]::IsNullOrWhiteSpace($KbRoot)) { $KbRoot = Join-Path $env:USERPROFILE 'kb' }
$KbRoot = $KbRoot.TrimEnd('\').TrimEnd('/')

$dshHome = if ($env:DSH_HOME) { $env:DSH_HOME } else { Join-Path $env:USERPROFILE '.dsh' }
$presetDir = Join-Path $dshHome '.agent-presets\kbagent'

Write-Host ''
Write-Host '=============================================='
Write-Host ' KB Agent preset installer'
Write-Host '=============================================='

# 1) knowledge base -----------------------------------------------------------
Write-Host '[1/4] Installing knowledge base ->' $KbRoot
New-Item -ItemType Directory -Force -Path $KbRoot | Out-Null
$kbSrc = Join-Path $here 'kb'
if (Test-Path $kbSrc) {
    Copy-Item -Path (Join-Path $kbSrc '*') -Destination $KbRoot -Recurse -Force
}

# 2) agent preset -------------------------------------------------------------
Write-Host '[2/4] Installing agent preset ->' $presetDir
New-Item -ItemType Directory -Force -Path $presetDir | Out-Null
$kbRootJs = $KbRoot.Replace('\', '/')
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
foreach ($f in @('agent.cordis.yml', 'kbtool.cjs', 'preset.yml')) {
    $src = Join-Path $here ('preset\' + $f)
    $content = Get-Content -Path $src -Raw -Encoding UTF8
    $content = $content.Replace('__KB_ROOT__', $kbRootJs)
    [System.IO.File]::WriteAllText((Join-Path $presetDir $f), $content, $utf8NoBom)
}

# 3) runtime check ------------------------------------------------------------
Write-Host '[3/4] Checking runtime...'
$node = Get-Command node.exe -ErrorAction SilentlyContinue
if (-not $node) {
    Write-Host '  [!] Node.js not found. Install Node.js 20+ from https://nodejs.org , then rerun this installer.'
} else {
    Write-Host '  [OK] Node.js found:' $node.Source
    $dsh = Get-Command dsh -ErrorAction SilentlyContinue
    if ($dsh) {
        Write-Host '  [OK] DSH found:' $dsh.Source
    } else {
        Write-Host '  [..] DSH not found. Installing globally via npm (needs network, may take a few minutes)...'
        $npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
        if ($npm) {
            & npm.cmd install -g @deepseek-ai/dsh
        } elseif (Test-Path "$env:ProgramFiles\nodejs\node_modules\npm\bin\npm-cli.js") {
            & node "$env:ProgramFiles\nodejs\node_modules\npm\bin\npm-cli.js" install -g @deepseek-ai/dsh
        } else {
            Write-Host '  [!] npm not found. Run it manually: npm install -g @deepseek-ai/dsh'
        }
        if ($LASTEXITCODE -ne 0) {
            Write-Host '  [!] npm install failed. Run it manually: npm install -g @deepseek-ai/dsh'
        } else {
            Write-Host '  [OK] DSH installed.'
        }
    }
}

# 4) done ---------------------------------------------------------------------
Write-Host '[4/4] Done.'
Write-Host ''
Write-Host 'Knowledge base location :' $KbRoot
Write-Host 'Preset installed at     :' $presetDir
Write-Host ''
Write-Host 'Next steps:'
Write-Host '  1. Start DSH (run the dsh command, or your usual launcher)'
Write-Host '  2. Open the web UI, create a NEW session, choose preset "kbagent" (Knowledge Base)'
Write-Host '  3. Ask: "查一下知识库里的 XXX"'
Write-Host ''
Read-Host 'Press Enter to exit'
