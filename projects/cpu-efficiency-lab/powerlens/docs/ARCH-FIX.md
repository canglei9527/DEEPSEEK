# PowerLens 架构重构说明（1.2）—— 解决"每次改完 root 就没了"

## 一、你遇到的问题

> "我每次给 APP ROOT 后 你改了一通我就发现 ROOT 没了"

**这是我的流程错误，不是你的操作问题。**

## 二、根因

KernelSU / Magisk 的 root 授权按 **(uid, APK 签名)** 记录。

我之前的升级流程是：
```bash
pm uninstall com.powerlens.app     # ← 灾难
pm install PowerLens.apk
```

后果：
- `pm uninstall` 会**销毁 App 的 uid**，重装后 uid 从 `u0_a427` 变成 `u0_a428`
- KernelSU 记录的是旧 uid 的授权，新 uid 不匹配 → **授权失效**
- 你就得重新在 KernelSU 里点一次授权

**这个循环每改一次版本就重复一次。**

## 三、根本解决：App 根本不需要 root

这是架构层面的问题。实测本机权限矩阵：

| 资源 | App（u0_a4xx） | root |
|---|---|---|
| `/sys/class/power_supply/battery/charge_counter` | **EACCES** | OK |
| `/sys/class/power_supply/battery/capacity` | **EACCES** | OK |
| `/proc/uid_cputime/show_uid_stat` | **EACCES** | OK |
| `/proc/uid_io/stats` | **EACCES** | OK |
| `su -c ...` | **被 KernelSU 拒绝** | — |

所以"让 App 拿 root 自己采集"这条路注定走不通，而且每次重装都要重新授权。

### 正确分工（1.2 开始）

```
┌─────────────────────────────────────────┐
│ root 侧（常驻，开机自启）                 │
│   /data/adb/service.d/40-plc.sh         │
│     └─ /data/local/tmp/plc/plc-daemon.sh│
│          └─ plcollect --once            │
│               ├─ 读 /proc/uid_cputime   │
│               ├─ 读 /proc/uid_io        │
│               ├─ 读 charge_counter      │
│               └─ 写 snapshots.tsv       │
└─────────────────────────────────────────┘
                    ↓ 写文件（权限 644）
┌─────────────────────────────────────────┐
│ App 侧（u0_a4xx，无 root、无后台）        │
│   MainActivity → Storage.summarize      │
│   只读 snapshots.tsv，绝不写入            │
└─────────────────────────────────────────┘
```

**收益**：
- App 在 KernelSU 里**可以完全撤销 root 授权**，功能不受影响
- 以后升级 APK 随便 `pm install -r`，不用再管授权
- App 侧 CPU 实测降到 **0 ms / 10s**
- App 不需要 JobScheduler（HyperOS 会拒），也不怕后台冻结

## 四、升级 APK 的正确姿势

```bash
# ✅ 正确：保留 uid、保留数据、保留授权
pm install -r /data/local/tmp/plc/PowerLens.apk

# ❌ 错误：换 uid，授权丢失
pm uninstall com.powerlens.app && pm install ...
```

已封装为 `upgrade-pl.sh`，它会：
1. 记录升级前的 uid
2. 用 `-r` 升级
3. 对比 uid 是否变化
4. 修正数据文件属主/权限/SELinux 标签
5. 报告数据格式健康度

## 五、这一轮修掉的其它 bug

| # | 现象（你的截图） | 根因 | 修法 |
|---|---|---|---|
| 1 | 电量计"不可读"、capacity `-1`、status `null` | App 无 root，sysfs 读不了 | 采集全搬到 root 侧；App 只读文件 |
| 2 | procfs `1 ms / -1 UID / EACCES` | `/proc/uid_cputime` 是 `-r--r--r-- root root` | 同上 |
| 3 | **列表全是重复的 `uid 1082`** | `labelCache` 用 `pkg` 当键，所有 `pkg="?"` 的 UID 共用键 `"?"` | 缓存键改为 `uid + "|" + pkg` |
| 4 | **剪贴板与常用语 / 文件 / 媒体选择工具** 占据功耗榜 | 感知 UID 无友好名，被当成普通应用 | 补齐 1000–1098 的 UID 名称表 |
| 5 | 数据库里混着 `4477 0 0` 这种行 | 旧 App 用 `BatteryManager` 写近似值，mAh 当 µAh 且状态丢失 | 识别伪数据行并跳过（`skippedInvalid`），UI 显示 |
| 6 | 三个采集器同时写同一文件 | 旧 `/data/local/tmp/powerlens/daemon.sh` 没清理 | `fix-arch.sh` 杀旧实例、禁用旧 service.d |
| 7 | 每次重连端口都变，脚本连不上 | 无线调试端口随机 | `deploy.ps1` 自动扫 30000-61000 |

## 六、验证命令

```bash
# 架构正确性检查
su -c "sh /data/local/tmp/crm.sh"

# 清理旧采集器
su -c "sh /data/local/tmp/fix-arch.sh"

# 升级 APK（保留 uid）
su -c "sh /data/local/tmp/upgrade-pl.sh"

# 重启守护（确保单实例）
su -c "sh /data/local/tmp/restart-daemon.sh"
```

## 七、注意事项

1. **`fix-arch.sh` 会禁用 `20-freezer.sh`**（NoActive 的息屏冻结）。
   如果还需要那个功能，改完手动恢复：`mv /data/adb/service.d/20-freezer.sh.disabled /data/adb/service.d/20-freezer.sh`
2. **不要再用 `/data/local/tmp/powerlens/`** 那个目录，里面的 `daemon.sh` / `pass.sh` 是旧架构，
   会产生伪数据。
3. App 里的「采集」按钮现在只在 **root 守护不可用** 时才真正采集；
   正常情况下它会提示"root 守护正在采集，无需手动"。