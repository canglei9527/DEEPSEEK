# 架构说明

## 技术路线
Python 3.9+ 标准库 CLI；Windows 主机通过已安装的 ADB 连接 Android。预检和传感器采样为只读；`cpu-sweep` 必须显式确认设备写入。实验性矩阵当前 fail-closed：保守恢复已完成部分本地/实机故障测试，但并发系统限频更新与半锁定/恢复中断仍未充分验证。所有路径保留 governor、温控和 SELinux。

## 目录结构

```text
AGENTS.md
README.md
pyproject.toml
src/cpu_efficiency_lab/
  __init__.py       版本与包入口
  __main__.py       python -m 入口
  cli.py            命令参数与输出
  adb.py            ADB / root shell 适配
  config.py         sysfs 路径、协议版本和验证常量
  preflight.py      只读设备/传感器/频率能力检查
  collector.py      采样 shell 协议生成、解析及 CSV 记录
  power.py          单位转换、功率和能量积分
  sweep.py          温度保护、频率点、临时锁频/恢复和旧式扫频调度
  topology.py       CPU 在线状态、容量分组的只读发现
  matrix_plan.py    分阶段逐核/纯组/混合频率配置生成
  matrix_controller.py 设备端热保护、CPU掩码/临时频率控制和独立恢复 guardian
  matrix_runner.py 试验阶段编排、日志/结果落盘；依赖恢复验证门
  matrix_analysis.py 矩阵多线程/电池样本解析、有效性与能效分析
  matrix_plot.py    Pareto 和混合频率热力图 SVG
  screen_control.py 屏幕常亮与超时设置的受控恢复
  analysis.py       原始扫频日志解析、配对基线和性能/能效指标
  plot.py           旧式频率扫频的无依赖 SVG 曲线输出
  models.py         采样与预检数据结构
  tests/            标准库 unittest
ai-context/          架构、函数和缺陷记录
scripts/lab.ps1      Windows 启动封装
scripts/test.ps1     单元测试封装（discover -s tests -t .）
scripts/verify_matrix_recovery.py  恢复故障注入验证（不改写门禁）
```

## 模块职责和接口

| 模块 | 职责 | 公共接口 | 读取边界 |
|---|---|---|---|
| `adb.py` | 本机 ADB 子进程与远程 root shell | `AdbClient.run()`、`run_root_script()` | 只调用明确的 ADB 命令 |
| `preflight.py` | 读取设备身份、节点、CPU policy 和电源状态 | `collect_preflight()`、`parse_preflight()` | `adb.py`、数据模型 |
| `collector.py` | 构建只读采样协议、解析并写本地 CSV | `build_sampler_script()`、`parse_samples()`、`write_csv()` | `adb.py`、`models.py`、`power.py` |
| `power.py` | µA/µV 换算、状态判断、梯形积分 | `battery_power_w()`、`integrate_energy_j()` | 纯函数 |
| `sweep.py` | 已授权的频率扫描、部署基准、温度中止与原值恢复核验 | `run_sweep()`、`build_point_script()` | `adb.py`、`preflight.py`、`models.py` |
| `topology.py` | 读取核心在线状态、容量和拓扑并验证热插拔控制可见性 | `discover_cpu_topology()`、`CpuTopology.capacity_groups()` | `adb.py` |
| `matrix_plan.py` | 按当前 policy 限制生成阶段 A-F 配置；CPU0 不作为 keeper 时始终在线 | `build_stage_a/b/c/d()`、`frequency_levels()` | `topology.py`、`models.py` |
| `matrix_controller.py` | 设备端临时 CPU 掩码/频率设置、采样、热保护、主/独立恢复和远端日志 | `build_matrix_config_script()`、`execute_matrix_config()`、`require_verified_hotplug_recovery()`；当前 fail-closed | `adb.py`、`preflight.py`、`matrix_plan.py`、`topology.py` |
| `matrix_runner.py` | 编排全频点逐核、纯组、完整 E×P 混合频率网格与候选复测，产生 raw/CSV/SVG/JSON | `run_full_matrix()`；当前由恢复门禁阻止 | `matrix_controller.py`、`matrix_analysis.py`、`matrix_plot.py` |
| `matrix_analysis.py` | 同步线程、频率、核心掩码和电池侧样本的解析/有效性判定 | `parse_matrix_log()`、`analyze_matrix_runs()`、`rank_single_core_order()` | `models.py`、`power.py`、`matrix_plan.py` |
| `matrix_plot.py` | Pareto、逐核固定频点能效曲线与 E/P 混合频率热力图 SVG | `render_matrix_pareto_svg()`、`render_single_core_frequency_curves()`、`render_mixed_frequency_heatmap()` | 标准库 |
| `screen_control.py` | 临时唤醒屏幕并按原超时值恢复 | `keep_screen_awake()`、`restore_screen_timeout()` | `adb.py` |
| `analysis.py` | 解析采样/基准协议，计算配对空闲基线、增量功率及迭代/J | `parse_sweep_log()`、`analyze_runs()`、`aggregate_curve()` | `models.py`、`power.py`、`sweep.py` |
| `plot.py` | 无第三方依赖生成频率扫频性能/功耗 SVG | `render_performance_power_svg()`、`render_efficiency_svg()` | 标准库 |
| `cli.py` | `preflight`、`capture-power`、`cpu-sweep`、禁用中的 `matrix-sweep` | `main()` | 上述一跳模块 |

## 调用关系

`__main__ -> cli -> preflight/collector 或 sweep -> adb -> adb + su shell`

矩阵目标调用链为 `cli -> screen_control -> matrix_runner -> matrix_controller -> adb/su shell`；当前恢复门禁禁止执行。只读采集器读取 `/proc/uptime` 和电池节点。矩阵设备端同步运行 pinned worker，按秒采样整机电池功率、实际 policy 频率、容量、在线掩码和温度；电量低于保留阈值、CPU掩码/温度异常会中止。CPU0 在非 worker 运行中保留为系统 keeper。频点计划按开测时 scaling 范围包含每个 CPU 的全部离散频点、纯 E/P 全部频点、六种 worker 组合的完整 E×P 频率笛卡尔积。固定频率矩阵点 2 次重复；汇总有效性要求计划重复全部通过，整机总能效与负载增量能效分别校验。新 SVG 只展示完整有效频点，不对缺失数据插值。

2026-11-10 首次 A-CPU0 项曾报 `cpufreq restoration mismatch: policy0`；本次修正为先恢复核心、再恢复 policy，并新增远端 controller 日志。设备 fault injection 记录 `data/phase-d-watchdog-fault-injection-final.raw.log` 和 `data/phase-d-watchdog-child-fault-injection.raw.log` 验证了强杀恢复与子负载终止；正常掩码/吞吐 smoke 在 `data/phase-d-matrix-controller-mask-smoke.log`。

2026-11-11 真机（`192.168.137.99:45387`）首次在故障注入窗口内直接观测到系统并发改动 cpufreq 边界：`policy0` 依次出现 384000–1996800 / 556800–2227200 / 1152000–2745600 / 1152000–1363200，`policy6` 上限在 1958400/2438400/3513600 间变化，同期 `online=0-7`、`low_power=0` 稳定且无本工具写入。保守恢复按设计不放宽并输出 `POLICY_RESTORE_RESTRICTED` + `tainted`；设备事后全核在线、无残留基准进程、屏幕超时已还原。验证器此前把这类外部限频当成工具恢复失败而中止整轮（`policy0_max/min/restore` 均 `cases_completed=0`，后续 8 例未执行），现已区分 `PASS`/`RESTRICTED`/`SKIPPED`/`FAIL`；`RESTRICTED` 表示结论不确定，退出码非 0，门禁不因此松动。

## 数据和计量边界
- `current_now` 单位为 µA，`voltage_now` 单位为 µV，须通过设备检查确认节点存在且可读。
- 电池侧放电功率 `W = |I_µA × V_µV| / 10^12`；充电/未知状态不解释为放电功耗。
- 长窗口可用放电时下降的 `charge_counter` 估算能量作交叉检查；它可能与 current_now 同源，绝不视为独立校准。
- 电池侧功率是整机电池端功率，不是 CPU 单独功耗。增量功耗只能称负载增量。
- 电流与电压为顺序读取；采样协议记录两者时间边界，报告异步时间差和有效更新率。
- 所有采集文件使用独占创建，避免覆盖已有数据；原始数据不可被分析结果覆盖。
- 当前设备的 `/proc/uptime` 文本分辨率为 0.01 秒；报告保留读数边界，不把显示为 0.000 秒解读为真正同时读取。

## 当前设备测量发现
- root 可读电池 `current_now` 和 `voltage_now`；不存在 `power_now`。
- 对 `/sys` 搜索 `energy_uj`、`power_uw`、`current_now`、`power_now` 后，只发现电池与 USB/无线输入电源的电流节点，未发现 CPU/SoC 能量计数器。
- 5.83 秒、请求 0.25 秒采样间隔的诊断中，实际中位间隔约 0.29 秒，current_now 观察到 6 次数值变化；变化周期约 1 秒量级，charge_counter 未变化。此短样本只证明存在明显量化/更新节奏，不能代替负载响应或长窗口验证。

## 当前未完成的实机验证
`scalar_mix_v2` 是自定义整数混合负载，不能代表 Geekbench 或 CPU 专用功耗。屏幕常亮的 all-online pinned 校准不是严格单核/纯组隔离；CPU/SoC 电源轨计量、外部校准、PowerLens 同步对照、热稳态建模和跨设备比较均未实现。新版保守恢复已在设备验证运行中 SIGKILL，以及 ADB 断连后正常结束恢复；系统并发调限和半锁定/恢复中断尚未充分验证。全频矩阵尚未开始。最近观察到 Android `low_power=1` 且 policy6 当前仅1017600 kHz 一个可用频点。

## 最小维护上下文
先看 `FUNCTION_INDEX.md` 定位 CLI/目标符号，再读目标模块、一跳依赖和对应 unittest。仅当失败证据跨越 ADB shell、sysfs 或数据 schema 时扩大范围。
