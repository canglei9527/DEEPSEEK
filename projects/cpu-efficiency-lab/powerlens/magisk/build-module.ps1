# build-module.ps1 -- 构建 PowerLens Magisk/KernelSU 模块 zip
# 用法: powershell -ExecutionPolicy Bypass -File build-module.ps1
# 产物: magisk/out/powerlens-module-vX.zip
#
# 设计原则：
#   * 不依赖 Gradle / 不依赖 Magisk 工具链，纯 zip 打包
#   * 二进制从 module/bin/ 取
#   * 所有 .sh 必须是 LF 换行 —— CRLF 会让 /system/bin/sh 报 syntax error

$ErrorActionPreference = 'Stop'

$Root    = $PSScriptRoot
$Mod     = Join-Path $Root 'module'
$OutDir  = Join-Path $Root 'out'
$propTxt = [IO.File]::ReadAllText((Join-Path $Mod 'module.prop'))
$m = [regex]::Match($propTxt, '(?m)^version=(.+)$')
$Version = if ($m.Success) { $m.Groups[1].Value.Trim() } else { 'unknown' }
$ZipName = "powerlens-module-$Version.zip"
$ZipPath = Join-Path $OutDir $ZipName

function Pad($s, $n) {
    $s = [string]$s
    if ($s.Length -ge $n) { return $s }
    return $s + (' ' * ($n - $s.Length))
}

Write-Host "[1/5] check files" -ForegroundColor Cyan
$required = @(
    'module.prop',
    'customize.sh',
    'post-fs-data.sh',
    'service.sh',
    'uninstall.sh',
    'bin/plcollect',
    'bin/pldims',
    'bin/plc-daemon.sh',
    'bin/40-plc.sh',
    'META-INF/com/google/android/update-binary',
    'META-INF/com/google/android/updater-script'
)
foreach ($f in $required) {
    $p = Join-Path $Mod ($f -replace '/', '\')
    if (-not (Test-Path $p)) { throw "missing file: $f" }
    $len = (Get-Item $p).Length
    Write-Host ("  OK  " + (Pad $f 48) + ("{0,8}" -f $len) + " B")
}

Write-Host "[2/5] normalize line endings to LF" -ForegroundColor Cyan
$shFiles = @(
    'customize.sh', 'post-fs-data.sh', 'service.sh', 'uninstall.sh',
    'bin/plc-daemon.sh', 'bin/40-plc.sh',
    'META-INF/com/google/android/update-binary'
)
foreach ($f in $shFiles) {
    $p = Join-Path $Mod ($f -replace '/', '\')
    $raw = [IO.File]::ReadAllText($p)
    $hadCrlf = $raw.Contains("`r`n")
    $lf = $raw -replace "`r`n", "`n"
    $bytes = [IO.File]::ReadAllBytes($p)
    $hadBom = ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF)
    if ($hadCrlf -or $hadBom) {
        if ($lf.Length -eq 0) { $lf = $raw }
        [IO.File]::WriteAllText($p, $lf, [Text.UTF8Encoding]::new($false))
        $notes = @()
        if ($hadCrlf) { $notes += 'CRLF->LF' }
        if ($hadBom)  { $notes += 'strip BOM' }
        Write-Host ("  fixed " + ($notes -join ', ') + " : " + $f)
    }
}

Write-Host "[3/5] verify binary architecture (aarch64)" -ForegroundColor Cyan
foreach ($b in @('plcollect', 'pldims')) {
    $p = Join-Path $Mod ("bin\" + $b)
    $bytes = [IO.File]::ReadAllBytes($p)
    if ($bytes[0] -ne 0x7F -or $bytes[1] -ne 0x45 -or $bytes[2] -ne 0x4C -or $bytes[3] -ne 0x46) {
        throw "$b is not an ELF file"
    }
    $machine = $bytes[18] -bor ($bytes[19] -shl 8)
    if ($machine -ne 0xB7) {
        throw ("$b wrong arch (e_machine=0x{0:X}), need aarch64" -f $machine)
    }
    Write-Host ("  OK  " + (Pad $b 12) + " aarch64, " + $bytes.Length + " B")
}

Write-Host "[4/5] pack zip" -ForegroundColor Cyan
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
if (Test-Path $ZipPath) { Remove-Item $ZipPath -Force }

Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem

$zip = [System.IO.Compression.ZipFile]::Open($ZipPath, [System.IO.Compression.ZipArchiveMode]::Create)
try {
    $files = Get-ChildItem $Mod -Recurse -File | ForEach-Object {
        $rel = $_.FullName.Substring($Mod.Length + 1).Replace('\', '/')
        [PSCustomObject]@{ Path = $_.FullName; Rel = $rel }
    }
    # META-INF 优先，其余按名排序
    $files = $files | Sort-Object @{ Expression = { if ($_.Rel -like 'META-INF/*') { 0 } else { 1 } } }, Rel

    foreach ($f in $files) {
        $entry = $zip.CreateEntry($f.Rel, [System.IO.Compression.CompressionLevel]::Optimal)
        $es = $entry.Open()
        $fs = [IO.File]::OpenRead($f.Path)
        try { $fs.CopyTo($es) } finally { $fs.Close(); $es.Close() }
        Write-Host ("  + " + $f.Rel)
    }
} finally {
    $zip.Dispose()
}

Write-Host "[5/5] verify" -ForegroundColor Cyan
$zi = Get-Item $ZipPath
Write-Host ("  output: " + $ZipPath) -ForegroundColor Green
Write-Host ("  size  : " + $zi.Length + " bytes (" + [math]::Round($zi.Length / 1KB, 1) + " KB)") -ForegroundColor Green

$z2 = [System.IO.Compression.ZipFile]::OpenRead($ZipPath)
try {
    Write-Host "  contents:" -ForegroundColor Green
    foreach ($e in $z2.Entries) {
        Write-Host ("    " + (Pad $e.FullName 50) + ("{0,10}" -f $e.Length) + " B")
    }
} finally { $z2.Dispose() }