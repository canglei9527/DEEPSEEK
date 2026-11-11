# CPU Efficiency Lab 维护规则

## 项目边界
- 根目录：`E:/DEEPSEEK ai/cpu-efficiency-lab`；本项目仅采集和分析 Android CPU 能效实验数据。
- 当前目标锚点：只读预检/电池采集、已授权的温控保护扫频、staged matrix 与本地分析绘图；架构索引见 `ai-context/ARCHITECTURE.md`、`ai-context/FUNCTION_INDEX.md`。
- `matrix-sweep` 当前 fail-closed：新版保守恢复已完成部分本地模拟、设备 SIGKILL 和 ADB 断连验证，但 sysfs 并非原子写入，半锁定或恢复中断、并发限频变更尚未充分验证。不得绕过 `require_verified_hotplug_recovery()`；恢复门禁解除前需完整故障注入与实机验证，保留日志。
- 禁止扫描父级工作区寻找项目代码。读取时只打开锚点、一跳依赖和最小测试。

## 设备安全
- 允许的只读设备操作：读取 `getprop`、电池/CPU/热状态 sysfs 节点、采集样本。
- 未获单独确认不得：锁频、改 governor、改变电源模式、解除限频/温控、运行 CPU 满载基准、安装/启动/停止应用、改写或删除设备文件。
- 绝不关闭 SELinux、温控或电池保护；不伪造 sysfs 节点。
- 采样需保存原始读数、时间戳、单位、采集间隔和数据来源。电池功耗不得标成 CPU 电源轨功耗。

## 修改约束
- 只修改本项目文件；保留用户数据；结果文件默认禁止覆盖。
- 标准库优先，兼容 Python 3.9；不得自动安装依赖。
- 配置集中在 `src/cpu_efficiency_lab/config.py`。

## 验证
- 单元测试：`python -m unittest discover -s tests -v`
- CLI 导入检查：`python -m compileall -q src tests`
- 实机预检、采样、CPU 负载和频率控制分别报告；静态测试不能替代实机验证。
