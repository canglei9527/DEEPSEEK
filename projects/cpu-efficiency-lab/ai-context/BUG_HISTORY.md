# Bug 历史

## 2026-11-11：恢复快照和watchdog证据核验不完整
- 已确认缺陷：旧 `verify_device_snapshot()` 只比对在线核和cpufreq上下限，不检查governor或policy的CPU映射；旧 `execute_matrix_config()` 可在缺少watchdog日志时仅凭 `CONFIG_DONE` 与数值快照匹配返回成功，断连时还会误称“独立恢复已完成”。
- 修复：核对governor与映射，不改写它们；成功还要求完整的 `WATCHDOG_DONE` 或格式有效的 `WATCHDOG_RECOVERY_OK|数字`，且任何恢复失败/taint/未知归属证据仍拒绝成功。断连只表述“数值当前一致，不能单凭此证明watchdog恢复”。
- 新增 `scheduler-status` 只读观测项目进程、保活锁、perfd请求、policy、governor、在线核、电池/屏幕；同设备、同ADB端点、同boot ID基线可比较。缺少扫描或读权限、跨设备/重启时拒绝推断恢复；匹配也不是出厂默认值。只读实测 `data/phase-p-scheduler-status.json` 与 `data/phase-q-scheduler-status.json` 同启动比较为MATCH；记录中policy0/6为walt、全8核在线、未观察项目残留。此前曾看到performance，不能臆断自动变化的原因。
- 本地针对性测试21项和compileall通过；一次全量测试120秒超时，后台重跑在本地测试进程长时间停滞后已停止该进程，均不能记为通过。修改后的设备故障注入未运行，sysfs竞态仍未解决，恢复门禁仍关闭。
- 厂商频率候选后端真机诊断：正句柄被perfd服务列为活动请求，但policy0上限及厂商vote均未变化，释放成功后起始快照一致；ownership为INCONCLUSIVE、SIGKILL未运行，不可用于扫描或解除门禁。证据见 `ai-context/PERF_REQUEST_EVALUATION.md` 及本机 `data/phase-{m,n,o}-*.{raw.log,summary.json}`。

## 2026-11-11：修复恢复归属误判与 taint 被数值相等掩盖
- 复现：新增 `tests/test_recovery_ownership.py`，修复前6项中4项失败。带 `.tainted` 或错误target的journal在数值恰好等于快照时仍返回 `ALREADY_ORIGINAL`/0；未参与锁频的policy或已tainted的policy被旧“取交集”分支改写，还可能返回成功。
- 修复：`_safe_policy_restore_shell()` 先验证journal及持久taint，再判断快照相等；一旦归属未知、受干扰或tainted，不再写任何cpufreq边界，保留系统当前值并返回2/`RESTRICTED`。只有匹配journal及观测状态的本次锁定才进入已有恢复路径。
- 防御：`execute_matrix_config()` 即使同时看到 `CONFIG_DONE`、成功watchdog标记及一致的最终快照，遇到受限/归属不明/变更/无效journal/taint证据仍拒绝成功，避免终态偶然一致抹掉中途干扰。
- 本地验证：新文件7项回归通过（执行器测试另含7种干扰证据子用例），完整 `python -m unittest discover -s tests -t .` 114项通过；`python -m compileall -q src tests scripts` 通过。既有fake-sysfs锁定/恢复中断和系统降限测试也通过。
- 真机只读接口探测：`data/phase-l-frequency-interface-probe.json`。发现厂商频率投票节点和perf服务，但尚未验证其按客户端独立聚合、超时/退出释放、保留其他系统限制的语义；没有调用未知ioctl/资源opcode，也没有改写这些节点。文件模式或root的 `test -w` 结果不等于写入成功或接口安全。
- **未解决的边界**：上述改动移除了未知归属分支的写入并修复成功误判，但已确认归属分支仍有sysfs检查/写入的非原子窗口；不能靠更多回读或flock声称消除。`INDEPENDENT_RECOVERY_VERIFIED=False` 保持不变。本轮未做设备故障注入、锁频或新性能负载；之前两轮11/11日志不验证本次修订，也不能替代独立请求型后端的论证。
- 顾问工具：本会话搜索不到可调用的DeepSeek顾问，未取得外部预评审结论。

## 2026-11-11（真机）：首次捕获系统并发限频竞态，验证器曾把它误判为恢复失败
- 环境：已脱敏 Android 设备（SM8750 / Android 16，root 可用、SELinux Enforcing、放电中、电量约 84–85%）。
- 只读采集复核：`data/phase-h-idle-check.*`，请求 20 秒、实际跨度 20.84 秒，中位间隔 1.041 秒；`current_now`/`voltage_now` 分别观测到 20/16 次数值变化（周期约 1.04 秒）。`current_now×voltage_now` 积分 14.57 J，`charge_counter` 反向估算 15.26 J，相差 4.78% —— 这是同一电量计的窗口内一致性检查，不是独立校准。
- 基准编译：NDK `25.2.9519653` 编译 `native/cpu_mix_bench.c` 成功，12832 字节；本机需把 `TEMP`/`TMP` 指向可写目录，否则 clang 报 `unable to make temporary file`。
- **核心发现（首次真机证据）**：策略边界在本次会话中被系统持续改动，且改动发生在故障注入窗口内。只读连续观测与多次 `preflight` 记录到 `policy0` 依次出现 384000–1996800、556800–2227200、1152000–2745600、1152000–1363200，`policy6` 上限也在 1958400/2438400/3513600 之间变化；`online=0-7`、`low_power=0` 全程稳定，未由本工具写入。这与之前只能靠模拟推断的“不可原子比较写竞态”一致，现在有真机原始日志。
- 失败用例与判定：`policy0_max` / `policy0_min` / `policy0_restore` 三个注入用例的 watchdog 日志均记录 `POLICY_RESTORE_RESTRICTED` 与 `policy-policy0.state.tainted`（如 `policy0 1152000 2745600 1996800 APPLY_MAX_INTENT`）。保守恢复正确地**没有**把系统收紧后的范围放宽回快照，而是保留交集并标记受限，最终设备处于安全状态。
- **验证器缺陷（本次修复）**：旧逻辑把上述“系统漂移”当成工具恢复失败 —— `verify_device_snapshot` 抛 `cpufreq restoration mismatch: policy0` 后直接中止整个验证，后续 8 个用例根本没跑。修改前实测：`policy0_max/min/restore` 均为 `rc=2 / cases_completed=0`；`policy6_*` 因 `policy0` 此刻只剩单频点而报 `no interior frequency inside current limits`。
- 修复：新增 `_external_restriction_policies()`，仅当同时存在 `POLICY_RESTORE_RESTRICTED|<policy>|` 与对应 `policy-policy0.state.tainted` 时才把该策略判为“外部限频”，该用例标记 `RESTRICTED`（结论不确定，不算 PASS）；单频点策略改为 `SKIPPED` 而不是中止整轮；`snapshot_error` 不再被硬编码为 `True`，实际收录到 summary。退出码仍仅在全部 PASS 时为 0，`RESTRICTED`/`SKIPPED` 一律非 0，门禁不会被这类结果松动。
- 残留风险与边界：这次证据只证明“系统会并发改动限频”和“保守恢复不会放宽”，**不证明**半锁定写入在所有时序下都能安全恢复。`INDEPENDENT_RECOVERY_VERIFIED` 保持 `False`，`matrix-sweep` 继续 fail-closed。
- 传输层旁证：无线 ADB 在本会话多次出现 `error: closed` / `offline`，`adb connect` 一度失败；`phase-h-probe-case4/5` 的 `no interior frequency` 失败与之一致，不能全归因于代码。真机门禁验证应在有线或稳定无线链路上重跑。
- 工具链缺陷（本次修复）：`AGENTS.md` 与 README 记录的 `python -m unittest discover -s tests` 在干净检出上直接失败（`ModuleNotFoundError: cpu_efficiency_lab` / `run_pinned_dynamic`，37 项中 8 错误 1 失败）。现补充 `tests/__init__.py` 注入 `src`/`scripts` 路径，并新增 `scripts/test.ps1`；验证命令更新为 `python -m unittest discover -s tests -t .`（`-t .` 必需）。

## 2026-11-10：矩阵测试首项频率恢复核验失败
- 现象：`data/phase-d-sm8750-matrix.raw.log` 首项 `A-CPU0` 报 `cpufreq restoration mismatch: policy0`，自动中止。失败时设备脚本输出未写入主机原始日志，因此不能确定首次不一致的具体值和根因。
- 恢复复查：随后只读检查 CPU0–CPU7 在线，`policy0` 384000–1996800 kHz，`policy6` 1017600–1689600 kHz，未发现残留基准进程；这是复查时的状态，不代表过程中所有时刻均已恢复。
- 附带安全缺陷：当前设备侧 watchdog 只向控制器发信号，控制器异常死亡时不会独立恢复核心/频率或保证子负载终止。主机异常恢复会向全部策略重写快照，可能与系统动态调整竞争；不得把该机制当作已验证的独立恢复。
- 处理：在 CLI、矩阵调度和执行入口设置 fail-closed，禁止实际矩阵 hotplug 运行；保留旧日志。恢复入口需先实现设备端独立超时恢复、保留失败原始输出、避免覆盖系统更新的限制，并通过故障注入和实机恢复验证。
- 旁发现的屏幕恢复记录问题：`data/phase-d-screen-keep-awake-state.json` 的 JSON 对象后附有 `APPLY_OUTPUT` 终端文本，严格 `json.loads` 会失败，旧逻辑因此静默采用当前超长 timeout，导致 `.screen-state.json` 记录 `restore_timeout_ms=2147483647` 而非原值 `1800000`。现新增安全 JSON 前缀读取，仅接受已知日志尾部，并对未知/不匹配记录要求显式 `--restore-screen-timeout-ms`；新增单测覆盖。
- 测量边界：亮屏无锁频、全核在线的 pinned worker 校准单独记录在 `data/phase-d-awake-percore-pinned-calibration-retry.raw.log`，不是严格隔离数据，也不能用于宣称已完成矩阵。自然负载短校准另见 `data/phase-d-awake-natural-calibration.raw.log`。

## 2026-11-11：修正恢复顺序、独立 watchdog 与掩码协议
- 根因修复：配置结束时先恢复原在线核心，再恢复所有原 cpufreq policy；CPU6/7 下线时 `policy6` 可能消失，不能先写该 policy。主脚本也会终止仍在运行的 benchmark。
- 独立恢复：用 `nohup setsid` guardian 保存原 core/policy 快照和 benchmark PID/starttime。故障注入分别 SIGKILL 控制器及正在运行的 benchmark，guardian 均恢复 CPU0–7、policy0/6 原范围并停止子负载。证据：`data/phase-d-watchdog-fault-injection-final.raw.log`、`data/phase-d-watchdog-child-fault-injection.raw.log`。
- 正常实机 smoke：CPU0-only 期间确认只有 CPU0 在线、native worker 亲和性正常，trap 恢复全核及频率范围；修复 `sample_online` 只记录实际在线 CPU。记录：`data/phase-d-matrix-controller-mask-smoke.log`、`data/phase-d-watchdog-native-smoke.log`。
- 日志：设备端 `controller.log` 与 `watchdog.log` 保存在每配置恢复目录；主机成功/错误路径均读取并写入 matrix raw。
- 电量保护：每个样本读取电量百分比，低于设定下限（默认60%）中止并恢复；80°C CPU/43°C 电池保护继续生效。
- 计划更新：逐核和纯组使用开测范围内全部可用频点；六种 E/P worker 数量组合使用完整 E×P 频率笛卡尔积。最近 policy0 8点、policy6 7点，对应 B/C/D 为62/31/336配置，名义预算约117.3分钟，尚未启动完整矩阵。B/C/D 空闲采样改为每侧两点，避免配对空闲功耗积分必然无数据。
- 新发现：恢复脚本会不加条件写回全部 policy 的开测快照；若系统在运行期间收紧限频，可能覆盖新限值。之前 policy0 最小值曾在恢复后由384000变为556800 kHz，实机记录 `matrix_recovery_501f7586dfa92ddcb704ac495d018671/controller.log`；这说明快照并非总是静态，但不能单凭它断言当时是热限频。ADB 断连、恢复失败也未验证。
- 门禁：再次设置 fail-closed，显式确认也不能绕过。CPU0 keeper 约束保留。

## 2026-11-11：保守恢复与实时系统限频
- 实现中的修复：主控制器、独立 watchdog 和主机回退共享保守恢复；只在当前 min=max=本次锁频时释放锁，否则对当前范围和快照取交集，不放宽更严格的界限；不匹配报告 `RESTRICTED` 并中止。sysfs 无原子比较写操作，存在竞态，不能当成所有场景已解决。
- 本地验证：测试覆盖当前限制收紧、半锁定、无法读取、watchdog 恢复失败和错误成功标记。单元测试52项通过；旧无电量字段的矩阵日志也可解析。
- 实机验证：运行中 SIGKILL 子负载恢复见 `data/phase-d-conservative-watchdog-child-test.raw.log`；ADB 断连后控制器继续运行直至正常结束并恢复，见 `data/phase-d-conservative-adb-disconnect-test.raw.log`。后者不是 watchdog 强制接管的证明。半锁定或恢复中断及系统并发调限仍未充分验证，门禁保持关闭。
- 最近只读预检：电量87%、全八核在线、无 benchmark；Android `settings get global low_power` 返回1；policy0 556800–1996800 kHz，policy6 当前为1017600–1017600 kHz，仅1个可用频点。之前 policy6 上限曾为2438400 kHz，不可推断变更原因，也不得擅自放宽或关闭系统省电模式。即使恢复门禁已解除，当前 P 核无法多频扫描，且阶段 B 的至少两有效频点门槛会阻止进入后续阶段。
- 另需验证采样质量：上述断连故障注入的 CPU0-only 15 秒配置实际计时 16.211 秒，负载电池采样有效覆盖仅 7.605 秒，不满足65%阈值，且增量功率非正；不能把此项当成有效能效结果。应优化采样/测量设计并以正常非故障实机数据复核，不得降低有效性阈值掩盖问题。

## 2026-11-11：当前限频下的快速全在线绑核实测
- 应用户要求尽快实测：`scripts/run_pinned_dynamic.py` 在全八核在线、动态调频下跑 CPU0–CPU7 单 worker、E6、P2及6种混合 worker，共16项；未改 cpufreq、在线掩码或省电模式。每项2秒预热、12秒计分，前后各4次空闲采样，CPU80°C/电池43°C/电量60%保护生效。
- 数据：`data/phase-e-fast-pinned-dynamic.raw.log`、`.summary.csv`、`.summary.json`，16/16 达到脚本的功率覆盖/在线掩码/worker 亲和性条件，负载采样覆盖92.5%–94.2%；CPU最高46.5°C、电池27.3°C。每项仅一次，不能当作统计稳定的最终频率能效结论。
- 用户请求的严格关核逐频及固定频率混合矩阵仍未运行，恢复门禁继续关闭；动态实测中 policy6 系统限频由一度1017600 kHz变化至2438400 kHz（未由本工具更改），不能把频率中位数解释为锁定频率或归因于特定设置。
- 测后全核在线、电量85%、无残留 benchmark；已将屏幕超时恢复到原值1800000 ms，记录 `data/phase-e-fast-pinned-dynamic.screen-state.json`。

## 2026-11-11：按用户更正停止动态复测，继续处理锁频安全
- 用户明确要求锁定每个频点而不是动态频率。停止了 `phase-f-two-repeat-dynamic` 第二轮：第一轮16项、第二轮11项有效；这些不是固定频率数据，也不构成两轮完整复测。已自动恢复屏幕超时到1800000 ms，之后核验全核在线、无基准进程。
- 发现 watchdog 与 benchmark 启动之间的登记窗口：原实现先启动负载再记录PID；控制器在此窗口死亡可能留下未登记子负载。修改为先启动等待门控的子进程，原子登记PID/starttime之后才允许 exec；父进程提前死亡时等待子进程直接退出。
- 设备故障注入在登记前、登记后/放行前、放行后、负载运行中四个时点 SIGKILL 控制器，均验证子负载终止和当时 CPU/policy 快照恢复；原始日志 `data/phase-g-start-gate-fault-injection.raw.log`。逐样本增加锁定上下限变化和电池充电状态变化检查。
- 仍不能解除矩阵恢复门禁：新增主机端 fake-sysfs 故障注入，模拟先写 `policy0/scaling_max_freq=960000` 后立即杀死控制器；看门狗恢复了核心，但为避免覆盖系统新限制，保留 `[384000,960000]` 而非原 `[384000,1996800]`，明确报告 `RESTRICTED`。设备中途下线首核的故障注入已通过，日志 `data/phase-g-first-offline-fault.raw.log`。真实锁频半写故障尚未在手机执行，不能人为留下未知归属限频。
- 系统同时更改 cpufreq 的不可原子比较写竞态也尚未解决。只读检查发现 Qualcomm `perf_lock_acq` 和相关 perfd 配置，但不清楚其资源仲裁是否保留其他系统限频，未调用未知 opcode。不得将故障测试当成正式逐频测量，也不得用其他入口绕过门禁。

## 初始记录
- 创建日期：2026-11-10
- 技术路线：Python 3.9 标准库 CLI + ADB/root 只读采集；本机分析电池侧功耗。

## 2026-11-10：ADB 普通 shell 与 root sysfs 访问边界
- 现象：普通 `adb shell` 无法列出电池目录/读取 uevent。
- 根因：读取命令没有在整个命令串外层正确调用 `su -c`；通过 `su -c` 确认 `current_now` 与 `voltage_now` 可读。
- 设备证据：`current_now`、`voltage_now` 存在；`power_now`、`current_avg` 不存在；SELinux 为 Enforcing。
- 处理原则：采集器使用 root shell 只读读取现有节点；不改权限、不改 SELinux、不伪造节点；`power_now` 在主机侧由电流×电压计算。
- 验证边界：实现后完成设备只读预检和实机短采样；目标 0.25 秒采样实际中位间隔约 0.29 秒，current_now 观察到约 1 秒量级的变化周期。
- 长窗口交叉检查：屏幕亮、MIUI Launcher 前台且放电，300 秒请求窗口实际覆盖 312.83 秒；current_now×voltage_now 积分约 190.56 J，charge_counter 减少 12,000 µAh、按平均电压估算约 189.05 J，相差约 0.79%。屏幕状态前后均 Awake/Launcher。
- 边界：两种读数可能来自同一电量计，差异仅证明本窗口内部一致性，不是独立校准或绝对精度保证。该样本是屏幕亮主屏整机功耗，不是 CPU 基线；尚未验证 CPU 负载响应或 PowerLens 数据源。未发现 CPU/SoC `energy_uj`/`power_uw` 计数器。
