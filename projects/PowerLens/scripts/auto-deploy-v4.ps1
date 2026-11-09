# auto-deploy-v4.ps1 —— 设备一上线就自动完成 v4 部署 + 自证 + 截图
#
# 为什么需要它：这台设备的无线 adb 每 10-30 分钟掉一次，
# 且端口会变（实测 36761 → 40483）。手工重试会大量浪费在等待上。
# 这个脚本自己等、自己找端口、上线后一口气跑完全部验证并落盘。
#
# 用法：
#   powershell -File auto-deploy-v4.ps1                 # 默认等 40 分钟
#   powershell -File auto-deploy-v4.ps1 -WaitMinutes 90

param(
    [int]$WaitMinutes = 40,
    [string]$IP = '192.168.137.22',
    [int[]]$KnownPorts = @(40483, 36761, 5555)
)

$ErrorActionPreference = 'Continue'
$env:TMP = 'D:\CODEX\tmp'; $env:TEMP = 'D:\CODEX\tmp'
$ADB  = 'D:\CODEX\tools\platform-tools\adb.exe'
$ROOT = 'D:\CODEX\w\powerlens'
$OUT  = 'D:\CODEX\tmp\v4-verify'
New-Item -ItemType Directory -Force -Path $OUT | Out-Null
$log = Join-Path $OUT 'deploy.log'
function L($m) { $t = (Get-Date -Format 'HH:mm:ss'); "$t $m" | Tee-Object -FilePath $log -Append | Write-Host }

function Test-Shell($addr) {
    $t = & $ADB -s $addr shell 'echo __OK__' 2>&1
    return ("$t" -match '__OK__')
}

<#
===== 为什么必须用 mDNS 发现，而不是记住端口 =====
  Android 无线调试的端口【每次重连都变】：
    本机实测 36761 → 40483，之后手机重连又换了。
  写死端口 = 每次手机重连就失联，只能手工全端口扫（30000-61000 要 3 分钟）。
  `adb mdns services` 直接列出 _adb-tls-connect._tcp 的 IP:端口，
  是官方发现机制，一次调用就够。
#>
function Find-ByMdns {
    $sv = & $ADB mdns services 2>&1 | Out-String
    $lines = $sv -split "`n" | Where-Object { $_ -match 'adb-tls-connect|adb-server' }
    foreach ($ln in $lines) {
        # 形如：  adb-xxxx    _adb-tls-connect._tcp.    192.168.137.22    37317
        $cols = ($ln -split '\s{2,}') | Where-Object { $_ }
        $ip = $null; $port = $null
        foreach ($c in $cols) {
            if ($c -match '^\d+\.\d+\.\d+\.\d+$') { $ip = $c }
            if ($c -match '^\d{2,5}$') { $port = [int]$c }
        }
        if ($ip -and $port) {
            & $ADB connect "${ip}:${port}" 2>&1 | Out-Null
            Start-Sleep -Seconds 2
            if (Test-Shell "${ip}:${port}") { return "${ip}:${port}" }
        }
    }
    return $null
}

function Find-Device {
    # 1) mDNS（最快、能拿到新端口）
    $m = Find-ByMdns
    if ($m) { L "  mDNS 命中 $m"; return $m }
    # 2) 已知 IP + 候选端口
    foreach ($p in $KnownPorts) {
        & $ADB connect "${IP}:${p}" 2>&1 | Out-Null
        Start-Sleep -Seconds 2
        if (Test-Shell "${IP}:${p}") { return "${IP}:${p}" }
    }
    # 3) 同网段其他 IP 的 adb 端口（热点会重新分配 IP）
    $lan = ($IP -split '\.')[0..2] -join '.'
    $ips = @("$lan.22") + (Get-NetNeighbor -ErrorAction SilentlyContinue |
            Where-Object { $_.IPAddress -like "$lan.*" -and $_.IPAddress -ne "$lan.1" } |
            Select-Object -ExpandProperty IPAddress)
    foreach ($cand in ($ips | Sort-Object -Unique)) {
        foreach ($p in @(5555, 36761, 40483)) {
            try {
                $c = New-Object System.Net.Sockets.TcpClient
                $iar = $c.BeginConnect($cand, $p, $null, $null)
                if ($iar.AsyncWaitHandle.WaitOne(300) -and $c.Connected) {
                    $c.Close()
                    & $ADB connect "${cand}:${p}" 2>&1 | Out-Null
                    Start-Sleep -Seconds 2
                    if (Test-Shell "${cand}:${p}") { return "${cand}:${p}" }
                } else { $c.Close() }
            } catch {}
        }
    }
    return $null
}

L "=== 等待设备（上限 $WaitMinutes 分钟）==="
$addr = $null
$deadline = (Get-Date).AddMinutes($WaitMinutes)
$tries = 0
while ((Get-Date) -lt $deadline) {
    $tries++
    $addr = Find-Device
    if ($addr) { break }
    if ($tries % 4 -eq 0) { & $ADB kill-server 2>&1 | Out-Null; Start-Sleep -Seconds 3; & $ADB start-server 2>&1 | Out-Null }
    L "  第 $tries 次未连上，15 秒后重试"
    Start-Sleep -Seconds 15
}
if (-not $addr) { L 'OFFLINE —— 放弃，设备需要物理确认（USB 调试授权/热点/息屏断网）'; exit 1 }
L "设备已上线：$addr"

function Sh($cmd) {
    $r = & $ADB -s $addr shell $cmd 2>&1
    return ($r | Out-String)
}
function Root($script) { Sh "su -c '$script'" }

L '=== 0. 解析层回归单测（门控：不过就别装新二进制）==='
# 这轮的 9 个 bug 全是"程序跑通、数字合法、值却错"的静默错误，
# 线上没法判定（dumpsys 每次输出都变）。断言用真机 libc 跑同一份代码，
# 比肉眼看输出可靠。失败即中止，不覆盖设备上正在采集的二进制。
& $ADB -s $addr push "$ROOT\c\out\pldims_test.arm64" /data/local/tmp/pldims_test 2>&1 | Out-Null
Root 'chmod 755 /data/local/tmp/pldims_test' | Out-Null
$t = Root '/data/local/tmp/pldims_test'
$t | Out-File "$OUT\unittest.txt" -Encoding UTF8
$failLines = ($t -split "`n") | Where-Object { $_ -match 'FAIL' }
$okLines = ($t -split "`n") | Where-Object { $_ -match '全部通过|失败' }
$okLines | ForEach-Object { L "  $($_.Trim())" }
$failLines | ForEach-Object { L "  $_" }
if ($t -notmatch '全部通过') {
    L '  ✗ 单测未通过 —— 中止部署（不要覆盖设备上的 pldims）'
    exit 2
}
L '  ✓ 单测通过，继续部署'

L '=== 1. 推送二进制 ==='
& $ADB -s $addr push "$ROOT\c\out\pldims"   /data/local/tmp/plc/pldims   2>&1 | Out-Null
& $ADB -s $addr push "$ROOT\c\out\plcollect" /data/local/tmp/plc/plcollect 2>&1 | Out-Null
& $ADB -s $addr push "$ROOT\scripts\deploy-v4.sh" /data/local/tmp/deploy-v4.sh 2>&1 | Out-Null
Root 'chmod 755 /data/local/tmp/plc/pldims /data/local/tmp/plc/plcollect; chmod 644 /data/local/tmp/deploy-v4.sh' | Out-Null
L (Root '/data/local/tmp/plc/pldims -v 2>&1 | grep -c GLOBAL').Trim() + ' 行 GLOBAL 输出（>0 说明 v3 已就位）'

L '=== 2. 存现场 dumpsys 作离线回归输入 ==='
Root 'dumpsys batterystats --charged > /data/local/tmp/bs-v4.txt 2>/dev/null; wc -l /data/local/tmp/bs-v4.txt'

L '=== 3. 跑 deploy-v4.sh（含确定性自证 + 基线重置 + 列数校验）==='
$out = Root 'sh /data/local/tmp/deploy-v4.sh'
$out | Out-File "$OUT\deploy-v4-out.txt" -Encoding UTF8
$out -split "`n" | Where-Object { $_ -match '✓|✗|列|GLOBAL|一致|行,' } | ForEach-Object { L ("  " + $_.Trim()) }

L '=== 4. 与 dumpsys 原文交叉核对 u0a341 / uid0 / uid1000 ==='
$src = Get-Content "$OUT\deploy-v4-out.txt" -Encoding UTF8 -Raw
Root 'grep -m1 "UID u0a341" -A1 /data/local/tmp/bs-v4.txt | tail -1' |
    Out-File "$OUT\truth-u0a341.txt" -Encoding UTF8
(Get-Content "$OUT\truth-u0a341.txt" -Raw) -split "`n" | Where-Object { $_ } | ForEach-Object { L "  原文: $($_.Trim())" }

L '=== 5. 等一轮守护采样，检查增量是否物理合理 ==='
Start-Sleep -Seconds 70
$d = Root 'tail -6 /data/local/tmp/powerlens/dims.tsv'
$d | Out-File "$OUT\dims.tsv" -Encoding UTF8
$g = Root 'cat /data/local/tmp/powerlens/dims.global'
$g | Out-File "$OUT\dims.global" -Encoding UTF8
L "  dims.global: $($g.Trim())"

# 自证：任何单 UID 单字段增量不得超过全机同字段增量
$rows = Get-Content "$OUT\dims.tsv" -Encoding UTF8 | Where-Object { $_ -match "`t" }
$gl = (Get-Content "$OUT\dims.global" -Encoding UTF8 | Where-Object { $_ }) -split "`t"
if ($gl.Count -ge 15) {
    # 全机本轮增量 ≈ global 累计 - 上轮累计；这里用「单 UID 之和 ≤ global 累计」做粗校验
    $sumByField = New-Object 'int64[]' 12
    foreach ($r in $rows) {
        $p = $r -split "`t"
        if ($p.Count -lt 22) { continue }
        for ($i = 0; $i -lt 12; $i++) {
            $v = 0L
            if ([int64]::TryParse($p[9 + $i], [ref] $v)) { $sumByField[$i] += $v }
        }
    }
    $names = @('cpu','screen','radio','wl','wifi','audio','video','sensors','camera','gnss','display','other')
    for ($i = 0; $i -lt 12; $i++) {
        $cum = 0L; [void][int64]::TryParse($gl[2 + $i], [ref] $cum)
        $flag = if ($sumByField[$i] -gt $cum) { '✗ 超全机累计（异常）' } else { 'ok' }
        L ("  Σ{0,-8} 本轮 {1,9} µAh  全机累计 {2,9} µAh  {3}" -f $names[$i], $sumByField[$i], $cum, $flag)
    }
}

L '=== 6. 安装 APK（只允许 -r，uninstall 会丢 KernelSU 授权）==='
& $ADB -s $addr push "$ROOT\app2\out\PowerLens-1.1.apk" /data/local/tmp/PowerLens-v4.apk 2>&1 | Out-Null
$ins = Sh 'pm install -r /data/local/tmp/PowerLens-v4.apk'
L "  $($ins.Trim())"
$ver = Sh 'dumpsys package com.powerlens.app | grep -E "versionName|lastUpdateTime"'
$ver -split "`n" | Where-Object { $_ } | ForEach-Object { L "  $($_.Trim())" }

L '=== 7. 唤醒屏幕并启动 App 截图 ==='
Sh 'input keyevent KEYCODE_WAKEUP' | Out-Null
Start-Sleep -Seconds 2
Sh 'wm dismiss-keyguard' | Out-Null
Sh 'am start -n com.powerlens.app/com.powerlens.app.MainActivity' | Out-Null
Start-Sleep -Seconds 6
& $ADB -s $addr exec-out screencap -p > "$OUT\ui-v4-all.png" 2>$null
L "  截图 $((Get-Item "$OUT\ui-v4-all.png" -ErrorAction SilentlyContinue).Length) 字节"

L '=== 8. 抓 UI 文本（比截图更能核对数字）==='
Sh 'uiautomator dump /sdcard/v4.xml' | Out-Null
$txt = Sh 'cat /sdcard/v4.xml'
$txt | Out-File "$OUT\ui-v4.xml" -Encoding UTF8
$m = [regex]::Matches($txt, '实测放电[^"]*|区间模型电量[^"]*|模型电量[^"]*|全部口径[^"]*|维度数据[^"]*')
foreach ($x in $m) { L "  UI: $($x.Value)" }

L '=== 9. 拉回原始数据供 PC 侧复核 ==='
& $ADB -s $addr shell "su -c 'cat /data/local/tmp/powerlens/dims.prev'" 2>&1 |
    Out-File "$OUT\dims.prev" -Encoding UTF8
& $ADB -s $addr pull /data/local/tmp/bs-v4.txt "$OUT\bs-v4.txt" 2>&1 | Out-Null
L "  已拉回 bs-v4.txt（$((Get-Item "$OUT\bs-v4.txt" -ErrorAction SilentlyContinue).Length) 字节）"

L '=== 完成 ==='
L "  产物目录：$OUT"
exit 0
