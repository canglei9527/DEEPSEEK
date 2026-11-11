# 独立频率请求后端：验证与当前阻碍

## 目的和安全边界

尝试使用厂商 perfd 的带期限请求替代直接覆盖 `scaling_min_freq` / `scaling_max_freq`：释放自己的句柄而不是重写别人的共享状态。它**尚未接入正式扫描**，不绕过原 `require_verified_hotplug_recovery()`，不解除 `INDEPENDENT_RECOVERY_VERIFIED=False`。

即使厂商服务能正确聚合其自身客户端，也不自动证明它与其他直接 sysfs 写入者、热限频、内核其他 QoS 请求的仲裁正确。需要分别验证系统降限期间的有效上限、两客户端不同释放顺序、正常退出、SIGKILL、到期和守护进程异常等；不能以API返回句柄作为生效证明。

## 证据

- 设备 `libqti-perfd-client.so` 导出 `perf_lock_acq` / `perf_lock_rel`；公开的 [LineageOS HAL utils.c](https://github.com/LineageOS/android_vendor_qcom_opensource_power/blob/lineage-22.2/utils.c) 中有对应签名和句柄调用方式；[performance.h](https://github.com/LineageOS/android_vendor_qcom_opensource_power/blob/lineage-22.2/performance.h) 有候选资源编码。公开HAL不代表本机供应商实现或冲突语义。
- 真机 `/vendor/etc/perf/targetresourceconfigs.xml` 将major `0x2` 的min/max映射到 `/sys/kernel/msm_performance/parameters/cpu_min_freq` / `cpu_max_freq`；`targetconfig.xml` 的该SoC为6核big+2核prime。设备 `libqti-perfd.so` 的 `ResourceInfo::ParseNewRequest` 解析字段与候选编码布局一致；perfboost配置包含对应资源值。**逻辑簇映射、有效约束、释放仲裁均未被成功试验证实。**
- 实机 `phase-m`、`phase-n`、`phase-o` 三次独立诊断均使用单个8秒时限的已有候选max资源，尝试把 policy0 上限从2745600收紧到748800 kHz。每次都返回正句柄，随后 `perf_lock_rel` 返回0，但 policy0 上限没有变化；`phase-n/o` 中厂商vote节点在采样期间也保持硬件最大值。`phase-o` 的 `dumpsys vendor.perfservice` 证实该句柄在服务活动客户端列表中。故“获得句柄”和“服务列出客户端”都不代表频率请求实际生效。
- 请求没有在约2秒观测窗口生效时，验证器立即释放自身句柄，不再做双请求或SIGKILL；等待超过8秒后复查：8核在线、无残留probe/bench、两个policy边界及governor与各次起始快照一致，设备在放电。电量/温度和观测见每次 summary。未执行性能基准、未关闭温控或SELinux、未直接向cpufreq sysfs写入。
- `phase-o` 终态为 `probe=PASS`（只代表能加载符号）、`ownership=INCONCLUSIVE`、`crash_ttl=NOT_RUN`、`postflight_restored=true`。`phase-m/n` 的旧版脚本把已开始但未生效的ownership写成 `NOT_RUN`；应按原始日志解释为 **INCONCLUSIVE**，不改写历史证据。
- 节点权限显示 vendor vote 节点为 `0444 root:root`，但实际加载 `libqti-perfd.so` 的 `vendor.qti.hardware.perf2-hal-service` 是root并持有capabilities；不能仅凭文件mode、root的 `test -w` 或符号判断拒绝写入的根因。只读logcat未找到明确拒绝日志。原因可能包括资源映射、状态/优先级、服务策略或节点实现等，**尚未定位，不做猜测性改权限或调用其他未验证opcode**。
- 本机证据保存在 `data/phase-{m,n,o}-*.raw.log` 与 `.summary.json`；`data/perf-api-research/` 是仅供本地分析的厂商库副本，按项目约定不得上传。`data/phase-l-frequency-interface-probe.json` 记录初始只读探测。

## 验证器

`native/perf_request_probe.c`：只允许固定的候选资源和内部选择的当前范围内频率；不接受任意opcode、任意handle或sysfs写命令。开测前校验SoC、policy拓扑、可用OPP、全核在线、放电、电量和温度；频率请求TTL为8秒（SIGKILL专用为3秒）。它在每次观测前检查CPU 80°C、电池43°C阈值，异常退出只释放本进程登记的句柄；SIGKILL用例仅在前序验证成功后执行。

`scripts/verify_perf_requests.py`：显式 `--confirm-device-writes`，独占创建日志，核对测试前后状态，失败时保留异常中捕获的远端stdout/stderr线索；必须分别看到6个不重复CASE和两个policy恢复结果才可算ownership通过，事后等过全部TTL后只读复查，不向设备写回旧频率。异常情况下以不同前缀保留证据，绝不覆盖。

构建（离线编译，不连接设备）：

```powershell
.\scripts\build-perf-request-probe.ps1 -NdkRoot $env:ANDROID_NDK_ROOT
```

验证器属于设备写入/故障注入工具，不是性能测量；不作为 `two-hour-sweep` 的隐式替代。当前观测未满足启用条件，下一步需要获得**匹配此设备的资源应用/仲裁接口与权限文档或供应商实现证据**，再针对明确假设验证；不得盲试资源号、调整系统权限或把句柄存在当作锁频成功。顾问工具在本会话不可调用。
