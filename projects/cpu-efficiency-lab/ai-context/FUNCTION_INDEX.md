# 函数索引

| 符号 | 源文件 | 职责 | 一跳项目自有依赖 | 最小测试 | 扩大范围条件 |
|---|---|---|---|---|---|
| `main` | `src/cpu_efficiency_lab/cli.py` | 解析只读、旧式扫频和当前 fail-closed 的 `matrix-sweep` 子命令 | `preflight.py`, `collector.py`, `sweep.py`, `matrix_runner.py`, `analysis.py`, `plot.py` | `tests/test_cli.py` | 参数到设备行为契约变化 |
| `AdbClient.run` | `src/cpu_efficiency_lab/adb.py` | 有超时、错误信息地执行本机 ADB | `config.py` | `tests/test_adb.py` | ADB 命令拼接/Windows subprocess 失败 |
| `AdbClient.run_root_script` | `src/cpu_efficiency_lab/adb.py` | 以 `su -c` 执行调用方负责安全边界的 shell 脚本 | `AdbClient.run` | `tests/test_adb.py` | Android shell 引号或 SELinux 边界失败 |
| `collect_preflight` | `src/cpu_efficiency_lab/preflight.py` | 读取目标身份、root、传感器与频率 policy | `adb.py`, `models.py` | `tests/test_preflight.py` | 设备输出 schema 或权限语义改变 |
| `discover_cpu_topology` | `src/cpu_efficiency_lab/topology.py` | 只读发现 core online、容量和 hotplug 节点状态 | `adb.py` | `tests/test_topology.py` | sysfs topology/online 语义改变 |
| `all_frequency_levels` / `build_stage_a/b/c/d` | `src/cpu_efficiency_lab/matrix_plan.py` | 在当前 scaling 范围内生成所有逐核频点、纯组频点和完整 E×P 网格 | `topology.py`, `preflight.py` | `tests/test_matrix_plan.py` | policy/core 分组或频点策略改变 |
| `build_matrix_config_script` | `src/cpu_efficiency_lab/matrix_controller.py` | 生成受热保护、核心掩码核验及原值恢复 trap 的临时设备脚本 | `matrix_plan.py`, `topology.py`, `sweep.py` | `tests/test_matrix_controller.py` + shell `-n` | 改写顺序、恢复字段或超时保护改变 |
| `execute_matrix_config` | `src/cpu_efficiency_lab/matrix_controller.py` | 配置执行、设备/独立恢复和主机状态核验；当前 fail-closed | `adb.py`, `matrix_controller.py` | `tests/test_matrix_controller.py` + device fault injection | 动态限频恢复、子负载终止或数据协议变化 |
| `require_verified_hotplug_recovery` | `src/cpu_efficiency_lab/matrix_controller.py` | 强制阻止未经充分恢复验证的矩阵，显式确认也不能绕过 | 无 | `tests/test_matrix_controller.py` | 恢复代码变更必须重新执行故障注入 |
| `verify_matrix_recovery.main` / `_external_restriction_policies` | `scripts/verify_matrix_recovery.py` | 真机恢复故障注入验证；区分 `PASS`/`RESTRICTED`/`SKIPPED`/`FAIL`，仅全部 `PASS` 时退出码为 0 | `matrix_controller.py`, `matrix_runner.py`, `screen_control.py` | `tests/test_verify_matrix_recovery.py` | 判定规则、用例清单或设备写入边界变化 |
| `run_full_matrix` | `src/cpu_efficiency_lab/matrix_runner.py` | staged A-F 编排所有离散频点和完整混合频率网格，当前 fail-closed | `matrix_controller.py`, `matrix_analysis.py`, `matrix_plot.py` | local tests + verified-device gate | 远端恢复、核心在线稳定性或原始数据持久化变化 |
| `parse_matrix_log` / `analyze_matrix_runs` / `aggregate_matrix` | `src/cpu_efficiency_lab/matrix_analysis.py` | 解析 worker 亲和性、policy 频率、在线掩码和整机电池能效；总能效与配对增量能效独立有效性，固定点按计划重复数汇总 | `matrix_plan.py`, `models.py`, `power.py` | `tests/test_matrix_analysis.py` | raw schema/有效性规则变化 |
| `render_matrix_pareto_svg` / `render_single_core_frequency_curves` / `render_mixed_frequency_heatmap` | `src/cpu_efficiency_lab/matrix_plot.py` | 绘制有效矩阵 Pareto、固定频率逐核能效曲线和缺测不插值的 E/P 热力图 | 标准库 | `tests/test_matrix_plot.py` | 统计字段或绘图口径变化 |
| `keep_screen_awake` / `restore_screen_timeout` | `src/cpu_efficiency_lab/screen_control.py` | 记录、唤醒并恢复 screen timeout，不自动熄灭显示 | `adb.py` | device smoke; `_saved_screen_timeout`: `tests/test_cli.py` | 屏幕状态/设置权限变化 |
| `_saved_screen_timeout` | `src/cpu_efficiency_lab/cli.py` | 从本机状态文件安全读取超时值，支持已知附加的 `APPLY_OUTPUT` 日志尾部 | 标准库 | `tests/test_cli.py` | 状态文件 schema 或附加日志格式变化 |
| `build_sampler_script` | `src/cpu_efficiency_lab/collector.py` | 生成有界、只读的 Android shell 采样循环 | `config.py` | `tests/test_collector.py` | shell 协议或远端命令状态发生变化 |
| `available_points` | `src/cpu_efficiency_lab/sweep.py` | 只列当前 scaling min/max 内可用频点并绑定 policy CPU | `models.py` | `tests/test_sweep.py` | 设备 policy schema/频率约束改变 |
| `discover_thermal_sensors` | `src/cpu_efficiency_lab/sweep.py` | 识别 CPU 与 battery thermal_zone 节点及初始温度 | `adb.py` | `tests/test_sweep.py` | 温区类型或温度单位不符合预期 |
| `build_point_script` | `src/cpu_efficiency_lab/sweep.py` | 生成单点限频、每秒采样、温控中止及 EXIT 恢复脚本 | `config.py`, `models.py` | `tests/test_sweep.py` | shell、热保护或频率写入顺序改变 |
| `run_sweep` | `src/cpu_efficiency_lab/sweep.py` | 执行部署/冒烟/随机扫频并在点间校验原限频 | `adb.py`, `preflight.py`, `sweep.py` | 实机 smoke + `tests/test_sweep.py` | 设备权限、断连、温控或恢复核验失败 |
| `parse_sweep_log` | `src/cpu_efficiency_lab/analysis.py` | 解析电池/热/频率读数和基准性能标记 | `models.py` | `tests/test_analysis.py` | raw protocol 字段或单位变化 |
| `analyze_runs` | `src/cpu_efficiency_lab/analysis.py` | 配对 idle baseline，积分电池侧 W/J 并校验采样覆盖/频率 | `power.py`, `sweep.py` | `tests/test_analysis.py` | 时间窗、单位或有效性门槛变化 |
| `render_performance_power_svg` | `src/cpu_efficiency_lab/plot.py` | 输出增量整机电池功率-吞吐 SVG | 标准库 | `tests/test_analysis.py` | 坐标/误差条或声明边界变化 |
| `render_efficiency_svg` | `src/cpu_efficiency_lab/plot.py` | 输出按目标频率分组的增量迭代/J SVG | 标准库 | `tests/test_analysis.py` | 净功率定义或频率标签变化 |
| `render_total_battery_power_svg` | `src/cpu_efficiency_lab/plot.py` | 输出性能-总整机电池功耗 SVG | 标准库 | `tests/test_analysis.py` | 整机功耗范围/标签变化 |
| `render_total_efficiency_svg` | `src/cpu_efficiency_lab/plot.py` | 输出总电池能量口径的迭代/J SVG | 标准库 | `tests/test_analysis.py` | 能量分母范围/标签变化 |
| `parse_samples` | `src/cpu_efficiency_lab/collector.py` | 校验和解析原始行 | `models.py` | `tests/test_collector.py` | 新样本字段/单位加入 |
| `value_change_intervals` | `src/cpu_efficiency_lab/collector.py` | 计算观测到的传感器数值变化间隔（不等同硬件更新率） | `models.py` | `tests/test_collector.py` | 传感器字段/采样节奏改变 |
| `write_csv` | `src/cpu_efficiency_lab/collector.py` | 独占创建本地原始 CSV | `models.py`, `power.py` | `tests/test_collector.py` | 输出 schema 变化 |
| `battery_power_w` | `src/cpu_efficiency_lab/power.py` | 状态有效性检查和 µA×µV 转 W | 无 | `tests/test_power.py` | 电流符号/单位经设备证据推翻 |
| `average_discharge_voltage_uv` | `src/cpu_efficiency_lab/power.py` | 按时间加权计算放电窗口电压 | `models.py` | `tests/test_power.py` | 电压采样与 current_now 时序不匹配 |
| `charge_counter_energy_j` | `src/cpu_efficiency_lab/power.py` | 由放电 charge_counter 变化估算能量，仅交叉检查 | 无 | `tests/test_power.py` | 计数器符号/单位被设备证据推翻 |
| `charge_counter_average_current_ma` | `src/cpu_efficiency_lab/power.py` | 由计数器变化推算窗口平均电流 | 无 | `tests/test_power.py` | 单位或窗口覆盖语义变化 |
| `integrate_energy_j` | `src/cpu_efficiency_lab/power.py` | 按单调时间对放电功率梯形积分 | `models.py` | `tests/test_power.py` | 缺样/状态切换处理改变 |
