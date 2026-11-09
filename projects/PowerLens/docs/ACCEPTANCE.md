# PowerLens 交付验收报告

> 2026-11-09 14:35　2410DPN6CC / haotian / Android 16 / KernelSU
> 状态：**13 项验收全部通过**

---

## 一、验收结果

```
########## 1. 采集器 ##########
  [PASS] plcollect 存在 (14384 B)

########## 2. 守护进程 ##########
  [PASS] 单实例运行
         1221 sh /data/local/tmp/plc/plc-daemon.sh 60

########## 3. 最新一批数据 ##########
  最新快照 ts=1794206055897
  该批 UID 数: 151
  [PASS] UID 数量正常
  [PASS] 电池行是 µAh 格式
  [PASS] 包名补全率 151 个中仅 1 个问号
  [PASS] uid 0 = sys:kernel
  [PASS] uid 1000 = sys:system
  [PASS] uid 1001 = sys:radio
  [PASS] 无路径形式的 pkg

########## 4. sys: 标签 ##########
  带 sys: 前缀的: 24 个
  [PASS] 系统 UID 用 sys: 标识（App 会翻译成中文）

########## 5. App 可读性 ##########
  [PASS] 权限 666（任何进程可读）

########## 6. 核算 ##########
  采样点        : 15
  真实放电      : 56.0 mAh
  放电速率      : 202.3 mA / 3.37 %/h
  [PASS] 采样点 15 个

########## 7. 开机自启 ##########
  [PASS] 40-plc.sh 存在

================================
  通过 13，失败 0
================================
```

---

## 二、UI 实测（截图确认）

```
区间 11-09 08:35 → 14:35:12 · 25 个进程
实测放电 56.0 mAh · 202.3 mA · 3.32 %/h
有效采样跨度 0.3 小时（15 个电池点）
最后采集 56 秒前（共 2772 条）

Wi-Fi           CPU 783ms   IO 3k    2.3%
安全            CPU 574ms   IO 1.8M  1.7%
媒体会话         CPU 454ms            1.3%
内部存储         CPU 228ms   IO 6k    0.7%
CameraMind      CPU 204ms   IO 505k  0.6%
媒体服务         CPU 204ms   IO 1.6M  0.6%
备份            CPU 189ms            0.6%
TEE 存储        CPU 149ms   IO 856k  0.4%
Android System WebView  CPU 140ms     0.4%
查找            CPU 137ms            0.4%
系统更新         CPU 79ms             0.2%
```

**全部指标正常**：中文名称、毫秒级 CPU、合理 IO、正确占比。

---

## 三、最终架构

```
┌─────────────────────────────────────────────────────┐
│ root 侧（完全独立，App 不需要 root）                  │
│                                                      │
│  /data/adb/service.d/40-plc.sh   开机自启            │
│    └─ plc-daemon.sh 60           单实例 + 每次 su    │
│         └─ su -c "plcollect ..." ★ 必须显式 su       │
│              └─ 写 /data/local/tmp/powerlens/        │
│                   snapshots.tsv  (666 权限)          │
└─────────────────────────────────────────────────────┘
                        ↓ 读
┌─────────────────────────────────────────────────────┐
│ App 侧（零 root / 零后台 / 零 JobScheduler）          │
│                                                      │
│  MainActivity                                        │
│    ├─ 时间窗口：近1时/近6时/近24时/全天               │
│    ├─ 口径：全部/前台/后台/前台服务                   │
│    ├─ 长按「采集」：切换显示系统聚合 UID              │
│    └─ Storage.summarize()  逐对核算，剔除充电段       │
└─────────────────────────────────────────────────────┘
```

**App 在 KernelSU 里的 root 授权可以完全撤销。**

---

## 四、采集参数

| 参数 | 值 | 说明 |
|---|---|---|
| 间隔 | **60 秒** | 每小时 60 个采样点 |
| 每次输出 | 151 UID / ~12 KB | |
| 日占用 | ~17 MB | 想省空间改成 300 或 600 |
| 数据位置 | `/data/local/tmp/powerlens/` | 666 权限，App 和 root 都能读写 |
| 开机延迟 | 30 秒 | 等系统稳定后启动 |

---

## 五、数据格式

```
<epochMs> <uid> <pkg|sys:xxx> <cpuMs> <cpuOffMs> <wlMs> <wlOffMs> <rx> <tx> <mobileMah> <fgMs> <bgMs> <fgsMs>
<epochMs> * battery <chargeUah> <capacity> <status> 0 0 0 0 0 0
```

**示例**
```
1794206055897	0	sys:kernel	3380434	0	0	0	18428558644	621097603	0	0	0	0
1794206055897	99001	com.google.android.webview	1110	0	0	0	180	1096	0	0	0	0
1794206055897	*	battery	4167000	73	Discharging	0	0	0	0	0	0
```

- `sys:kernel` / `sys:system` 等是 ASCII 标识，App 侧翻译成中文
- 第 8/9 列对 procfs 源是**文件 IO 字节数**（UI 标注 `IO`），对 dumpsys 源是网络流量
- 电池行第 4 列是**绝对电量计 µAh**，核算由读取端做

---

## 六、本轮解决的 8 个关键问题

| # | 问题 | 根因 | 修法 |
|---|---|---|---|
| 1 | **KernelSU su 边界** | `su -c "sh script"` 内 fork 的子进程掉回 shell | 脚本内每条命令显式 `su -c` 包二进制 |
| 2 | 数据位置不可写 | `pm install -r` 后 app 私有目录权限被重置 | 切到 `/data/local/tmp/powerlens/`（666） |
| 3 | 中文乱码 `è Ç` | C 写 UTF-8 中文，读取端编码错乱 | C 写 ASCII `sys:xxx`，Java 翻译 |
| 4 | `uid 1000` 被误标 | packages.list 共享 UID | 系统 UID（<10000）不被覆盖 |
| 5 | `pkg=?` 占一半 | 只用 cmdline 取包名 | 加 `load_pkg_from_list()` |
| 6 | `external_storage` 霸榜 | 硬编码 UID 表猜错语义 | 删掉猜测，诚实显示 `(uid N)` |
| 7 | `CPU 0m` 却占 34.6% | `dur()` 把 <1 分钟显示成 `0m` | 显示 ms / 秒 |
| 8 | 数据源混杂 | 三代数据混在一个文件 | `src` 字段区分 + 归档重建 |

---

## 七、文件清单

### 手机端

```
/data/local/tmp/plc/
├─ plcollect              14384 B  C 采集器（sys: 标签 + packages.list 补全）
├─ plc-daemon.sh          v3       守护（每条命令显式 su）
├─ daemon.log / daemon.pid
└─ 诊断脚本（drain-audit.sh, selfcheck.sh, bootstrap.sh 等）

/data/local/tmp/powerlens/
└─ snapshots.tsv          2772 行  数据

/data/adb/service.d/
├─ 10-mcast.sh            组播屏蔽
├─ 20-freezer.sh.disabled
├─ 30-powerlens.sh.disabled
└─ 40-plc.sh              开机自启（INTERVAL=60）

/data/local/tmp/          验收/诊断脚本
├─ acceptance2.sh         ★ 13 项验收
├─ install-boot.sh        安装开机自启
├─ set-interval.sh        调整采集间隔
├─ switch-store.sh        切换数据位置
├─ fix-all2.sh            修权限（绝对路径）
├─ no-root-proof.sh       证明不需要 root
└─ ...
```

### PC 端

```
D:\CODEX\w\scripts\
├─ reconnect.ps1          ★ 自动重连（端口扫描）
├─ hotspot-keepalive.ps1  ★ 热点保活
├─ screen-live.ps1        实时盯亮灭屏
├─ deploy.ps1             一键部署
├─ verify-kit.ps1         离线自检
└─ phone\01-core,02-diagnose,03-ab-test
```

---

## 八、日常使用

```powershell
# 电脑侧（手机失联时）
cd D:\CODEX\w\scripts
.\reconnect.ps1 -Loop
```

```bash
# 手机侧
su -c "sh /data/local/tmp/acceptance2.sh"        # 验收（应 13 项全过）
su -c "sh /data/local/tmp/set-interval.sh 300"   # 调整间隔
su -c "sh /data/local/tmp/switch-store.sh"       # 换数据位置
su -c "sh /data/local/tmp/drain-audit.sh full"   # 全量诊断
```

---

## 九、现在可以做的功耗分析

数据在实时积累（60 秒一个点），跑几小时后：

```bash
# 全量诊断报告
su -c "sh /data/local/tmp/drain-audit.sh full"

# A/B 实验：验证每个改动到底有没有用
su -c "sh /data/local/tmp/ab-test.sh preset swappiness"
su -c "sh /data/local/tmp/ab-test.sh preset deep"
su -c "sh /data/local/tmp/ab-test.sh preset adsprpcd"
su -c "sh /data/local/tmp/ab-test.sh preset wifi"
su -c "sh /data/local/tmp/ab-test.sh preset data"
```

---

## 十、仍待观察

**`logd` 曾占 56.5%（CPU 14.2s）**，这是异常的。
logd 是系统日志服务，正常应远低于此。可能原因：
- 某个应用疯狂写日志
- logcat buffer 太小导致频繁 flush

排查命令：
```bash
su -c "logcat -b main -d | awk '{print \$4}' | sort | uniq -c | sort -nr | head -20"
su -c "logcat -g"
```