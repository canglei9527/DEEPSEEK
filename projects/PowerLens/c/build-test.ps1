# build-test.ps1 —— 编译 pldims 解析层回归单测
#
# ===== 为什么需要主机/设备单测 =====
#   这轮修掉的 9 个 bug 全都【不报错】：程序正常跑完、正常写文件、
#   数字看起来完全合法，只有拿去和 dumpsys 原文对才发现错了。
#   最典型的是 parse_dur 越界：fg 报 6h37m（真值 43m42s），虚高 9 倍，
#   界面上就是一条平平无奇的"耗电最多"记录，肉眼完全看不出来。
#   靠线上数据也没法判定 —— dumpsys 每次重跑输出都变，没有可重复输入。
#
#   所以把每个踩过的坑固化成断言，改解析代码必须先过一遍。
#
# ===== 两个目标 =====
#   1) pldims_test.arm64 —— 推到设备上跑。用真机 libc + 真机 dumpsys 样本，
#      是部署门控：单测不过就别装新二进制。
#   2) 主机 exe —— 若机器上有 MSVC/MinGW 会自动尝试；没有就跳过（不报错），
#      因为设备版已经覆盖了同样的代码路径。
param(
    [switch]$DeviceOnly
)
$ErrorActionPreference = 'Stop'
$NDK  = 'D:\CODEX\tools\android-sdk\ndk\25.2.9519653'
$BIN  = Join-Path $NDK 'toolchains\llvm\prebuilt\windows-x86_64\bin'
$SRC  = 'D:\CODEX\w\powerlens\c'
$OUT  = Join-Path $SRC 'out'
New-Item -ItemType Directory -Force -Path $OUT | Out-Null

$ctmp = Join-Path $OUT 'tmp'
New-Item -ItemType Directory -Force -Path $ctmp | Out-Null
$env:TMP = $ctmp; $env:TEMP = $ctmp

# ---------- 1. aarch64（真机门控用）----------
$tc = Join-Path $BIN 'aarch64-linux-android24-clang.cmd'
$armExe = Join-Path $OUT 'pldims_test.arm64'
#
# -Wno-unused-function 只对单测目标开：
#   pldims.c 里 now_ms/find_prev/run_dumpsys/load_prev/save_prev/save_global
#   只在 main() 路径用到，而单测编译排除了 main()，于是它们"未被使用"。
#   给源码逐个加 attribute 会污染生产代码，所以在这里按目标关掉这一项，
#   保留 -Wall -Wextra 对真问题的检查
#   （implicit-function-declaration 就是这样当场抓出我漏写前置声明的）。
#
& $tc -O1 -Wall -Wextra -Wno-unused-function -DPLDIMS_TEST `
    -o $armExe "$SRC\pldims.c" "$SRC\pldims_test.c" 2>&1 `
    | Select-String 'error|warning'
if ($LASTEXITCODE -ne 0) { throw "arm64 单测编译失败 exit=$LASTEXITCODE" }
Write-Host "[arm64] $armExe ($((Get-Item $armExe).Length) B)" -ForegroundColor Green

# ---------- 2. 主机（有编译器才做）----------
if (-not $DeviceOnly) {
    $clang = Join-Path $BIN 'clang.exe'
    $hostExe = Join-Path $OUT 'pldims_test.exe'
    # NDK 的 clang 不含 Windows CRT 库，需要能找到 mingw/MSVC 才有意义。
    # 试探性编译：失败就安静跳过。
    $out = & $clang --target=x86_64-w64-windows-gnu -O1 -DPLDIMS_TEST `
        -o $hostExe "$SRC\pldims.c" "$SRC\pldims_test.c" 2>&1
    if ($LASTEXITCODE -eq 0 -and (Test-Path $hostExe)) {
        Write-Host "[host] $hostExe —— 运行" -ForegroundColor Cyan
        & $hostExe
        Write-Host "主机单测退出码 $LASTEXITCODE"
        exit $LASTEXITCODE
    }
    Write-Host "[host] 本机没有可用的 Windows C 运行库（NDK clang 不带 CRT），跳过主机版" -ForegroundColor DarkGray
    Write-Host "       用设备版：adb push 后 su -c '/data/local/tmp/pldims_test.arm64'" -ForegroundColor DarkGray
}
exit 0
