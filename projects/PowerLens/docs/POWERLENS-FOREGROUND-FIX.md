# 「前台/后台/前台服务」口径修复记录

> 2026-11-09 15:20
> 用户报的两个问题：① uid 2000 是什么 ② 选择前台啥都不显示

---

## 一、uid 2000 是什么

**`shell` 用户，adbd 的调试身份。**

```
来源：adbd 通过 shell 用户执行 adb shell 命令
apps：无（不是普通应用）
```

它出现在耗电榜上，**是这个工具在调试手机时自己产生的开销**：
我执行的每条 `adb shell` 都在以 uid 2000 跑命令。
用户正常使用（不连 adb）时它归零。

**已修**：C 采集器加了 `case 2000: return "sys:adb_shell"`，
App 侧 `SYS_LABEL` 加 `sys:adb_shell → "Shell 调试通道"`。

---

## 二、「前台」空白的根因（分两层）

### 第一层：数据源没有这个维度

原架构只有一个采集器 `plcollect`：

| 数据源 | 有 CPU/IO | 有 fg/bg/fgs/cpuOff/wl |
|---|---|---|
| `/proc/uid_cputime` | ✓ | ✗ |
| `/proc/uid_io` | ✓ | ✗ |
| `dumpsys batterystats` | ✓ | **只有它有** |

为了保证速度（97ms vs 666ms）选了 procfs，代价就是丢掉全部维度。

**UI 里那段代码**：
```java
if (mode == 1) {                    // 前台
    w = r.wFg();
    if (r.fgMs <= 0) { w = 0; }    // ← fgMs 恒为 0，每条都被清零
}
```
实测 755 行数据：`fgMs 非零 0 行 / bgMs 0 行 / fgsMs 0 行 / cpuOffMs 0 行 / wlMs 0 行`
→ 三个口径全空。

### 第二层：修了之后又踩了两个坑

**坑 1：用累计值排序没有意义**

第一版 `pldims` 输出 dumpsys 的**累计值**（开机以来）。
实测 `Σfg = 290 小时`，单个 UID 只有 6-7 小时 —— 排序出来是
「开机以来谁在前台待得久」，和「这 1 小时谁在耗电」完全无关。

**坑 2：按比例分配 CPU 会被过滤掉**

第二版试图用 `dims` 占比切分 `snapshots` 的 CPU 增量：
```
cpu_fg(uid) = Δcpu(uid) × fg(uid)/Σfg
```
但 `ratio` 只有 0.02 左右，乘上几十秒的 CPU 后只剩零点几秒，
被 `w <= 0.001` 过滤 → 整屏空白（实测只剩 4 个进程）。

---

## 三、最终方案（方案 A）

### 架构：双采集器

```
plcollect （高频 60s）→ procfs   → CPU / IO      → snapshots.tsv
pldims    （低频 1200s）→ dumpsys → fg/bg/fgs/wl → dims.tsv（增量）
```

### pldims v2 的关键改动：输出增量

每次运行保存上一份累计值到 `dims.prev`，本次算 Δ：

```
Δfg = fg_now - fg_prev
```

这样 `dims.tsv` 里的数就是「距上次采样这段时间内的前台/后台时长」，
与 `snapshots.tsv` 的区间增量口径一致，可以直接排序。

**输出格式（9 列）**：
```
<epochMs> <uid> <ΔfgMs> <ΔbgMs> <ΔfgsMs> <ΔcpuOffMs> <ΔwlMs> <ΔwlOffMs> <intervalMs>
```

**首次运行**只写 `dims.prev` 基线，`dims.tsv` 留空（App 会提示维度数据不足）。

### App 侧口径

```java
if (mode == 1 || mode == 2 || mode == 3) {
    double sec = dims.secondsOf(r.uid, mode);   // 增量秒数
    if (sec <= 0.001d) continue;                // 该维度为 0 → 不显示
    w = sec + trafficPart;
}
```

**权重单位**：秒。mAh 仍按总放电量分摊（`share = weight / Σweight`）。
含义清晰：「前台」= 这段时间内谁在前台待得最久，按这个比例分摊实测耗电。

---

## 四、实测验证

### pldims 数据正确

```
增量前（累计）：Σfg = 290.0 小时  Σbg = 504.2 小时  Σfgs = 189.1 小时
                 cpuOff = 6.6h    wl = 1.3h        wlOff = 0.8h

UID 覆盖：bg>0 有 135 个，fg>0 有 74 个，fgs>0 有 60 个
与 snapshots 的 UID 交集：145 / 155（94%）
```

### UI 已能显示

```
前台口径：按前台时长排序（dumpsys fg）
维度数据 7 分钟前（165 个 UID）
```

列表能出数据（截图确认 8 个进程、正常 mAh 与百分比）。

---

## 五、当前状态与待验证

### 已完成
- `pldims.c` v2（增量版，8976 字节）
- `plc-daemon.sh` v4（双采集器：CPU 60s / 维度 1200s）
- `Dims.java`（读 9 列增量格式 + `secondsOf()`）
- `MainActivity` 三种口径改用 dims 增量时长
- uid 2000 → "Shell 调试通道"

### 待验证
1. pldims 第二次运行产出的增量是否合理（需等 20 分钟）
2. 三种口径显示的进程数是否足够（预期 74/135/60）
3. 权重排序是否符合直觉（前台时间长的排前面）

### 采样节奏
```
snapshots: 60 秒一次（CPU/IO）
dims:      1200 秒一次（维度增量）
```
dims 间隔 20 分钟是为了控制 dumpsys 的开销（每次 666ms + 主频抖动）。
想更细可以改 `plc-daemon.sh` 的第二个参数。

---

## 六、工具

```
D:\CODEX\w\powerlens\c\
├─ plcollect.c          主采集器（procfs，CPU/IO）
└─ pldims.c             维度采集器 v2（dumpsys，增量）

D:\CODEX\w\powerlens\app2\src\com\powerlens\app\
├─ Dims.java            维度读取 + secondsOf()
├─ Storage.java         区间核算
└─ MainActivity.java    四种口径 + 时间窗口 + 隐藏聚合UID

D:\CODEX\w\scripts\phone\
├─ 01-core\plc-daemon.sh    v4 双采集器守护
├─ 02-diagnose\dims-audit.sh  维度数据审计
└─ 02-diagnose\sel-mode.sh    用键盘切换口径（点击坐标不可靠）
```

---

## 七、KernelSU su 边界（再次强调，这轮又踩多次）

| 写法 | 结果 |
|---|---|
| `su -c "id"` | ✓ root |
| `su -c "id; echo x"` | ✓ 第一条 root，后面掉回 shell |
| `su -c "echo x; id"` | ✗ 只有第一条 |
| `su -c "sh script.sh"` | 脚本本身 root，但内部 cp/awk/ls/stat 会掉 |
| `echo x \| su -c tee file` | ✓ tee 是 root（写 sysfs 必须这样） |
| `su -c "echo x > file"` | ✗ 重定向由普通 shell 执行 |
| `input tap/keyevent` | ✗ INJECT_EVENTS 被拒（改用 UIAutomator 或直接改配置） |

**可靠做法**：PC 侧生成内容 → `adb push` → 单条 `su -c "cp ..."`。