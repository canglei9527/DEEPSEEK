# PowerLens — Android 功耗归因工具

一个**不依赖 Gradle、不依赖 App 拿 root** 的 Android 功耗归因方案。
用 C 采集器（root 侧常驻）+ Java UI（只读文件）的组合，
把「谁在耗电」量化到每个 UID，并给出可对照的基线。

> 开发/验证机型：Xiaomi 15 Pro `2410DPN6CC`（`haotian`）/ Android 16 / KernelSU
> 所有数据均来自真机实测，不是理论值。

---

## 它解决什么问题

系统自带的耗电统计有两个致命缺陷：

1. **`dumpsys batterystats` 太贵** —— 实测 666 ms、280 KB，且这部分 CPU
   记在 `system_server` 头上，用户在任何统计里都**看不见**。
2. **应用层的数字不可信** —— 常见的「首值减末值」算法在区间内有充电时
   会把充进去的电算成消耗。本项目实测某工具虚高 **4.5 倍**
   （显示 2813 mAh，真实 629 mAh）。

PowerLens 的做法：

| | 传统做法 | 本项目 |
|---|---|---|
| 采集频率 | 10 分钟一次 dumpsys | **60 秒** procfs（97 ms / 12 KB） |
| CPU 归属 | 记在 system_server（隐藏成本） | 内核，不计入任何进程 |
| 电量核算 | 首值 − 末值（充电即错） | 逐对累加，只算下降、跳过充电段 |
| App root | 需要（每次重装就丢授权） | **不需要**（采集全在 root 侧） |

---

## 架构

```
┌──────────────────────────────────────────────────────┐
│ root 侧（常驻，开机自启）                              │
│                                                       │
│  /data/adb/service.d/40-plc.sh                        │
│    └─ plc-daemon.sh  [CPU间隔=60s] [维度间隔=1200s]    │
│         ├─ plcollect  (procfs)   → snapshots.tsv      │
│         │    └─ /proc/uid_cputime + /proc/uid_io      │
│         └─ pldims     (dumpsys)  → dims.tsv (增量)    │
│              └─ fg/bg/fgs/cpuOff/wakeLock 维度         │
└──────────────────────────────────────────────────────┘
                        ↓ 读（文件权限 666）
┌──────────────────────────────────────────────────────┐
│ App 侧（零 root / 零后台 / 零 JobScheduler）           │
│  MainActivity + Storage.summarize + Dims              │
└──────────────────────────────────────────────────────┘
```

**为什么 App 不需要 root**：实测本机权限矩阵 —— `charge_counter`、
`capacity`、`/proc/uid_cputime`、`/proc/uid_io` 对 App 全部 `EACCES`，
`su -c` 也被 KernelSU 拒。所以 App 侧怎么改都拿不到真数据。
把采集搬到 root 侧后，App 只读文件，**KernelSU 授权可以完全撤销**。

---

## 两个采集器为什么分开

| 数据源 | 成本 | 有 CPU/IO | 有 fg/bg/fgs/wl |
|---|---|---|---|
| `/proc/uid_cputime` + `/proc/uid_io` | **97 ms / 12 KB** | ✓ | ✗ |
| `dumpsys batterystats` | 666 ms / 280 KB | ✓ | **只有它有** |

- **CPU/IO 要高频**（60 秒）→ 用 procfs
- **维度要准**（前台/后台/前台服务/唤醒锁）→ 只能用 dumpsys，低频（20 分钟）

`pldims` 输出的是**增量**（`Δfg = fg_now - fg_prev`），不是累计值。
用累计值排序等于「开机以来谁在前台待得久」，与当前区间无关 —— 这是踩过的坑。

---

## 电量核算算法（核心）

```
逐对相邻采样点：
  d = prev.count − cur.count

  d > 0 且 d ≤ 上限   → 记为放电，累加
  d > 0 但 d > 上限   → 判定为电量计跳变/校准，丢弃并计数
  d ≤ 0              → 充电或持平，跳过（不计负、不回退）

  上限 = max(50 mAh, 15 mAh/min × 间隔分钟数)
```

**兼容三代数据格式**：
- 单位自适应：第 4 列 < 100000 视为 mAh，否则视为 µAh
- 状态列缺失（`0`/`?`/空）视为「未知」，按放电处理
- 识别伪数据行（无状态无容量）并跳过

---

## 四种口径

| 口径 | 权重 | 含义 |
|---|---|---|
| 全部 | `CPU秒 + 息屏CPU秒 + 唤醒锁×0.6 + 真实流量MB×0.44` | 综合 |
| 前台 | `√(fg秒 × CPU秒)` | 前台且真在跑 |
| 后台 | `√(bg秒 × CPU秒)` | 后台且真在跑 |
| 前台服务 | `√(fgs秒 × CPU秒)` | 前台服务 |

**为什么前台用开平方**：实测 dumpsys 给多个系统服务报了完全相同的 fg 值
（8 个 UID 全是 170669 ms），纯按 fg 排序就是并列 12.5%。
乘上 CPU 活跃度后再开平方，既能体现「在前台」又能体现「真在跑」。

---

## 构建

不依赖 Gradle，纯 `build-tools` + `javac`。

```powershell
# 1. C 采集器（需要 NDK）
cd c
.\build-c.ps1

# 2. APK（需要 JDK17 + build-tools 35 + platform android-35）
cd app2
.\build.ps1
```

**构建时的一个硬要求**：
```powershell
aapt2 link --no-compress ...     # resources.arsc 必须 STORED
```
否则 Android 11+ 报
`requires the resources.arsc ... stored uncompressed and aligned on a 4-byte boundary`。
且**不能解压-重打包**（会把 arsc 变回 compressed），只能在 aapt2 产出的 apk 上
用 zip Update 模式追加 `classes.dex`。

---

## 部署

```bash
# 手机侧（root）
su -c "sh /data/local/tmp/plc/bootstrap.sh"     # 重建数据集 + 起守护
su -c "sh /data/local/tmp/acceptance2.sh"       # 13 项验收
su -c "sh /data/local/tmp/set-interval.sh 300"  # 调整采样间隔
```

```powershell
# PC 侧
.\reconnect.ps1 -Loop    # 无线调试端口每次重连都变，自动扫
```

---

## 踩过的坑（重要）

### 1. KernelSU 的 su 边界

| 写法 | 结果 |
|---|---|
| `su -c "id"` | ✓ root |
| `su -c "id; echo x"` | ✓ 只有第一条是 root |
| `su -c "echo x; id"` | ✗ 第二条掉回 shell |
| `su -c "sh script.sh"` | 脚本本身 root，但内部 `cp`/`awk`/`ls` 会掉 |
| `echo x \| su -c tee file` | ✓ tee 是 root（写 sysfs 必须这样） |
| `su -c "echo x > file"` | ✗ 重定向由普通 shell 执行 |

### 2. App 升级不能用 `pm uninstall`

KernelSU/Magisk 的 root 授权按 **(uid, APK 签名)** 记录。
`pm uninstall` 会让 App 换新 uid，授权立即失效。
**一律用 `pm install -r`**。

### 3. 数据文件里不要写非 ASCII

C 程序写 UTF-8 中文，读取端在非 UTF-8 locale 下会变成 `è Ç`。
改为 C 只写 ASCII 标识（`sys:kernel`），由 Java 侧翻译。

### 4. `packages.list` 有共享 UID

同一 uid 可能对应多个包（`sharedUserId`），无条件覆盖会把
`uid 1000` 标成 `com.xiaomi.digitalkey`。系统 UID（<10000）必须保护。

### 5. 不要猜系统 UID 的语义

不同 Android 版本 / OEM 的 UID 分配不同。硬编码表猜错会造出
「CPU 0m 却占 60%」这种假条目。查询失败就诚实显示 `(uid N)`。

### 6. `dur()` 的分辨率要匹配数据精度

数据是毫秒级，显示成 `0m` 会让人以为没数据。`<1s` 显示 ms，`<60s` 显示秒。

### 7. Windows 热点会空闲自动关闭

长测量必须开保活脚本，否则手机失联。

---

## 实测基线（Xiaomi 15 Pro / Android 16）

```
采集开销：plcollect 97 ms / 12 KB / 150 UID
         pldims    ~800 ms / 80 KB / 增量（20 分钟一次）
App 常驻：0 ms / 10 s（只读文件）

待机电流：67.8 mA（1.13 %/h）—— 最好是时候
         116–225 mA —— 恶化时

唤醒源（60 秒增量）：
  eventpoll       2429 次/秒（应用层 EPOLLWAKEUP）
  qrtr_ws         1579 次/秒（Qualcomm IPC Router）
  [timerfd]        460 次/秒（累计真唤醒 14425 次）
  gh_vcpu_ws_45_*  累计 46 万（gunyah hypervisor vCPU）
```

---

## 已知限制

1. **procfs 没有息屏维度** —— 需要 dumpsys，所以是低频
2. **`dumpsys` 的 fg 对系统服务没区分度** —— 已用 `√(fg×CPU)` 补偿
3. **亮屏静止功耗仍在 200+ mA** —— 已定位到 `CWB`/`boledalgo` 每 70ms 轮询，
   但还没找到关闭方法
4. **`logd` 占 1.85% 单核** —— 已 A/B 证伪「是采集导致的」，实际约 0.03W

---

## 目录结构

```
PowerLens/
├── c/
│   ├── plcollect.c        主采集器（procfs，CPU/IO）
│   ├── pldims.c           维度采集器（dumpsys，增量）
│   └── build-c.ps1        NDK 交叉编译
├── app2/
│   ├── build.ps1          手工构建（无 Gradle）
│   ├── AndroidManifest.xml
│   └── src/com/powerlens/app/
│       ├── MainActivity.java    UI + 四口径 + 时间窗口
│       ├── Storage.java         ★ 电量核算 + 增量 + 来源识别
│       ├── Dims.java            维度读取 + secondsOf
│       ├── Collector.java       只读文件
│       ├── Procfs.java          procfs 数据源
│       └── Bstats.java          dumpsys 解析
├── scripts/
│   ├── phone/01-core/         手机端常驻
│   ├── phone/02-diagnose/     诊断脚本
│   └── *.ps1                  PC 侧工具
└── docs/
    ├── POWER-DIAG-PLAYBOOK.md      方法论
    ├── FOREGROUND-FIX.md           前台口径修复
    ├── ACCEPTANCE.md               验收
    └── NOACTIVE-FINDINGS.md        NoActive 排查
```

---

## 许可

个人项目，供学习与自用。