# build-c.ps1 -- 用 NDK 交叉编译 plcollect（aarch64 + arm 通用）
# 用法: powershell -ExecutionPolicy Bypass -File build-c.ps1
# 产物: ..\c\out\plcollect (arm64)  ..\c\out\plcollect-arm (armeabi-v7a)

$ErrorActionPreference = 'Stop'

$SDK = 'D:\CODEX\tools\android-sdk'
$NDK = Join-Path $SDK 'ndk\25.2.9519653'
$env:TMP = 'D:\CODEX\tmp'
$env:TEMP = 'D:\CODEX\tmp'

$Src  = 'D:\CODEX\w\powerlens\c\plcollect.c'
$Out  = 'D:\CODEX\w\powerlens\c\out'

if (-not (Test-Path $Src)) { throw "缺少源文件: $Src" }
if (-not (Test-Path $NDK)) { throw "缺少 NDK: $NDK" }
New-Item -ItemType Directory -Force -Path $Out | Out-Null

# NDK 自带的 clang wrapper
$tc64 = Join-Path $NDK 'toolchains\llvm\prebuilt\windows-x86_64\bin\aarch64-linux-android24-clang.cmd'
$tc32 = Join-Path $NDK 'toolchains\llvm\prebuilt\windows-x86_64\bin\armv7a-linux-androideabi21-clang.cmd'
$strip64 = Join-Path $NDK 'toolchains\llvm\prebuilt\windows-x86_64\bin\llvm-strip.exe'

foreach ($t in @($tc64, $tc32, $strip64)) {
    if (-not (Test-Path $t)) { throw "缺少编译器: $t" }
}

Write-Host '[1/2] arm64-v8a' -ForegroundColor Cyan
$arm64 = Join-Path $Out 'plcollect'
& $tc64 -O2 -Os -s -static-libgcc -fPIE -pie -Wall -Wextra `
    -o $arm64 $Src 2>&1 | ForEach-Object { Write-Host "  $_" }
if ($LASTEXITCODE -ne 0) { throw 'arm64 编译失败' }

Write-Host '[2/2] armeabi-v7a' -ForegroundColor Cyan
$arm32 = Join-Path $Out 'plcollect-arm'
& $tc32 -O2 -Os -s -static-libgcc -fPIE -pie -Wall -Wextra `
    -o $arm32 $Src 2>&1 | ForEach-Object { Write-Host "  $_" }
if ($LASTEXITCODE -ne 0) { throw 'arm32 编译失败' }

Write-Host ''
foreach ($f in @($arm64, $arm32)) {
    $fi = Get-Item $f
    Write-Host ("{0}  {1:N0} 字节" -f $fi.Name, $fi.Length) -ForegroundColor Green
}
Write-Host ''
Write-Host '推送: adb push plcollect /data/local/tmp/ && chmod 755' -ForegroundColor Green