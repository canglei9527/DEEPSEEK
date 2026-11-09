# PowerLens 1.1 修复报告

> 日期：2026-11-09　目标机：Xiaomi 2410DPN6CC / haotian / Android 16 (sdk 36)
> 构建机：DEYEBPW001563　工程：`D:\CODEX\w\powerlens\app2\`

## 一、核心 bug：功耗显示虚高 5.35 倍

### 现象

App 显示「实测总耗电 2813 mAh」，而同期真实放电只有 526 mAh。

### 根因（算法级，不是采集问题）

1.0 的 `queryRange` 把 `*` 电池行的第 3 列当作「区间总耗电」，而这一列的真实含义是
**「区间首个电量计值 − 区间末个电量计值」**。

```
01:33  2539 mAh
01:43  3704  ← 充电 +602
01:52  4183  ← 充电 +189
02:03  4803  ← 充电 +551
02:13  5293  ← 充电 +490
02:23  5741  ← 充电 +448
02:33  5890  ← 充电 +149
```

这段时间充进去约 2500 mAh，全被算成「消耗」。更糟的是旧格式**只存百分比/整数 mAh，
且状态列全部写 `0`**，读取端根本无法区分充电段和放电段。

实测对照：

| | 数值 |
|---|---|
| App 1.0 显示 | **2813 mAh** |
| 真实放电（只累计下降） | **629 mAh** |
| 虚高倍数 | **4.5×** |
| 折算电流（正确） | **67.8 mA / 1.13 %/h** |

### 修复

`Storage.summarize()` 逐对采样点核算：

```
d = prev.count − cur.count
d > 0 且 d ≤ 上限          → 累计为放电
d > 0 但 d > 上限          → 判定为电量计跳变/校准，丢弃并计数
d ≤ 0                      → 充电或持平，跳过（不计负、不回退）
上限 = max(50 mAh, 15 mAh/min × 间隔分钟数)
```

同时对老数据做两处兼容：
- **单位自适应**：第 4 列 < 100000 视为 mAh（1.0 格式），否则视为 µAh（1.1 格式）
- **状态列缺失**：`0` / `?` / 空 视为「未知」，按放电处理（否则老数据放电全被丢弃）

## 二、为什么保留 Java（回答"换 C 是否更省"）

实测 PowerLens 进程：

| 指标 | 值 |
|---|---|
| 自身 CPU | **10 ms / 10 s = 0.1% 单核** → 折算整机 **约 0.01 mA** |
| Java 堆 | **2.3 MB** |
| RSS 166 MB 构成 | `.so mmap` 47 MB、`.jar mmap` 43 MB、`.oat` 16 MB、`.art` 16 MB — **全是 Android Framework，用 C 写 Activity 也甩不掉** |

**所以换语言省不到 0.05% 电量。**

真正的开销是 `dumpsys batterystats --charged`：**666 ms + 280 KB**，且这部分 CPU
记在 `system_server`(uid 1000) 头上——用户在任何耗电统计里都看不到它。
一天 144 次 = **96 秒 system_server CPU**。

## 三、实际做法：换数据源 + C 写采集器

| | dumpsys（1.0） | procfs + C（1.1） |
|---|---|---|
| 单次耗时 | 666 ms | **97 ms** |
| 数据量 | 280 KB | **12 KB** |
| 输出行数 | 16144 | **197** |
| CPU 归属 | system_server（隐藏成本） | 内核，不计入任何进程 |
| Java 堆占用 | 大（解析 280 KB 字符串） | **0**（C 进程独立，退出即释放） |

### 架构

```
root 常驻 C 守护  /data/local/tmp/plc/plc-daemon.sh  (每 600 s)
      └─ plcollect --once  →  读 procfs  →  写 snapshots.tsv
                                              ↓
App（u0_a428，无 root、无 JobScheduler、无后台）只读文件并显示
```

这样做的原因：实测 App 以 `u0_a428` 身份 fork 出的 `su` 子进程被 KernelSU 拒
（`/proc/uid_cputime/show_uid_stat: EACCES`），所以「App 自己调 root 采集」走不通。
把采集全部搬到 root 侧后，**App 侧常驻 CPU 实测降为 0 ms / 10 s**。

### 数据源

| 文件 | 内容 | 权限 |
|---|---|---|
| `/proc/uid_cputime/show_uid_stat` | 每 UID 用户态+内核态 µs | 需 root |
| `/proc/uid_io/stats` | 每 UID 读写字节 | 需 root |
| `/sys/class/power_supply/battery/*` | 电量计 µAh / 百分比 / 充电状态 | 可读 |

**局限（诚实标注）**：procfs 没有「按 UID 的息屏 CPU / 唤醒锁 / 前台时长」这三个维度，
只有 batterystats 有。所以：

- 默认口径 = **总 CPU + IO**（足够回答"谁在耗电"）
- 需要息屏维度时用 `Collector.collectLocked(ctx, true)` 强制走 dumpsys（手动/低频）

## 四、修掉的其他 bug

| # | 问题 | 修法 |
|---|---|---|
| 1 | JobScheduler 在 HyperOS 上报 `enforceValidJobRequest` 异常刷屏 | App 侧彻底不注册 Job，改 root C 守护 |
| 2 | `"\t*battery\t"` 少了中间 `\t`，Java 侧统计出 **0 个采样点** | 改为 `"*".equals(p[1]) && "battery".equals(p[2])` 纯 split 判定 |
| 3 | 1.0 的 refresh 30 秒一次且不判 busy，长采集时堆叠线程 | 加 `refreshInFlight` 闸门 |
| 4 | 1.0「前台」口径用总 CPU，和「全部」没区别 | 改为「全部 / 前台(总CPU) / 后台(息屏CPU+息屏锁) / 前台服务(fgs)」并在标题写清 |
| 5 | 采集失败也占用 2 分钟冷却 | 冷却改为按「最后一次成功采集」判断 |
| 6 | 电量计不可用时写假数据 | 写 0，读取端显式跳过 |
| 7 | `resources.arsc` 被压缩，Android 11+ 拒装 | 用 `aapt2 link --no-compress` + zip Update 模式追加 dex |

## 五、验证结果

```
采样点        : 96
真实放电      : 663.0 mAh
充入          : 3351.0 mAh
跳过充电段    : 11
跳过跳变      : 0
跨度          : 9.78 h
放电速率      : 67.8 mA
               : 1.13 %/h
```

合成用例（10+10+10 放电、500 充电跳变）：
```
采样点        : 5
真实放电      : 30.0 mAh      ← 与期望完全一致
充入          : 500.0 mAh
跳过充电段    : 1
```

进程开销：`10s CPU: 0 ms`、`VmRSS 187 MB`（Framework）、`VmSwap 52 MB`。

## 六、文件位置

```
D:\CODEX\w\powerlens\
├─ app2\                        ← PowerLens 1.1 源码 + 构建
│  ├─ build.ps1                 ← 手工构建（不依赖 Gradle）
│  ├─ AndroidManifest.xml
│  ├─ res\
│  ├─ src\com\powerlens\app\
│  │  ├─ MainActivity.java      UI + 四口径
│  │  ├─ Collector.java         采集调度（C 优先，dumpsys 兜底）
│  │  ├─ Procfs.java            procfs 数据源
│  │  ├─ Bstats.java            dumpsys 解析器
│  │  ├─ Storage.java           ★ 电量核算（核心修复）
│  │  ├─ Shell.java             带超时的 shell
│  │  └─ CollectJobService.java
│  └─ out\PowerLens-1.1.apk     ← 33 KB，已签名
├─ c\
│  ├─ plcollect.c               ★ C 采集器（11 KB 静态 ELF）
│  ├─ build-c.ps1               NDK 交叉编译
│  └─ out\plcollect             arm64
├─ src\, src2\, extract\        1.0 反编译产物（对照用）
├─ app_snapshots.tsv            1.0 历史数据（分析用）
└─ powerlens.apk                1.0 原始 APK
```

手机端：

```
/data/local/tmp/plc/plcollect           C 采集器（root 执行）
/data/local/tmp/plc/plc-daemon.sh       守护脚本（600 s）
/data/adb/service.d/40-plc.sh           开机拉起
/data/adb/service.d/30-powerlens.sh.disabled   旧 dumpsys 采集器（已禁用）
/data/data/com.powerlens.app/files/snapshots.tsv  数据（C 守护写，App 读）
```

## 七、常用命令

```bash
# 手动采一次
su -c "/data/local/tmp/plc/plcollect --once -f /data/data/com.powerlens.app/files/snapshots.tsv"

# 核算现有数据（正确算法）
su -c "/data/local/tmp/plc/plcollect --status -f /data/data/com.powerlens.app/files/snapshots.tsv"

# 守护状态
ps -A | grep plc-daemon
tail /data/local/tmp/plc/daemon.log
```

## 八、还没做的

1. **息屏维度缺失**：procfs 没有按 UID 的息屏 CPU，需要的话得给 dumpsys 路径加低频开关
   （UI 上「全部口径」右键或长按采集按钮触发一次 dumpsys）
2. **C 版 plcollect 的 `--loop` 未接入**：目前靠 `plc-daemon.sh` 的 shell 循环，可以合并
3. **arm32 版本未编译**：`build-c.ps1` 里写了但本机只需要 arm64