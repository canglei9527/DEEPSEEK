# 代码导航

本索引只登记稳定入口和维护路由；设备安全边界与验证要求以 `AGENTS.md` 为准。

| 任务 | 实现入口 | 最小验证 |
|---|---|---|
| 只读导出当前设备调度配置 | `src/cpu_efficiency_lab/scheduler_sources.py` → `collect()` | `tests/test_scheduler_sources.py` |
| 离线分析官方调度配置 | `scheduler_report.py` → `analyze_sources()` / `build_report()`；CLI `scheduler-report` | `tests/test_scheduler_report.py` |
| 八核历史能效曲线与组合点 | `efficiency_report.py` → `analyze()` / `build_report()`；CLI `efficiency-report` | `tests/test_efficiency_report.py` |
| 后台调度 systemless 候选 ZIP | `ksu_module.py` → `candidate()` / `build_module()`；CLI `ksu-module` | `tests/test_ksu_module.py` |
| 配对空闲基线与增量筛选 | `baseline.py` → `paired_baseline()`；sweep 与 matrix 离线分析共用 | `tests/test_baseline.py`, `tests/test_analysis.py`, `tests/test_matrix_analysis.py` |
| Windows 桌面操作 | `desktop.py` → `Desktop` + `desktop_views.py`；“分析”页支持逐核选择/组合热力图，“设备”页预检自动保存并回填计划；离线命令复用 `cli.py` | `tests/test_desktop.py` + 可选真实 Tk 集成 |
| 历史测量质量报告 | `historical_report.py` → `build_report()` | `tests/test_historical_report.py` |
| 其余采集、矩阵与恢复任务 | `FUNCTION_INDEX.md` | 按对应符号的最小测试执行 |

离线调度报告输入为 `scheduler-sources` 导出目录的 `manifest.json` 和已保存的原始文件，不连接 ADB，不修改设备。报告数值以 `report.json` 为规范结构，HTML/Markdown 由同一结构渲染；所有原始属性/Profile/聚合成员、静态覆盖来源和厂商嵌套字段均可追溯。来源哈希不符、必需配置类型缺失或配置格式错误时，在创建输出目录前失败。

`efficiency-report` 将历史单核 TSV 分开绘制 CPU0–CPU7，并报告已测频率组合中最低整机电池侧 nJ/op 的记录和缺失格；不插值、不跨核心合并重复，也不把电池侧功率标成 CPU 电源轨功耗。历史 TSV 缺少前后配对的空闲原始窗口，不能追溯扣除底噪。新的 sweep/matrix 分析使用 `baseline.py` 配对前后空闲窗口估计负载增量，并按漂移、采样间隔和空闲观察范围筛除不可靠增量指标。

`ksu-module` 仅从校验过的当前配置导出本地候选 ZIP：复用既有 `CpuPolicyPack`，只加入两个后台聚合，并保存原配置、哈希和安装条件。此 systemless overlay 不是内核调度器替换，不代表实机 profile 已加载或功耗改善；不会由工具安装。实机安装/重启和性能对照须依项目设备安全规则另行授权与验证。
