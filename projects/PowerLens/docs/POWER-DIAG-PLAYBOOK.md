# Android 待机耗电诊断方法论

> 来源：小米 15 Pro（2410DPN6CC / haotian / Android 16 / KernelSU）实测总结
> 用途：下次遇到"待机耗电快"，照这份走，不必重新发明。
> 所有数字都是本机实测，不是理论值。

---

## 零、先决条件（不满足就别开始测）

| 条件 | 检查命令 | 不满足的后果 |
|---|---|---|
| 必须放电中 | `cat /sys/class/power_supply/battery/status` = `Discharging` | 充电时不挂起，测出的电流毫无意义 |
| 必须非 Awake | `dumpsys power \| grep mWakefulness` | 亮屏时是使用态，不是待机态 |
| 屏幕必须灭 | `cat /sys/class/backlight/panel0-backlight/brightness` = 0 | 同上 |
| 记录开机时长 | `cut -d' ' -f1 /proc/uptime` | 越短，累加计数器越干净 |

**本机踩过的坑**：所有"待机电流"必须用 `charge_counter` 差值算，不能用 `current_now`。
`current_now` 实测在 18 mA ~ 1.7 A 之间瞬间跳变，单次读数完全不可靠。

---

## 一、指标体系：只看四个数

### 1.1 电流（唯一可信的耗电指标）

```bash
C0=$(cat /sys/class/power_supply/battery/charge_counter)   # µAh
sleep 600
C1=$(cat /sys/class/power_supply/battery/charge_counter)
awk -v a=$C0 -v b=$C1 -v t=600 'BEGIN{printf "%.1f mA\n", (a-b)/1000*3600/t}'
```

**判读标准（本机实测基线）**

| 电流 | 评价 | 说明 |
|---|---|---|
| < 60 mA | 优秀 | 正常深度待机水平 |
| 60–120 mA | 可接受 | 本机最好成绩 67.8 mA |
| 120–250 mA | 偏高 | 本机当前状态 116–225 mA |
| > 250 mA | 严重 | 必然有挂起失败或唤醒风暴 |

### 1.2 挂起失败率（决定一切的前提）

```bash
S0=$(cat /sys/power/suspend_stats/success)
F0=$(cat /sys/power/suspend_stats/fail)
sleep 300
S1=$(cat /sys/power/suspend_stats/success)
F1=$(cat /sys/power/suspend_stats/fail)
# 失败率 = (F1-F0) / ((S1-S0)+(F1-F0))
cat /sys/power/suspend_stats/last_failed_dev     # 谁导致的
cat /sys/power/suspend_stats/last_failed_errno   # -16=EBUSY  -11=EAGAIN
```

**这个数字 > 40% 时，其它优化都是次要的** —— 设备根本没睡下去，所有应用层的工作都在
"醒着"的状态下发生，功耗自然高。

### 1.3 唤醒源速率（谁把设备拽出来）

```bash
W=/sys/kernel/debug/wakeup_sources
awk 'NR>1 && NF>=8 {print $1"|"$2"|"$3}' $W | sort > /tmp/w0
sleep 60
awk 'NR>1 && NF>=8 {print $1"|"$2"|"$3}' $W | sort > /tmp/w1
awk -F'|' 'FNR==NR{a[$1]=$3;next}{d=$3-a[$1]; if(d>0) printf "%d\t%s\n", d/60, $1}' /tmp/w0 /tmp/w1 | sort -nr | head -20
```

**必须求差**。这个文件是开机以来的累计值，直接看绝对值毫无意义
（本机 `synaptics_tcm.0` 累计 71 万次，但那是开机 16 小时的总量）。

### 1.4 per-UID CPU

```bash
cat /proc/uid_cputime/show_uid_stat   # "<uid>: <utime_us> <stime_us>"
```

**同样必须求差**，且需要 root（非 root 是 `EACCES`）。

---

## 二、四个致命陷阱（我都踩过）

### 陷阱 1：`/proc/stat` 字段错位 → 假的 iowait 86%

```
$2 user  $3 nice  $4 system  $5 idle  $6 iowait  $7 irq  $8 softirq  $9 steal
```

我曾经把 `$5 idle` 当成 iowait，得出"iowait 92%，磁盘是主因"的结论。
**真值是 iowait ≈ 0%，PSI io = 0.00%。**

**交叉验证方法**（必须做）：
```bash
cat /proc/pressure/io
# 若 PSI io 的 avg10 接近 0，则真实 I/O 压力为零，
# 和 /proc/stat 的高 iowait 矛盾时，几乎一定是字段读错了
```

### 陷阱 2：`loadavg` 在 Android 上会骗人

本机 `loadavg` 长期 17–38，但逐核 idle 80–95%。

原因：14 个**常驻 D 态内核线程**（`cpudmof/0-7`、`g_reclaim_thread`、`lb:g_manager`、
`rsmc_nl_up/dn_thread`、`hang_detect`、`adci_thread`）永远在 D 态，
Linux 的 loadavg 会把 D 态算进去。

**结论：Android 上 loadavg 不能用来判断 CPU 负载**，必须看 `/proc/stat` 逐核差值。

### 陷阱 3：累加计数器当瞬时值

`/proc/uid_cputime/show_uid_stat`、`/sys/kernel/debug/wakeup_sources`、
`/proc/vmstat`、`/proc/diskstats` —— 全都是**开机以来累加**。

本机实例：`com.android.dynsystem` 看起来占了 40 秒 CPU，
实际是它历史上刷过一次 GSI 镜像的残留，当前完全没有活动。

**规则：见到 `/proc/` 里的计数器，第一反应是"要两次采样求差"。**

### 陷阱 4：息屏会断网

本机实测：灭屏约 20 分钟后，**整个 Wi-Fi 连接消失**（不只是 adbd 被掐），
`ping` 100% 丢包，`adb` 完全失联。

这对长时间测量的影响：
- 任何依赖 adb 常驻的脚本都会中断
- 测量必须在手机上自己跑（`setsid sh script &`），结果写文件，事后取

**配套改动**：
```bash
settings put global wifi_sleep_policy 2      # 2 = 息屏保持
cmd deviceidle whitelist +com.android.shell  # 别冻结 adbd
```

---

## 三、本机实测数据基线（2026-11-09）

### 3.1 电量

| 指标 | 值 |
|---|---|
| 设计容量 | 6100 mAh |
| 当前满充 | 5890 mAh（`charge_full`），健康度 96.6% |
| 循环次数 | 39 |
| 电量计单位 | µAh（`charge_counter` = 5332000 表示 5332 mAh） |

### 3.2 待机（息屏 Dozing / Discharging）

| 指标 | 最好的时候 | 当前 |
|---|---|---|
| 电流 | **67.8 mA** | 116–225 mA |
| %/h | 1.13 | 1.9–3.7 |
| 8 小时掉电 | 9% | 15–30% |

### 3.3 挂起

```
success 累计 12596     fail 累计 2844
失败率 ≈ 18%（健康时），恶化时达 44%
last_failed_dev = 0000:01:00.0（PCIe）/ alarmtimer.0.auto
last_failed_errno = -11 (EAGAIN) / -16 (EBUSY)
```

### 3.4 唤醒源（本机的"名人堂"）

| 源 | 速率 | 性质 |
|---|---|---|
| `eventpoll` ×3 | 2400 / 1200 / 350 次/秒 | 应用层 EPOLLWAKEUP 池 |
| `qrtr_ws` | **1579 次/秒** | Qualcomm IPC Router |
| `[timerfd]` | 460 次/秒 | EPOLLWAKEUP 定时器，累计真唤醒 14425 次 |
| `gh_vcpu_ws_45_0/1` | 累计 46 万 / 40 万 | gunyah hypervisor vCPU |
| `qcom_rx_wakelock` | 10 次/秒 | Wi-Fi 收包 |
| `synaptics_tcm.0` | 累计 71 万 | 触控 IC（双击亮屏开启时） |
| `fastrpc-secure` | 累计 1 万 | ADSP/NPU |

**本机最特殊的结构**：
```
qrtr / qrtr_gunyah / qrtr_mhi / qrtr_smd / qrtr_tun
[qrtr_ns] pid=163   [qrtr_rx] × 6 个线程
```
`qrtr_gunyah` 把 QRTR 与 hypervisor 直接连起来 →
AP 与 modem/ADSP/trustedvm 之间每来一条 QRTR 消息就唤醒一次 vCPU，
vCPU 又要 AP 调度 → 正反馈。

### 3.5 已排除的嫌疑（有数据支撑，不必再查）

| 嫌疑 | 数据 | 结论 |
|---|---|---|
| 磁盘 I/O | PSI io avg10 = 0.00% | 排除 |
| CPU 空转 | 逐核 idle 80–95% | 排除 |
| 应用 CPU | top UID 合计 91 秒/96 秒，最大项是历史累计 | 排除 |
| PowerLens 自身 | 0 ms / 10 s | 排除 |
| 支付宝 push | 339 MB RSS 但 CPU 接近 0 | 排除（内存占用大，功耗不大） |
| 京东 | CPU 恒 0 | 排除 |
| zram thrashing | pswpin 21 页/秒（健康时） | 当前不是主因 |

### 3.6 内存现状

```
MemTotal   15.1 GB
MemAvailable 3.87 GB
zram swap  8.81 GB / 16 GB = 55%
swappiness = 100（最高档）
```

大内存占用者（RSS+Swap 合计）：
微信 1222 MB、B站 1064 MB、支付宝 919 MB、system_server 825 MB、
酷安 803 MB、systemui 754 MB、钉钉 718 MB、百度地图 642 MB。

**注意**：它们占内存多，但 CPU 不高，所以对**待机电流**贡献有限。
只能在 "zram 接近满 → 触发 aggressive reclaim" 时才间接影响功耗。

---

## 四、诊断流程（照着走）

```
第 1 步：跑基线
   su -c "sh /data/local/tmp/drain-audit.sh"

第 2 步：看挂起失败率
   > 40%  → 先解决挂起问题（第 3 步）
   15-40% → 有优化空间（第 4 步）
   < 15%  → 看唤醒源（第 5 步）

第 3 步：挂起为什么失败
   cat /sys/power/suspend_stats/last_failed_dev
   cat /sys/power/suspend_stats/last_failed_errno
   dmesg | grep -E 'PM: suspend|Abort: Pending' | tail -20

第 4 步：谁在唤醒
   → 用 ab-test.sh 逐个条件对比

第 5 步：A/B 验证每个改动
   su -c "sh /data/local/tmp/ab-test.sh preset swappiness"
   su -c "sh /data/local/tmp/ab-test.sh preset adsprpcd"
   su -c "sh /data/local/tmp/ab-test.sh preset deep"

第 6 步：固化有效项
   写进 /data/adb/service.d/，每项单独一个文件，可独立回滚
```

---

## 五、A/B 实验的正确做法

单次测量**不可信**。本机同条件重复测量的自然波动约 ±15%。

**必须做的三件事**：

1. **交替而非分组**：A B A B，不是 A A B B
   （时间漂移会污染分组实验）
2. **取中位数而非平均**：离群点（偶发网络重连）会拉偏平均
3. **多轮**：至少 3 轮，每轮 ≥ 90 秒

`ab-test.sh` 已实现这些。判读规则：
- B 比 A 省 > 15% → 采用
- B 比 A 费 > 15% → 不采用
- 差异 < 15% → **无结论**，别硬下判断

---

## 六、常用工具速查

```bash
# ===== 一次性诊断 =====
su -c "sh /data/local/tmp/drain-audit.sh"           # 90 秒快速版
su -c "sh /data/local/tmp/drain-audit.sh full"      # 3 分钟完整版

# ===== A/B 实验 =====
su -c "sh /data/local/tmp/ab-test.sh preset swappiness"
su -c "sh /data/local/tmp/ab-test.sh preset deep"
su -c "sh /data/local/tmp/ab-test.sh 90 3 'A命令' 'B命令'"

# ===== 单项深挖 =====
su -c "sh /data/local/tmp/idle.sh"      # 逐核 idle/iowait 精确分解
su -c "sh /data/local/tmp/qrtr2.sh"     # QRTR / eventpoll / gh_vcpu
su -c "sh /data/local/tmp/swtune2.sh"   # swappiness（注意：本脚本 iowait 字段有 bug）

# ===== 后台采集（不依赖 adb）=====
su -c "setsid sh /data/local/tmp/plc/plc-daemon.sh 600 >/dev/null 2>&1 &"
su -c "/data/local/tmp/plc/plcollect --status -f <文件>"   # 核算
```

---

## 七、PowerLens 工具本身的坑（已修）

| 问题 | 现象 | 修法 |
|---|---|---|
| **mAh 虚高 4.5 倍** | 显示 2813 mAh，真实 629 mAh | 不能"首值减末值"，要逐对累计下降、跳过充电回升 |
| 老格式无状态列 | 状态列全是 `0`，无法区分充放电 | `0`/`?`/空 视为未知，按放电处理 |
| 单位不一致 | 1.0 存 mAh（2687），1.2 存 µAh（5410000） | 第 4 列 < 100000 视为 mAh |
| dumpsys 太贵 | 666 ms / 280 KB，CPU 记在 system_server | 换 procfs：97 ms / 12 KB |
| App 调 root 失败 | `EACCES`（KernelSU 拒 app 的 su） | 采集搬到 root 侧守护，App 只读文件 |
| JobScheduler 抛异常 | HyperOS `enforceValidJobRequest` | App 侧不注册 Job |
| 电池行匹配错误 | `"\t*battery\t"` 少了中间 `\t`，采样点恒为 0 | 用 split 后按列判断 |

---

## 八、构建环境（重装系统后照这个装）

```
JDK 17        D:\CODEX\tools\jdk17          (Temurin 17.0.21)
Android SDK   D:\CODEX\tools\android-sdk
  ├─ cmdline-tools\latest                   (sdkmanager 19.0)
  ├─ platforms\android-35                   (targetSdk 35 必需)
  ├─ build-tools\35.0.0                     (aapt2 / d8 / zipalign / apksigner)
  └─ ndk\25.2.9519653                       (交叉编译 C 采集器)
platform-tools D:\CODEX\tools\platform-tools (adb 37.0.1)
```

**本机特有的三个环境坑**（已固化在脚本里）：

1. `C:\Users\Administrator\AppData\Local\Temp` **对 adb 不可写** →
   adb 启动时必写 `adb.log`，直接崩。必须 `TMP/TEMP` 指向 `D:\CODEX\tmp`。
2. `C:\Users\Administrator\.android` **不可写** → adb 生成不了密钥。
   已用 junction 指向 `D:\CODEX\.android`。
   jadx 还需要 `AppData\Local\skylot` 和 `AppData\Roaming\skylot` 两个 junction。
3. PowerShell 5.1 读**无 BOM 的 UTF-8 .ps1 会乱码**，中文全角字符被吞导致语法错误。
   写脚本后必须按 `UTF8Encoding($true)` 重写加 BOM。

**构建 APK 时的一个硬要求**：
```powershell
aapt2 link --no-compress ...     # resources.arsc 必须 STORED
# 否则 Android 11+ 报：
# "requires the resources.arsc ... stored uncompressed and aligned on a 4-byte boundary"
```
且**不能解压-重打包**（会把 arsc 变回 compressed），
只能在 aapt2 产出的 apk 上用 zip Update 模式追加 classes.dex。

---

## 九、待验证的假设（下次接着做）

1. **qrtr_gunyah 正反馈**：停 `vendor.adsprpcd` 后 `gh_vcpu_ws` 是否下降
2. **双击亮屏的代价**：`synaptics_tcm.0` 累计 71 万次唤醒，
   关掉 `double_tap_to_wake` 后待机电流能降多少（本机该开关是开的）
3. **deep 挂起**：第一次测试失败（`errno -16 EBUSY`，根本没进 deep），
   在压制 hypervisor 唤醒源后重测才有意义
4. **三个 eventpoll 池的归属**：需要增量扫描（只扫 CPU 有增长的进程）才能定位
5. **swappiness=100 是否真的有害**：第一次 A/B 因字段错位作废，需重测