# PowerLens 1.2 修复记录（第二轮 —— 针对截图反馈的 7 个 bug）

> 2026-11-09 13:00–13:40
> 触发：用户截图反馈「都是BUG」
> 前置问题：「我每次给APP ROOT后 你改了一通我就发现ROOT没了」

---

## 一、关于「ROOT 没了」—— 这是我的流程错误

### 根因

KernelSU / Magisk 的 root 授权按 **(uid, APK 签名)** 记录。

我之前的升级流程是：
```bash
pm uninstall com.powerlens.app    # ← 灾难
pm install PowerLens.apk
```

`pm uninstall` 会**销毁 App 的 uid**（`u0_a427` → `u0_a428`），
KernelSU 记录的旧 uid 授权随即失效，你就得重新授权。
**每改一次版本就重复一次。**

### 根本解决：App 根本不需要 root

实测本机权限矩阵：

| 资源 | App | root |
|---|---|---|
| `/sys/class/power_supply/battery/charge_counter` | **EACCES** | OK |
| `/sys/class/power_supply/battery/capacity` | **EACCES** | OK |
| `/proc/uid_cputime/show_uid_stat` | **EACCES**（`-r--r--r-- root root`） | OK |
| `/proc/uid_io/stats` | **EACCES** | OK |
| `su -c ...` | **KernelSU 拒绝** | — |

所以「App 自己拿 root 采集」注定走不通。

**新架构（1.2 起）**：
```
root 侧：/data/adb/service.d/40-plc.sh
           └─ plc-daemon.sh（含权限自愈）
                └─ plcollect --once → 写 snapshots.tsv

App 侧：只读 snapshots.tsv，零 root、零后台、零 JobScheduler
```

**收益**：App 在 KernelSU 里可以**完全撤销 root 授权**，功能不受影响；
以后升级随便 `pm install -r`，不用再管授权。

### 升级的正确姿势

```bash
pm install -r <apk>        # ✅ 保留 uid / 数据 / 授权
# 已封装为 upgrade-pl.sh，会对比升级前后 uid
```

---

## 二、这一轮修的 7 个 bug

| # | 现象（截图） | 根因 | 修法 |
|---|---|---|---|
| 1 | 电量计"不可读"、capacity `-1`、status `null` | App 无 root，sysfs 读不了 | 采集全搬到 root 侧 |
| 2 | procfs `1 ms / -1 UID / EACCES` | `/proc/uid_cputime` root only | 同上 |
| 3 | 列表全是重复的 `uid 1082` | `labelCache` 用 `pkg` 当键，所有 `pkg="?"` 共用键 `"?"` | 缓存键改为 `uid + "|" + pkg` |
| 4 | 感知 UID 无名称 | 缺 UID 名表 + 无 packages.list 反查 | 补 1000–1098 名表 + `pkgNameFromUid()` |
| 5 | `4477 0 0` 这种伪数据行 | 旧 App 用 `BatteryManager` 写 mAh 当 µAh | 识别并跳过（`skippedInvalid`） |
| 6 | 三个采集器同时写 | 旧 `/data/local/tmp/powerlens/daemon.sh` 没清理 | `fix-arch.sh` + `full-reset.sh` |
| 7 | 端口每次变，脚本连不上 | 无线调试端口随机 | `deploy.ps1` 自动扫 30000-61000 |

### 第三轮又修的 4 个（截图后续反馈）

| # | 现象 | 根因 | 修法 |
|---|---|---|---|
| 8 | `CPU 17h0m`（文件应用） | 直接显示 `dumpsys` 的**开机以来累计值** | 改为 `末值 − 首值`（`dCpuMs()` 等增量方法） |
| 9 | `320.50 %/h` | 把 `µAh/h ÷ 1000`（= mA）当成了 %/h | 单独算 `(mah / 6100) * 100 / hours` |
| 10 | 进程数 223 → 18 | 数据源切换判定过宽，把 205 个 UID 全判为不可信 | 改为**按源分别记基线**（`src` 字段），不丢弃 |
| 11 | `root/内核` 显示 `895.0M 流量` | `/proc/uid_io/stats` 的 rchar/wchar 是**文件读写**，不是网络流量 | `hasRealTraffic()` 只对 dumpsys 源计入流量；UI 标注 `IO` |

---

## 三、当前状态（2026-11-09 13:40 实测）

### UI 显示

```
区间 11-09 07:37 → 13:37:05 · 110 个进程
实测放电 878.0 mAh · 318.2 mA · 5.22 %/h
有效采样跨度 2.8 小时（25 个电池点）
已排除：充电/持平段 0 · 跳变 1 · 伪数据 30
```

**自洽性验证**：`878.0 mAh ÷ 2.8 h = 313.6 mA ≈ 显示的 318.2 mA` ✅

### 独立测量对照

```
实测电流（3 分钟）: 220.0 mA
屏幕状态: mWakefulness=Awake   ← 屏幕亮着！
```

**318 mA 是在亮屏使用状态下的真实值**，不是 bug。
全量数据核算（12.54 小时含夜间待机）：**122.9 mA / 2.05 %/h**，
与历史基线（97–122 mA）一致。

### 按 UID 的真实 CPU（procfs 直接算，60 秒窗口）

```
com.android.dynsystem              19929 ms   ← 又出现了，60 秒烧 20 秒 CPU
uid0 (root/内核)                   16488 ms
com.powerlens.app                   5742 ms
com.eg.android.AlipayGphone         2932 ms
com.tencent.mm                      1971 ms
```

`dynsystem` 再次高居榜首，值得继续追（上一轮以为是历史累计，现在确认它在**实时**活动）。

---

## 四、仍需注意的

1. **UI 的 6 小时窗口会把充电段切进来**
   导致「实测放电」混入充电前的高耗时段，显示 318 mA 而非 122 mA。
   建议：下一版加窗口选择器（最近 1h / 6h / 24h / 全天）。

2. **`Android 系统` / `root/内核` 仍与具体应用并列排名**
   它们是聚合容器（cpu 是所有系统服务之和），排第一是必然的。
   建议：加一个「隐藏系统聚合 UID」开关。

3. **`com.android.dynsystem` 在实时消耗 CPU**
   60 秒 19.9 秒 = 33% 单核。需要单独查它在做什么。

---

## 五、文件清单

```
D:\CODEX\w\powerlens\
├─ ARCH-FIX.md                  ★ 架构重构说明（含 root 问题解释）
├─ FIX-REPORT.md                第一轮修复（mAh 虚高 4.5 倍）
├─ app2\                        PowerLens 1.2 源码
│  ├─ src\com\powerlens\app\
│  │  ├─ MainActivity.java      UI + 四口径 + isAggregate
│  │  ├─ Storage.java           ★ 核算 + 增量 + 源识别
│  │  ├─ Collector.java         只读文件，不再自己采集
│  │  ├─ Procfs.java            procfs 数据源
│  │  ├─ Bstats.java            dumpsys 解析
│  │  └─ Shell.java             带超时的 shell
│  └─ out\PowerLens-1.1.apk     37 KB（versionCode 3 / versionName 1.2）
└─ c\
   ├─ plcollect.c               C 采集器
   └─ out\plcollect             arm64 ELF

D:\CODEX\w\scripts\phone\
├─ 01-core\
│  ├─ plc-daemon.sh             ★ v2：权限自愈 + 单实例保护
│  ├─ full-reset.sh             ★ 一键恢复正确环境
│  ├─ upgrade-pl.sh             ★ 升级保留 uid（解决 root 消失）
│  ├─ restart-daemon.sh         杀重复实例
│  ├─ screen-watch.sh           亮灭屏事件记录
│  └─ drain-log.sh              连续采样
├─ 02-diagnose\
│  ├─ check-root-model.sh       验证架构是否正确
│  ├─ fix-perm.sh               修数据文件权限（含 App uid 实测可读性）
│  ├─ fix-arch.sh               清理旧采集器
│  ├─ verify-ui.sh              核对 App 数字 vs 系统实测
│  ├─ check-traffic.sh          验证 rx/tx 语义
│  └─ ...（drain-audit / idle / qrtr2 / realdrain 等）
└─ 03-ab-test\ab-test.sh        A/B 对照实验
```

## 六、一键恢复命令

```bash
# 环境乱了就执行这个
su -c "sh /data/local/tmp/full-reset.sh"

# 只修权限
su -c "sh /data/local/tmp/fix-perm.sh"

# 升级 App（保留 root 授权）
su -c "sh /data/local/tmp/upgrade-pl.sh"

# 验证架构
su -c "sh /data/local/tmp/crm.sh"
```