# PowerLens 第三轮修复（数据一致性问题）

> 2026-11-09 13:40–14:00
> 目标：让工具的数字真正可信，能当以后分析功耗的基线参考

---

## 本轮修的问题

### 1. 「CPU 0m 却占 34.6%」—— 旧数据污染

**现象**：列表里 `external_storage` 占 34.6%、`Shell` 占 16.5%，但 CPU 都显示 `0m`。

**根因**：数据文件里混着**三个时代**的数据，互相污染：

| 时代 | 电池行格式 | pkg 来源 | 问题 |
|---|---|---|---|
| 1.0 原始 | mAh 整数，状态全 `0` | dumpsys 自带包名 | 无法分辨充放电 |
| 1.1 中期 | mAh（BatteryManager 伪值） | App 自己写 | 伪数据 |
| 1.2 起 | **µAh + 真实状态** | `packages.list` 补全 | 正确 |

`external_storage` 只在旧数据里出现过，它的 CPU 首值和末值跨越了数据源切换，
算出来的「增量」毫无物理意义。

**修法**：`clean-hard.sh`
1. 按时间戳分组，统计每批的「UID 行数」和「问号(pkg=?)行数」
2. 找最后一个「问号率 ≥ 10%」的批次
3. 从它的下一批开始全部保留

实测结果：
```
清洗前: 4841 行，问号 2227 (46%)
清洗后:  456 行，问号    9 (1%)    ← 144 个唯一包名
```

### 2. `pkg=?` 占一半 —— C 采集器拿不到包名

**根因**：`plcollect.c` 原来只用 `/proc/<pid>/cmdline` 取包名，
但那只能拿到**当前有进程在跑**的 UID。
快照里大部分系统服务/已缓存应用当时没有进程，于是 pkg 大面积是 `"?"`
（实测 23540 行里 11088 行是 `?`）。

**修法**：在 C 里加了 `load_pkg_from_list()`，读 `/data/system/packages.list`
（格式 `<pkg> <uid> <debuggable> <dataDir> ...`），这是权威映射，且只在 root 下可读 ——
正好由 root 运行的采集器来做。

实测结果：
```
修复前: 151 UID 里 46 个 pkg=?
修复后: 151 UID 里  3 个 pkg=?   (98% 补全率)
```

包名样例：`com.google.android.webview`、`com.xiaomi.aiservice`、
`com.miui.misound`、`com.tencent.mobileqq`、`com.android.contacts`…

### 3. 时间窗口切进充电段 —— 电流虚高

**现象**：UI 显示 318 mA，但全量核算只有 122.9 mA。

**根因**：固定「今天 0 点起」的窗口会把夜间充电、白天充电的大段区间算进来，
`878 mAh ÷ 2.8 h = 313 mA` 这种数字虽然自洽，但反映的是「含充电前后」的混合状态。

**修法**：加时间窗口选择器（近1时 / 近6时 / 近24时 / 全天），默认近 6 小时。

### 4. 系统聚合 UID 霸榜

**现象**：`Android 系统`、`root/内核` 永远排第一，把真实应用挤下去。

**根因**：它们是**聚合容器** —— `root/内核(uid 0)` 是所有内核线程之和，
`Android 系统(uid 1000)` 是几十个系统服务之和。和「微信」这种单一应用并列比不公平。

**修法**：`isAggregate()` 识别聚合 UID（0 / 1000 / 1001 / 9999 / 10000-10499），
长按「采集」按钮切换显示/隐藏。实测：隐藏后进程数从 **111 → 38**。

### 5. `外部存储 895 MB 流量` —— 把文件 I/O 当网络流量

**根因**：`/proc/uid_io/stats` 的 `rchar`/`wchar` 是**文件读写字节数**，
包含读 `/proc`、读数据库、写日志。uid 0 有 15 GB 是因为 init 大量遍历 /proc。
而 dumpsys 的 `wifi=`/`mobile=` 才是真网络流量。

**修法**：`hasRealTraffic()` 只对 dumpsys 源计入流量权重；
procfs 源在 UI 上标注为 `IO` 而不是流量，且不参与流量加权。

### 6. 端口每次重连都变

**修法**：`reconnect.ps1` 自动扫 30000-61000，验证 `ro.product.model == 2410DPN6CC` 后返回地址。

---

## 当前状态（2026-11-09 13:55）

### 数据健康度

```
行数: 456
问号: 9 / 453 = 1%        ← 修复前 46%
唯一包名: 144
电池行: 全部 µAh 格式
```

### 采集器

```
/data/local/tmp/plc/plcollect   11536 B  含 packages.list 补全
/data/local/tmp/plc/plc-daemon.sh  v2  含权限自愈 + 单实例保护
/data/adb/service.d/40-plc.sh   开机自启
```

### 架构

```
root 侧：plc-daemon.sh → plcollect → snapshots.tsv
App 侧：只读文件，零 root
```

**App 在 KernelSU 里的 root 授权可以完全撤销**，功能不受影响。

---

## 下一步（数据积累后）

当前只有 3 个采样点（0.03 小时跨度），因为旧数据全被清洗掉了。
守护进程每 10 分钟采一次，`seed.sh` 可以手动快速累积。

等积累到足够样本（建议 ≥ 10 个点 / ≥ 1 小时）后：
1. 跑 `drain-audit.sh` 建立干净的基线
2. 用 `ab-test.sh` 做 A/B 实验（swappiness / deep / adsprpcd）
3. 追查 `com.android.dynsystem` 为什么持续吃 CPU

---

## 工具速查

```bash
# 数据清洗（旧数据混入时）
su -c "sh /data/local/tmp/clean-hard.sh"

# 快速积累样本
su -c "sh /data/local/tmp/seed.sh 10 60"

# 自检（不依赖 adb）
su -c "sh /data/local/tmp/selfcheck.sh"

# 重连（端口自动发现）
.\reconnect.ps1 -Loop

# 全量诊断
su -c "sh /data/local/tmp/drain-audit.sh full"
```