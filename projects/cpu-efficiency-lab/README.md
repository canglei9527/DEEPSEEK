# CPU Efficiency Lab

无需外置硬件的 Android 电池侧功耗采集与 CPU 能效实验工具。支持只读预检/采样、显式授权 CPU 扫频；实验性 `matrix-sweep` 当前因恢复安全问题 fail-closed。不改变 governor、不关闭温控或 SELinux。

## 当前进度

- **实现**：固定频率逐点计划、双次重复、配对空闲基线、有效性分析及逐核曲线/混合频率热力图已就绪；本地模拟覆盖锁频写入/恢复中断和可观察到的系统限频变化。
- **本地验证**：75 项单元/故障注入测试通过，`python -m compileall -q src tests scripts` 通过。
- **真机恢复验证**：设备写入验证已获授权，但目前 ADB 设备为 `offline`；验证器在只读预检阶段退出，未开始故障注入，未改写手机状态。
- **尚未完成**：`INDEPENDENT_RECOVERY_VERIFIED = False`，固定频率矩阵仍被安全门禁阻止；没有新增固定频率实测数据或对应实测曲线。此前动态频率结果不能用于判定最优频点。
- **下一步**：设备重新连接后运行 `scripts/verify_matrix_recovery.py`；审查真机日志并继续处理 sysfs 检查/写入不可原子的并发限频竞态。即使故障注入通过，也不自动解除门禁。

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

### staged `matrix-sweep`（当前禁用）

此前 watchdog 在控制脚本 SIGKILL 后恢复核心状态及 cpufreq，并终止运行中的 benchmark。原有恢复无条件写回开测快照，可能覆盖系统新限频；当前逻辑仅在状态仍与本程序的锁定日志吻合时执行精确恢复，否则保守收紧并报告受限状态。已有部分设备故障注入记录保存在本地 `data/`（默认不随仓库发布）；本地测试覆盖锁频/恢复中断、benchmark 启动门闩，以及恢复期间可观测到的系统限频变化。后者会被标记为 tainted 并保留较低上限，但不能消除 sysfs 检查与写入间不可原子的竞态。`INDEPENDENT_RECOVERY_VERIFIED = False` 门禁因此保持关闭，不能以确认参数绕过。

计划按开测前实际 scaling 范围生成：每个 CPU 遍历该 policy 的**全部可用离散频点**；E2/E4/E6/P2 纯组遍历全部频点；六种 E/P worker 数量组合遍历 E×P **完整频率笛卡尔积**。逐频矩阵的点默认重复 2 次（每次 2 秒预热、6 秒计分），并在单点负载前后配对空闲基线；缺少任一侧基线时只舍弃增量功率指标，不丢弃有效的整机总电池能效。汇总曲线只把计划要求的重复次数全部有效的点标为有效；单核频率曲线在无效点处断开，混合频率热力图用跨面板统一色标，缺测/无效格不插值。输出包括 `.single-core-frequency.svg` 与 `.mixed-heatmap.svg`。这些改进只准备测量和分析流程，并未产生新实测数据。

每次运行均以当次只读预检得到的 scaling 上下限和可用离散频点为准。若某 policy 当前只有一个可用频点，矩阵将无法比较该 policy 的不同频率；不得擅自扩大范围、关闭省电模式或修改系统限频。

在恢复门禁关闭期间，可用下列命令单独运行**恢复故障注入验证**（不是性能扫描）；它会临时写 cpufreq 上下限、热插拔非 keeper 核心，并故意终止控制器以检查 watchdog。只应在明确接受设备状态变更且设备温度/电量满足要求时运行：

```powershell
$adbSerial = "YOUR_ADB_SERIAL"
python scripts/verify_matrix_recovery.py --serial $adbSerial --output data\matrix-recovery --confirm-device-writes
```

该验证全部通过也不会自动开启矩阵门禁；还需审查真机日志及 sysfs 非原子竞态的剩余风险。

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
python -m unittest discover -s tests -v
python -m compileall -q src tests
```

单元测试不代表实机扫频已验证。开始扫频前先审阅只读预检和温度传感器识别；测试中如发生异常，优先检查原始日志及频率恢复核验。PowerLens 对照和外部校准仍未实施。
