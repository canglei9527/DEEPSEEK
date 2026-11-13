# CPU Efficiency Lab

无需外置硬件的 Android 电池侧功耗采集与 CPU 能效实验工具。支持只读预检/采样、显式授权 CPU 扫频；实验性 `matrix-sweep` 当前因恢复安全问题 fail-closed。不改变 governor、不关闭温控或 SELinux。

## 项目构成

本项目包含两个协同的半边，是同一台 Xiaomi 15 Pro（`2410DPN6CC` / SM8750 / Android 16 / KernelSU）上的同一条测量线：

| 目录 | 角色 | 回答的问题 |
|---|---|---|
| `src/` + `scripts/` + `native/` | 实验侧（CPU Efficiency Lab） | 每焦耳能干多少活：锁频矩阵、吞吐 ÷ 积分功率（nJ/op）、逐核能效曲线 |
| `powerlens/` | 采集侧（原独立项目 PowerLens，2026-11-13 并入本目录） | 谁在耗电：root 侧 procfs/dumpsys 常驻采集 + 免 root Java UI，逐 UID 归因 |

两侧互相依赖，不是并列的两个工具：

- **实验侧依赖采集侧的开关**：`powerlens/` 的守护进程是本实验最主要的噪声源。实测每频点相对极差中位 9.3%、最大 59.5%，`data/EEFF-RESULTS.md` 明确记录压噪需要「测前 kill 掉 PowerLens 守护与其它后台进程」。跑任何能效测点前，先用 `powerlens/magisk/module/bin/collect-control.sh` 关闭采集开关。
- **采集侧依赖实验侧的口径**：`powerlens/` 能告诉你某个 UID 耗了多少 mAh，但答不出为什么。最优频点结论（policy0 2400 MHz 8.42 nJ/op、policy6 3072 MHz 8.86 nJ/op）是解释这些差异的频率口径。
- **共用同一套电池侧电量口径**：两侧都读 `/sys/class/power_supply/battery` 的 `current_now` / `voltage_now` / `charge_counter`，数字可以直接对账；也受同一个限制——测的是整机放电，不是 CPU 电源轨功耗。

## 当前进度

- **实现**：固定频率逐点计划、双次重复、配对空闲基线、有效性分析及逐核曲线/混合频率热力图已就绪；本地模拟覆盖锁频写入/恢复中断和可观察到的系统限频变化。
- **本地验证**：此前114项全量测试通过；最新调度状态、恢复证据和厂商探针针对性测试21项通过，`python -m compileall -q src tests scripts` 通过。本轮全量测试一次120秒超时、一次本机后台重跑停滞后停止了该本地测试进程，均不能记为通过；手机端无对应基准进程。测试命令为 `python -m unittest discover -s tests -t .`，`-t .` 不可省略。
- **两小时试运行尝试（未启动负载）**：只读预检与离线计划保存在 `data/phase-j-two-hour-preflight.json`、`data/phase-j-two-hour-plan.json`，当前快照计划127配置、估算119分40秒。`two-hour-sweep` 在创建设备客户端/运行目录之前被原恢复门禁拒绝；未锁频、热插拔或启动基准。
- **真机只读采集**：已在 `192.168.137.99:45387`（2410DPN6CC/SM8750/Android 16）完成预检与 20 秒采样（`data/phase-h-idle-check.*`）：实际跨度 20.84 秒、中位间隔 1.041 秒，`current×voltage` 积分 14.57 J 与 `charge_counter` 估算 15.26 J 相差 4.78%。这是同一电量计的窗口一致性检查，不是独立校准。
- **真机恢复验证（已开跑，未通过）**：故障注入用例已开始执行，但**首次在真机捕获到系统并发限频竞态** — 会话期间 `policy0` 在 384000–1996800 / 556800–2227200 / 1152000–2745600 / 1152000–1363200 之间被系统反复改动（`online=0-7`、`low_power=0` 稳定，非本工具写入）。保守恢复正确地不放宽并标记 `RESTRICTED` + `tainted`，设备事后处于安全状态（全核在线、无残留基准进程、屏幕超时已还原）。
- **本次修掉的缺陷**：验证器曾把这种“系统漂移”当成工具恢复失败并中止整轮（`policy0_max/min/restore` 均 `cases_completed=0`，后续 8 个用例完全未执行）。现区分 `PASS` / `RESTRICTED` / `SKIPPED` / `FAIL`，只对真实恢复失败中止，但退出码仍只在全部 `PASS` 时为 0。
- **固定频率实测（已完成，手工路径）**：已在真机逐点锁频测完 8 核能效，86 个单核测点 + 23 个 8 核满载组合测点，实测曲线与结论见 `data/EEFF-RESULTS.md`。单核能效最优为 policy0 2400 MHz（8.42 nJ/op）、policy6 3072 MHz（8.86 nJ/op）；8 核满载能效最优组合为小核 1997 + 大核 1402 MHz（475 Mop/s/W），吞吐最优组合为 2746 + 2438 MHz，两者不重合。
- **这些结果不解除门禁**：上述测量走的是「手工锁频 + 显式恢复 + 写后回读校验」，没有经过 `matrix-sweep` 的自动恢复路径，因此不构成 `INDEPENDENT_RECOVERY_VERIFIED` 的证据。测量期间再次复现了系统并发限频（CPU6/7 一度被下线、policy6 `scaling_max_freq` 被压到 1689600），反而强化了 fail-closed 的必要性。
- **真机恢复验证（2026-11-11）**：两轮各11个控制器/看门狗故障注入用例均 `PASS`，0 `RESTRICTED` / `SKIPPED` / `FAIL`；详情见 `data/phase-i-recovery-verify.*` 和 `data/phase-k-recovery-revalidate.*`。最近一轮后 CPU0–CPU7 全在线、无残留 benchmark，屏幕超时及 policy 上下限与该轮测试快照一致；不证明 sysfs 并发竞态已解决。
- **门禁仍关闭**：`INDEPENDENT_RECOVERY_VERIFIED = False`。全部故障注入通过只证明覆盖到的这些时序能恢复，不消除 sysfs 检查/写入非原子竞态。且本轮之前的只读预检中，policy0 上限从 1785600 变到 2745600 kHz、policy6 从 2438400 变到 3072000 kHz（期间本工具未写设备），说明系统限制状态有变化；不能据此启动完整锁频矩阵。
- **厂商频率请求验证（未通过）**：三次短时perfd诊断均取得正句柄并释放，但实际频率上限和厂商vote没有按请求变化；服务可见句柄不等于锁频生效。`phase-o` 为 `ownership=INCONCLUSIVE`、`crash_ttl=NOT_RUN`、事后快照一致，原始证据 `data/phase-{m,n,o}-*.raw.log` 和 `.summary.json`。此实验未执行性能负载或直接写cpufreq。详见 `ai-context/PERF_REQUEST_EVALUATION.md`。
- **最新只读调度核验**：`data/phase-p-scheduler-status.json` 与 `data/phase-q-scheduler-status.json` 同启动周期比较为 `MATCH`，8核在线、policy0/6均为 `walt`，未观察到本项目进程、保活锁或perfd请求；只是快照比对，不证明出厂默认。屏幕超时仍为2147483647 ms，未擅自改动。
- **尚未完成**：最新恢复代码只完成本地测试，未执行对应版本的真机故障注入；perfd独立后端未验证有效，sysfs检查/写入竞态仍存在。两小时测量没有新增性能样本，门禁保持fail-closed；不得用手工脚本绕过。
- **下一步**：需要匹配此手机的厂商频率资源应用与仲裁实现证据，定位为何请求未生效；再在受控条件下验证独立句柄与系统限频并存。不得靠返回正句柄、猜测性资源号、改权限或旧版11/11记录解除门禁。

## 能效实测口径

`data/EEFF-RESULTS.md` 记录了 8 核逐频点能效实测。要点：

- 锁频用 `governor=performance` + `scaling_min_freq = scaling_max_freq = F`，
  **写完必须回读 `scaling_cur_freq` 校验**；本机实测到外部策略会随时改写
  `scaling_max_freq`，写完就信会得到错误频点下的数据。
- 能效口径是 `吞吐 / 积分平均功率`（nJ/op），**不是**「负载功率减空闲功率」。
  后者在本机会得到负值：单核满载时其余核进入更深休眠，整机功率反而下降。
- 同一频点的多个核心测点取**中位数功率**。逐核直接比较不可靠：
  同频 core6/core7 吞吐一致但功率差 30%（采样撞上负载结束后的过渡态）。
- 组合能效必须**实测 8 核满载**，不能按核数外推单核功率。
  实测证明外推高估 48%（外推 619 vs 实测 419 Mop/s/W）。

## 如何核验系统调度是否恢复

先观察，不要手写旧频率或猜测默认governor。下列命令只读手机，输出文件必须为新路径：

```powershell
$env:PYTHONPATH = "src"
$adbSerial = "YOUR_ADB_SERIAL"
# 开始任何将来获准的实验之前先记录基线
python -m cpu_efficiency_lab scheduler-status --serial $adbSerial --output data\scheduler-before.json
# 实验结束后或异常中断后，等控制器/watchdog退出，再记录并比较
python -m cpu_efficiency_lab scheduler-status --serial $adbSerial --baseline data\scheduler-before.json --output data\scheduler-after.json
```

- `NO_LAB_ACTIVITY_OBSERVED` 表示按可见接口未观察到本项目进程、保活锁或perfd句柄，**不证明所有系统调度项为出厂默认**；`LAB_ACTIVITY_OBSERVED` 或 `INCOMPLETE` 需要先排查，不能盲目改频率。
- `baseline_comparison` 的 `MATCH` 只表示列出的 governor、policy上下限、在线核、省电模式和屏幕超时等于基线；`DIFFERENT` 不等于必须写回——系统可能主动更新。重启或ADB端点、设备身份不匹配时返回 `UNCOMPARABLE_IDENTITY_OR_BOOT`，不使用旧记录执行恢复。指定基线时，只有匹配且未观察项目活动才返回退出码0；发现项目活动为1，信息不足或基线不匹配为2。此核验只读，采集并非原子快照。
- 如自动恢复失败或ADB断开，先停止继续测试、等待控制器/watchdog或已知有期限的请求结束，再只读复查；不要关闭温控、SELinux，或强行写旧policy上限/governor。设备重启**可能**清掉临时进程/请求，但不保证清除开机脚本、模块或持久设置；保存数据后自行选择，并重新检查。
- 屏幕超时与CPU调度不同：本机现为2147483647 ms，一份旧记录是1800000 ms（30分钟）；若这仍是本人偏好，可在手机显示设置里手动调整，工具不会根据旧记录擅自写入。

## 当前测量边界

- 通过 root 读取 `current_now`（µA）和 `voltage_now`（µV），在主机端计算电池侧放电功率。
- `power_now` 节点不存在时，不创建伪节点；只计算派生字段：`abs(current_now × voltage_now) / 1e12` W，且仅在状态为 `Discharging` 时解释为放电功率。
- 这是整机电池侧功率，不是 CPU 单独功耗。没有外部校准时无法给出绝对准确度保证。
- 采样器在设备上执行只读 shell 读取并通过 ADB 回传；原始 CSV 写在主机本地，目标文件已存在时拒绝覆盖。

## 环境

- Windows 主机已安装 Python 3.9+ 与 Android Platform Tools (`adb`)。
- Android 设备已连接并授权 ADB；使用 root `su -c` 读取 sysfs。
- 不需要安装 Python 第三方包。

## 启动

PowerShell：先从 `adb devices` 获取设备序列号或无线调试 `IP:端口`，再替换下面的占位符：

```powershell
$adbSerial = "YOUR_ADB_SERIAL"
.\scripts\lab.ps1 preflight --serial $adbSerial
.\scripts\lab.ps1 capture-power --serial $adbSerial --duration 10 --interval 1 --output data\idle-check.csv
```

首条只读检查设备身份、root、节点和 CPU policy。第二条只读采集电池传感器，不运行负载；间隔用于轮询，实际有效更新周期需根据读数变化验证。执行前确保设备未充电、状态与测试目的相符。采集会产生少量 adb/采集器开销。

若直接在终端调用：

```powershell
$env:PYTHONPATH = "src"
$adbSerial = "YOUR_ADB_SERIAL"
python -m cpu_efficiency_lab preflight --serial $adbSerial
```

### CPU 扫频（会改动设备状态）

先编译 ARM64 微基准，并设置 `$adbSerial` 为 `adb devices` 显示的设备序列号或无线调试 `IP:端口`，再执行明确带有授权开关的命令：

```powershell
$adbSerial = "YOUR_ADB_SERIAL"
.\scripts\build-bench.ps1 -NdkRoot $env:ANDROID_NDK_ROOT
.\scripts\lab.ps1 cpu-sweep --serial $adbSerial --binary build\cpu_mix_bench --output data\cpu-sweep --confirm-device-writes
```

默认在每个当前可用频点做 3 次（15 秒预热 + 25 秒计分），并配对记录空闲基线。计划至少 30 分钟实际基准负载；完整运行还包含基线和冷却时间，通常约 48 分钟。频率不超过预检时 `scaling_min_freq` / `scaling_max_freq`，温度达到 CPU 80°C 或电池 43°C 会中止。不要在过程中拔线、重启或手动改频率；程序结束后会核对原上下限。基准为自定义 `scalar_mix_v2`，不可与 Geekbench/极客湾直接比较。

扫频会将微基准推送到 `/data/local/tmp/cpu-efficiency-lab/`，然后逐频点临时写入 cpufreq 上下限；因此即使已提供 `--confirm-device-writes`，也只应在明确接受这些临时改动时执行。结果前缀对应 `.raw.log`、`.samples.csv`、`.runs.csv`、`.curve.csv`、`.summary.json`，以及总电池功耗/增量功耗和总/增量能效四张 SVG。若负载减空闲的增量功率不显著为正，增量图可能没有有效点，但总电池侧曲线仍保留测量有效的数据。功耗是整机电池侧读数，不能冒称 CPU 单独功耗；性能分数是自定义 `scalar_mix_v2`，不能与 Geekbench/极客湾曲线直接比较。

### 两小时灭屏低噪声方案（代码已接入，真机未运行）

- `two_hour_plan.py`：8 个单核各扫当前合法全部频点；混合范围限定 E6+P2 的最多 5×5 频率网格，再加 E6/P2 对照。最近频率表对应 92+25+10=127 配置、每点两次计分，名义测量 84 分 40 秒，含预留预算 119 分 40 秒。新的频率表若超预算，会保存计划但不启动扫描。
- `two_hour_runner.py`：优先覆盖所有核心和混合低/中/高点，再补齐；逐点保存 raw/event/CSV。第110分钟停止启动新配置，启动前还检查控制器超时和恢复预算，不只看40秒名义时长。异常限频、断连或持续过热时不保证全部点有效。
- `low_noise_environment.py`：要求可读写的内核 `wake_lock`、可写的 `wake_unlock` 和可核验的背光 `actual_brightness`；独有锁带内核超时，守护监控PID/starttime与主机心跳，逐样本核验锁名、屏幕与守护。此前只读探测时锁列表为空，不能据此判断持锁时是否可读回；该后端尚未做真机灭屏及恢复验证。没有可验证后端就报错，不切回亮屏。不改屏幕超时、governor、温控、SELinux、飞行模式或应用状态。- 开始前在最多8分钟内等待 CPU≤45°C、电池≤32°C；CPU80°C/电池43°C硬保护不变。所有功耗仍为整机电池侧。增量指标单独判定，并以观测空闲极差作保守噪声筛选，不把它称为置信区间或 CPU 独立功耗。
- 图表：8条独立单核曲线、E6+P2组合热力图及切片、纯簇对照；总/增量能效分别标注。重复极差不是测量准确度；无效/未测点断线，缺格不插值。

以下两个入口完全离线，只读本机输入并创建新的本机输出（不连接 ADB）：

```powershell
$env:PYTHONPATH = "src"
python -m cpu_efficiency_lab two-hour-plan --preflight data\saved-preflight.json --output reports\two-hour-plan.json
python -m cpu_efficiency_lab two-hour-analyze --input data\two-hour-run --output reports\two-hour-analysis
```

`two-hour-sweep` 实测入口已接入，但仍先调用原有 `require_verified_hotplug_recovery()`，门禁关闭时连 ADB 客户端都不创建；全部确认参数也不能绕过。**新增代码只经过离线、mock 和 fake-sysfs 验证，没有连接手机、做新故障注入或启动负载。** 灭屏保活/恢复还需真机验证；sysfs 非原子竞态仍未解决。方案及边界见 `ai-context/TWO_HOUR_LOW_NOISE_PLAN.md`。

### staged `matrix-sweep`（当前禁用）

此前 watchdog 在控制脚本 SIGKILL 后恢复核心状态及 cpufreq，并终止运行中的 benchmark。原有恢复无条件写回开测快照，可能覆盖系统新限频；当前逻辑先校验journal和持久taint，仅在状态仍与本程序的锁定日志吻合时执行恢复；未知归属或tainted的policy不再写入，即使数值回到快照也不能掩盖干扰证据。已有部分设备故障注入记录保存在本地 `data/`（默认不随仓库发布）；本地测试覆盖锁频/恢复中断、benchmark 启动门闩，以及恢复期间可观测到的系统限频变化。后者会被标记为 tainted 并保留较低上限，但不能消除 sysfs 检查与写入间不可原子的竞态。`INDEPENDENT_RECOVERY_VERIFIED = False` 门禁因此保持关闭，不能以确认参数绕过。

计划按开测前实际 scaling 范围生成：每个 CPU 遍历该 policy 的**全部可用离散频点**；E2/E4/E6/P2 纯组遍历全部频点；六种 E/P worker 数量组合遍历 E×P **完整频率笛卡尔积**。逐频矩阵的点默认重复 2 次（每次 2 秒预热、6 秒计分），并在单点负载前后配对空闲基线；缺少任一侧基线时只舍弃增量功率指标，不丢弃有效的整机总电池能效。汇总曲线只把计划要求的重复次数全部有效的点标为有效；单核频率曲线在无效点处断开，混合频率热力图用跨面板统一色标，缺测/无效格不插值。输出包括 `.single-core-frequency.svg` 与 `.mixed-heatmap.svg`。这些改进只准备测量和分析流程，并未产生新实测数据。

每次运行均以当次只读预检得到的 scaling 上下限和可用离散频点为准。若某 policy 当前只有一个可用频点，矩阵将无法比较该 policy 的不同频率；不得擅自扩大范围、关闭省电模式或修改系统限频。

在恢复门禁关闭期间，可用下列命令单独运行**恢复故障注入验证**（不是性能扫描）；它会临时写 cpufreq 上下限、热插拔非 keeper 核心，并故意终止控制器以检查 watchdog。只应在明确接受设备状态变更且设备温度/电量满足要求时运行：

```powershell
$adbSerial = "YOUR_ADB_SERIAL"
python scripts/verify_matrix_recovery.py --serial $adbSerial --output data\matrix-recovery --confirm-device-writes
```

该验证全部通过也不会自动开启矩阵门禁；还需审查真机日志及 sysfs 非原子竞态的剩余风险。

验证器对每个用例给出四种结论之一：`PASS`（工具恢复且快照一致）、`RESTRICTED`（发现策略干扰或归属不明，当前实现保留当时读到的边界而不再改写，并保留taint；属结论不确定，不算通过）、`SKIPPED`（当前 policy 只剩单频点，无法写入临时锁频）、`FAIL`（真实恢复失败，立即中止）。只有全部 `PASS` 时退出码才为 0，`RESTRICTED`/`SKIPPED`/`FAIL` 一律非 0，不会因为出现外部限频而松动门禁。真机首次跑时 `policy0_max/min/restore` 三例均返回 `RESTRICTED`，原因是系统在注入窗口内反复改动 `policy0` 边界（详见 `ai-context/BUG_HISTORY.md`）。

CPU0 保留为 Android keeper：只有 CPU0-only 项能实现严格单在线核心；CPU1–CPU7 单 worker 项会关闭其他可关核心，但 CPU0 仍在线。混合测试按每个 policy 统一锁频，因为同一 policy 内核心不能分别设频。频点只取当前 scaling 范围内可用值。

历史 all-online、无锁频 pinned 校准仅用于预研，不是严格隔离/固定频率矩阵。原始设备测量数据保存在本地 `data/`，默认被 Git 忽略，不随代码仓库发布。

## 输出与数据保护

- 预检 JSON 只在指定 `--output` 时写入，并采用独占创建。
- `capture-power` 必须显式指定 CSV 路径，也采用独占创建；不会覆盖旧结果。
- 同时生成 `.summary.json`，记录实际采样时间跨度、观测间隔、状态计数、设备信息和积分结果；长窗口内可比较 `charge_counter` 推算值，但它与 `current_now` 可能同属一个电量计，不能视为独立校准。
- CSV 保留电流/电压原始值、各自读取时间边界、电池状态、温度、电量、充电计数和派生功率。
- `current_now` / `voltage_now` 是顺序读数；时间边界用于检查其不同步程度。

## 验证

```powershell
.\scripts\test.ps1
python -m compileall -q src tests scripts
```

`.\scripts\test.ps1` 等价于在项目根目录执行 `python -m unittest discover -s tests -t .`。`-t .` 不能省：省略时 `tests` 被当作顶层目录，`cpu_efficiency_lab` 与 `run_pinned_dynamic` 会导入失败。

单元测试不代表实机扫频已验证。开始扫频前先审阅只读预检和温度传感器识别；测试中如发生异常，优先检查原始日志及频率恢复核验。PowerLens 对照和外部校准仍未实施。
