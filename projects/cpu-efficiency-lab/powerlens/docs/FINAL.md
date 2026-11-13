# PowerLens 最终交付（1.2 定稿）

> 2026-11-09 14:25　2410DPN6CC / haotian / Android 16 / KernelSU
> 目标：做到「数字可信、可直接当功耗分析基线」

---

## 一、最终状态（实测确认）

### UI 显示

```
区间 11-09 08:21 → 14:21:28 · 22 个进程
实测放电 41.0 mAh · 215.7 mA · 3.54 %/h
有效采样跨度 0.2 小时（14 个电池点）
最后采集 11 秒前（共 2131 条）

logd        CPU 14.2s   IO 14.0M   56.5%
(uid 2000)  CPU 4.2s               16.6%
蓝牙         CPU 1.5s   IO 417k     6.2%
音效/振动    CPU 1.0s   IO 1.2M     4.1%
NFC         CPU 863ms   IO 540k     3.4%
网络共享     CPU 661ms              2.6%
Wi-Fi       CPU 490ms   IO 4k       2.0%
媒体会话     CPU 328ms              1.7%
安全         CPU 156ms   IO 31k     0.6%
```

### 架构（稳定）

```
root 侧：
  /data/adb/service.d/40-plc.sh   →  开机自启
    └─ plc-daemon.sh v3           →  单实例 + 权限自愈
         └─ su -c "plcollect ..." →  ★ 必须显式 su 包二进制
              └─ 写 /data/local/tmp/powerlens/snapshots.tsv

App 侧：
  MainActivity → Storage.storeFile() 自动选行数多的位置 → 显示
  零 root / 零后台 / 零 JobScheduler
```

**App 在 KernelSU 里可以完全撤销 root 授权。**

---

## 二、本轮解决的关键技术问题

### 1. KernelSU 的 su 边界（最坑的一个）

```bash
su -c "/data/local/tmp/plc/plcollect --once"        # ✓ plcollect 是 root
su -c "sh /data/local/tmp/daemon.sh"                # ✗ 脚本内 fork 的 plcollect 掉回 shell
su -c "sh -c 'plcollect --once'"                    # ✗ 多套一层就丢
```

**修法**：守护脚本里每条需要 root 的命令都显式 `su -c` 包一次，
且必须**直接执行二进制**，不能经过 `sh -c`。

### 2. 数据位置：从 app 私有目录切到 /data/local/tmp

`pm install -r` 之后，`/data/data/com.powerlens.app/files/` 出现过
root 也无法写入的情况（SELinux 标签或目录权限被重置）。

改用 `/data/local/tmp/powerlens/`（`shell_data_file`，权限 777），
root 和 App 都能读写。App 的 `storeFile()` 已经实现「比较两个位置行数，选多的」，
所以不需要改 APK。

### 3. 中文不能从 C 写进数据文件

C 程序写 UTF-8 中文，读取端在非 UTF-8 locale 下会显示成 `è Ç`。

**修法**：C 只写 ASCII 标识 `sys:kernel`，Java 侧用 `SYS_LABEL` 表翻译成「root/内核」。

### 4. packages.list 的共享 UID 陷阱

```
uid 1000 在 packages.list 里同时对应 system_server 和 com.xiaomi.digitalkey
无条件覆盖 → 「Android 系统」消失，变成一堆莫名其妙的包名
```

**修法**：系统 UID（< 10000）不被 `packages.list` 覆盖。

### 5. 不要猜系统 UID 的语义

我硬编码的表把 1077 写成「上下文中心」，真机是 `external_storage`，
导致 UI 出现「CPU 0m 却占 60%」的假条目。

**修法**：删掉猜测，改为「采集器给名 → PackageManager → 诚实显示 `(uid N)`」。

### 6. dur() 的分辨率必须匹配数据精度

数据是毫秒级（`1.1s`、`98ms`），但 `dur()` 把 <1 分钟统一显示成 `0m`，
看起来像「没数据」。

**修法**：`<1s` 显示 ms，`<60s` 显示秒，否则显示分钟。

---

## 三、本轮修的完整 bug 列表

| # | 现象 | 根因 |
|---|---|---|
| 1 | `CPU 0m` 却占 34.6% | `dur()` 把 <1 分钟显示成 `0m` |
| 2 | 名称乱码 `è Ç` | C 写中文，读取端编码错乱 |
| 3 | `uid 1000` → `com.xiaomi.digitalkey` | packages.list 共享 UID |
| 4 | `pkg=?` 占一半 | 只用 cmdline 取包名 |
| 5 | `external_storage` 霸榜 | 硬编码 UID 表猜错 |
| 6 | 数据源混杂 → 增量乱算 | 三个时代数据混一起 |
| 7 | 采集器 `Permission denied` | KernelSU su 边界 |
| 8 | App 读不到数据 | 数据位置在 app 私有目录 |

---

## 四、文件清单

### 手机端

```
/data/local/tmp/plc/
├─ plcollect              14128 B  arm64 C 采集器（含 packages.list 补全 + sys: 标签）
├─ plc-daemon.sh          v3       守护（每条命令显式 su）
├─ daemon.log                       运行日志
└─ daemon.pid                       单实例锁

/data/local/tmp/powerlens/
└─ snapshots.tsv          2131 行  数据（App 从这读）

/data/adb/service.d/
└─ 40-plc.sh              开机自启

/data/local/tmp/                 诊断脚本
├─ drain-audit.sh         诊断主入口（九节报告）
├─ selfcheck.sh           自检（不依赖 adb）
├─ bootstrap.sh           一键重建数据集
├─ switch-store.sh        切换存储位置
├─ fix-all2.sh            修权限（绝对路径版）
├─ check-names.sh         UID 名称验证
├─ no-root-proof.sh       证明 App 不需要 root
├─ clean-hard.sh          清洗脏数据
└─ seed.sh                快速积累采样点
```

### PC 端

```
D:\CODEX\w\scripts\
├─ reconnect.ps1          ★ 自动重连（端口扫描）
├─ hotspot-keepalive.ps1  ★ 热点保活
├─ screen-live.ps1        实时盯亮灭屏
├─ deploy.ps1             一键部署
├─ verify-kit.ps1         离线自检
├─ adb-env.ps1 / adb-retry.ps1
└─ phone\01-core,02-diagnose,03-ab-test
```

---

## 五、常用命令

```powershell
# PC 侧（连不上时）
cd D:\CODEX\w\scripts
.\reconnect.ps1 -Loop
.\reconnect.ps1 -Run "su -c 'sh /data/local/tmp/selfcheck.sh'"
```

```bash
# 手机侧
su -c "sh /data/local/tmp/selfcheck.sh"          # 自检
su -c "sh /data/local/tmp/switch-store.sh"       # 修数据位置
su -c "sh /data/local/tmp/bootstrap.sh"          # 重建数据集
su -c "sh /data/local/tmp/drain-audit.sh full"   # 全量诊断
su -c "sh /data/local/tmp/check-names.sh"        # 验证名称
su -c "sh /data/local/tmp/no-root-proof.sh"      # 验证不需要 root
```

---

## 六、待观察

### `logd` 占 56.5%、CPU 14.2s

这是**异常高的**。logd 是系统日志服务，正常应该远低于这个值。
可能原因：
- 某个应用在疯狂写日志
- logcat buffer 太小导致频繁 flush
- 某些调试开关被打开

下一个排查方向：
```bash
# 看谁在刷日志
su -c "logcat -d -b main | awk '{print \$4}' | sort | uniq -c | sort -nr | head -20"
# 看 logd 的 buffer 配置
su -c "logcat -g"
# 看 logd 的写入速率
su -c "logcat -d -b main | wc -l"
```

### `(uid 2000)` = shell

`adbd` 通过 shell 用户执行命令，占 16.6%。
这是**我的调试活动造成的**，不是手机本身的问题。

### `(uid 2903)` 无法命名

packages.list 里没有，`/proc` 里当时也没有进程。

---

## 七、下一步（数据积累后）

现在数据在实时增长（每分钟约 151 行，10 分钟一个采样点）。

```bash
# 1. 让它跑 2-3 小时
# 2. 建立干净基线
su -c "sh /data/local/tmp/drain-audit.sh full"

# 3. A/B 实验（最有价值）
su -c "sh /data/local/tmp/ab-test.sh preset swappiness"
su -c "sh /data/local/tmp/ab-test.sh preset deep"
su -c "sh /data/local/tmp/ab-test.sh preset adsprpcd"
su -c "sh /data/local/tmp/ab-test.sh preset wifi"
```

**注意**：ab-test 依赖的 `drain-audit.sh` 和 `ab-test.sh` 里读写的是
`/data/data/.../snapshots.tsv`，需要同步改成 `/data/local/tmp/powerlens/snapshots.tsv`。