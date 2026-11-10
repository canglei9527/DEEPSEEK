# 息屏耗电诊断 — 运行笔记（自动追加）

采集器：PID 36668 (monitor.ps1) + PID 22032 (suspend-control-monitor.ps1)，均于 2026-11-09 00:00 停止。
设备 192.168.1.89:45051，24410DPN6CC / 2410DPN6CC，root 可用，无线 ADB 保持在线。

## 基线（截至 20:25）

- 纯息屏/Doze IDLE 窗口的 charge-counter 放电率高度稳定：93–110 mA ≈ 1.6–1.9 %/h
  （18:55→19:10 110mA；19:10→19:25 93mA；19:25→19:40 102mA；19:55→20:10 105mA）。
- 同一批窗口：UID 1000 系统 CPU 228–294 s / 15 min（25–33% 单核）；UID 0 内核 95–121 s；
  其中 android.system.suspend-service 143–186 s（16–21%）。
- suspend 尝试约 0.9–1.0 次/秒（19:17 累计 2722 → 20:17 累计 5495，一小时 +2773）。
  累计 overhead 656 s/h ≈ 18% 墙钟时间花在进入挂起。
- 内核 /sys/power/suspend_stats：success 4680 / fail 997，failed_freeze 589、
  failed_suspend 385；last_failed_dev=0000:01:00.0（cnss WLAN），errno -16(EBUSY)/-11(EAGAIN)。
- 唤醒中止主因：timerfd ≈0.77–0.87/s（15 min 内 665–778 次）、qcom_rx_wakelock（Wi-Fi RX）、
  WLAN PCIe -11（15 min 18–25 次）、alarmtimer -16、3-000e。
- /sys/power/wake_lock 为空 → 没有用户态 wake_lock 持有；问题在设备/中断层而非 app 锁。
- MountServiceIdler 只在 19:40–19:55 出现一次（331 s），19:55 与 20:10 快照计数完全相同 → 未重复。
- 京东：19:55 起 bucket 10→40，20:10 窗口 JD CPU 0.000 s、网络 0 包；限制前后耗电率无变化。
- NoActive / NoActive Monitor：无 CPU、无 partial wakelock 计数行。
- 19:40–19:55 为用户确认的百度地图正常使用（248 mA / 4.4 %/h，亮屏 145 s），不作为后台证据。

## 逐窗记录（后续追加）

## 20:45-21:00 root 深查（只读）

- root 正常：su -c 可读 /sys/power/suspend_stats、/proc/*/fd、dmesg（设备端过滤）。
- /sys/power/wake_lock 为空；doze_always_on=0（无息屏显示）、wifi_scan_always=0、wifi_wakeup=0。
- dmesg 序列：PM: suspend entry (s2idle) 每 0.3-0.6 s 一次，入口后 0.1-0.4 s 就
  Abort: Pending Wakeup Sources: [timerfd] -> PM: suspend exit。
  绝大多数 abort 记为 [timerfd]；其次 WLAN 0000:01:00.0 error -11、3-000e、alarmtimer -16。
- 设备端 awk 统计最近 118 s 挂起事件：awake 62.0 s (52.5%) / s2idle 56.0 s (47.5%)。
  -> 息屏时 AP 一半以上时间醒着，每次只睡 0.2-0.4 s。
- 15 min 纯息屏窗口（19:55-20:10）进程 CPU：suspend-service +186 s、system +52 s、
  phone +12.7 s、QQ MSF +11.7 s、quicksearchbox +10.3 s、WeChat +6.7 s、
  busybox +11.7 s（我自己的 adb/su 命令开销，诊断污染）。
- 当前可见 98 个 timerfd 句柄中没有 1 s 周期的，说明 1 Hz 源是重新武装的 oneshot
  或 pending 标志粘住；内核不记录 timerfd 属主，无法从日志直接指认 app。

## 21:05-21:20 纯息屏窗口（用户确认：不开 ADB 时同样耗电快）

- charge 3483 -> 3458 mAh = 25 mAh / 889 s = 101 mA = 1.75 %/h
- system 231 s、kernel 109 s、suspend-service ~142 s、QQ 后台 partial 125.6 s（比 20:10 窗口的 13.9 s 高 9 倍，耗电率不变）
- 我方负载：adbd 3.05 s CPU/15 min（0.34%），shell Wi-Fi TX ~2 MiB/15 min
- 结论：ADB/诊断流量不是原因；19:10 窗口的 19.5 MiB TX 属污染，但耗电率与干净窗口一致

## 21:36-21:45 唤醒源实证（隐藏 debugfs）

MIUI 把 debugfs 挂在 `/dev/uiwjakqc/bmdpxbzz`（不是 /sys/kernel/debug），root 可读 wakeup_sources。
开机约 13.8 ks 的累计值（wakeup_count = 真正把系统从挂起中叫醒的次数）：

| source | wakeups | events | total_time |
|---|---|---|---|
| [timerfd] | 6975 | 13950 | 424 s |
| qcom_rx_wakelock | 2103 | 33425 | 2667 s (19% 开机时长) |
| qrtr_ws | 585 | 40458 | 9 s |
| pmo_wow_wl | 182 | 182 | 184 s |
| IPA_WS | 140 | 4937 | 0.6 s |
| alarmtimer.0.auto | 129 | 129 | 260 s |
| 3-000e | 112 | 919 | 2 s |
| NETLINK | 77 | 72921 | 27 s |
| eventpoll (3 个) | 47/12/10 | 86660/15150/72948 | <1 s |

合计约 1.0e4 次唤醒 / 13.8 ks ≈ 0.75 次/秒（含亮屏时段），息屏时约 2 次/秒。

外部证据：
1. 内核提交 "fs: Improve eventpoll logging to stop indicting timerfd"：
   timerfd 本身不建 wakelock；"[timerfd]" 实际是 **eventpoll 的 EPOLLWAKEUP 每 fd 锁**，
   旧内核用 fd 名字命名，所以误标成 [timerfd]。即：某进程用 epoll_wait(EPOLLWAKEUP) 盯着一个 timerfd，
   定时器一到就产生名为 [timerfd] 的 wakelock 并唤醒 AP。
2. 小米社区另一台 HyperOS 设备（Redmi Note 13 Pro）的技术报告里出现一模一样的序列：
   `Abort: Pending Wakeup Sources: [timerfd] → PM: suspend exit → PM: suspend entry (s2idle)`，
   并同样描述为系统级 deep sleep 阻塞。

其他：NFC 开（mState=on，st21nfc 累计 2410 s = 17.5% 开机时长，但 wakeups=0，不是挂起阻塞源）；
蓝牙 enabled:false；移动数据关；wifi_scan_always=0。

## 21:47 处置

- 用户同意 A-1：已执行 `svc nfc disable`，验证 mState=off。撤销命令：`su -c "svc nfc enable"`。
- 未执行 A-2（重启）：重启后无线调试端口有可能变化，一旦 45051 变了我会彻底失去设备，
  当晚数据链断裂。改到 00:00 收完最后快照后再做重启对照，或由用户明确同意承担该风险。

## 21:56-22:10 ftrace 定位唤醒链（决定性）

1) 纯息屏 50 s 窗口的 wakeup_sources 差分（21:54-21:56）：
   [timerfd] 107 次 = **2.13/s**；qcom_rx_wakelock 25 次 = 0.50/s；pmo_wow_wl 2 次；
   合计 **2.73 次唤醒/秒**。同一时段 awake 占 62.5%。

2) `/sys/kernel/tracing/events/power/wakeup_source_activate` 采样 4 s（息屏 Dozing），
   得到严格 460 ms 周期的唤醒链：
   ```
   +0.000 kworker/3:0        -> [timerfd]
   +0.050 dp_rx_thread_0     -> qcom_rx_wakelock
   +0.058 tee-supplicant x2  -> gh_vcpu_ws
   +0.075 AlarmManager-4714  -> eventpoll + [timerfd] + eventpoll
   +0.101 trustedvm_vcpu0/1  -> gh_vcpu_ws
   （约 460 ms 后重复）
   ```
   AlarmManager-4714 是 system_server 的 AlarmManager 线程。

3) 亮屏时同一 tracepoint 几乎不出现（[timerfd] 事件 90 s 只有 6 次 = 0.07/s）
   → 这条 460 ms 周期链只在"正在尝试挂起"时存在，属于挂起-唤醒循环本身，
   不是某个 App 的定时器。

4) 结合内核提交 "fs: Improve eventpoll logging to stop indicting timerfd"：
   [timerfd] 是 epoll/EPOLLWAKEUP 的每 fd 锁被旧内核按 fd 名命名所致。

结论（分级见最终报告）：
- 已证实：息屏无法维持挂起（每 0.46 s 一次尝试、0.1-0.4 s 被中止、50-62% 醒着、~105 mA）。
- 已证实：唤醒源以 [timerfd]/eventpoll 为主（2.1/s）、Wi-Fi qcom_rx 次之（0.5/s）。
- 已证实：应用层锁不是原因（QQ 13.9 s vs 282 s 两个窗口耗电都是 105 mA）；
  京东限制无效果；NoActive 无计数。
- 候选：TEE(tee-supplicant)/TrustedVM(trustedvm_vcpu0/1)+Gunyah vCPU 在同一 460 ms 相位被调度，
  疑为厂商层周期任务；尚无能力在只读条件下进一步归因。

## 22:30-22:40 已装模块盘点 + mDNS 组播 A/B

已装（/data/adb/modules，KernelSU + ZygiskNext + LSPosed）：
MiuiVariableThermal(动态温控)、WorkSettingPro、disablebaa(电池循环 438 伪装 39)、
idle_battery_saver v3.46（息屏省电：Doze 加速/息屏关核/超大核限频/停止手势传感器/App Standby）、
qpace_lkm(SM8850 内存压缩 LKM)、scene_systemless、xm15_baa_change_all、cosmicmap_adblock、zygisk_lsposed、zygisksu。

A/B（息屏，60 s 窗口，临时 iptables 规则，测完已删除）：
- 基线：qcom_rx_wakelock 0.73/s、[timerfd] 0.02/s，总 0.92/s
- 屏蔽 udp 5353(mDNS)/1900(SSDP) 后：qcom_rx_wakelock 0.18/s（-75%）、[timerfd] 0.73/s，总 1.02/s
→ 组播确实贡献了 qcom_rx 的大头，但总唤醒率没有下降（[timerfd] 相位接棒）。
  说明各唤醒源在同一重试循环里轮换，单独挡一个源不足以减少总唤醒次数。

cmdline 侧证据（本机）：/data/adb/modules 里没有 magisk / ksud 二进制，root 管理是 KernelSU。
