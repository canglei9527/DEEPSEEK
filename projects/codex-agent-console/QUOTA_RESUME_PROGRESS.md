# 额度续跑集成进度

- 目标：在现有 Windows 控制台中恢复被订阅额度明确打断的同一桌面会话；人工停止不自动恢复；后台低功耗。
- 授权：仅本项目源码、测试和本地 release 构建；保留原有未提交修改，不提交、不推送、不发布。
- 当前阶段：实现与协议验证。2026-10-19。

## 实现边界

- `codex_desktop_ipc.py`：连接已运行桌面端的本机 IPC，跟随真实会话状态；不新开 app-server，不更改模型、沙箱、权限。
- `codex_resume_sources.py`：按需读取会话清单和额度，不生成模型请求、不读取认证凭据、不兑换重置或购买额度。
- `codex_quota_resume.py`：结构化失败识别、人工干预排除、原子本地记录、去重与单实例监控。
- `codex_resume_ui.py`：开关、状态、取消本次和立即检查；可见时才刷新内存状态。
- `codex_agent_console.py`：工具栏与生命周期集成。

## 验收矩阵

- [ ] 明确 usageLimitExceeded / usageLimitReached 且 failed 才入队；普通429、网络错误、提示文字、百分比均不能入队。
- [ ] interrupted、goal paused、完成、归档、新轮次、取消本次阻止恢复。
- [ ] 重置时刻到达仍须服务端 ordinaryUsageAllowed=true，且订阅窗口不再耗尽。
- [ ] 恢复前复核真实桌面最新状态；同一轮次至多发送一次，未知结果不重发。
- [ ] 关闭、断线、桌面重启、配置损坏、两个控制台实例默认安全处理。
- [ ] 无任务时阻塞等待；清单最多每分钟读取一次；等待已知重置期间不请求模型或反复查额度。
- [ ] 最小回归测试、既有测试、真实只读桌面接入、GUI烟测、EXE构建、空闲CPU增量观察。

## 已确认的接口限制

- 本机独立 app-server 会把桌面正在运行的线程回放为 notLoaded/interrupted，不能据此判断任务结束。
- 使用当前桌面本机 IPC owner/follower，属于版本相关协议；不支持时停止恢复并显示连接状态。
- 当前桌面 start-turn 没有服务端 expectedTurnId 原子条件；发送前需要新快照及取消检查，不能把该检查宣称为服务端原子操作。
- 额度错误是结构化 codexErrorInfo，不能用文字或账户额度耗尽推断任何任务应恢复。

## 本轮验证与未完成项

- 120项测试 PASS；新增2项 usageLimited Goal拒绝发送/乐观turn不提前确认测试 PASS。
- 同桌面 owner 快照和只读 account/rateLimits/read 实测 PASS；未发起真实续跑。
- 20秒真实只读监听：CPU时间0.0469秒，队列0项，工作线程正常退出。
- Tk主窗1120x760、续跑子窗900x520，打开/重开/关闭 PASS，初始关闭监控。
- EXE构建 PASS；PYZ内最新Goal保护常量确认 PASS；打包后GUI启动/正常关闭 PASS（exit 0）。
- 完整目标尚未完成：原生Goal usageLimited缺少可达owner恢复接口；真实订阅自然重置后的完整续跑未实测；发送接口无原子expectedTurn条件，不能绝对排除最后瞬间的人工操作竞态。

## 2026-10-20 交付与阻塞审计

- 本地候选 EXE：`release/CodexAgentConsole-v2.2.0.exe`，13489121 bytes。
- SHA256：`8ccdd8ed8aa253adf640a9af64c4ab190dd7f6a1a5c7eba7d8f63a607ca12f14`。旁附 validation.json。未覆盖旧程序，未提交/推送/发布，监控默认关闭。
- 最终整套122项测试 PASS；打包过程中源码分析包含最新Goal保护，归档复核 PASS。
- 已连续三次目标轮次确认同一外部阻碍：本机Desktop owner IPC没有Goal恢复handler或原子条件发送。现有API不足以完成原始完整要求；重复发送普通“继续”不能恢复Goal循环，也不能证明最后瞬间人工停止永不误触。故完整目标应标blocked，不能complete。
- 解除条件：桌面提供可达的Goal恢复及条件发送接口，或用户明确批准改变相关范围/交互约束；真实额度周期验证仍待执行。

## 2026-10-20 恢复目标后的第一轮复核

- 当前bundled CLI仍为0.163.0-alpha.10.2；同桌面owner真实只读复核为active/Goal active，未发起真实turn。
- 修复恢复确认的时序边界：runtime先active但最新实体仍为旧quota failed时，保持resuming，不提前确认或取消；后续明确interrupted仍取消。
- 新回归及相关49项测试 PASS，py_compile/diff-check PASS。
- 正在重新构建候选EXE，exec session 40350；须完成后刷新SHA256/validation并做打包GUI检查。当前release候选文件仍是上一轮已验证版本，尚不含本轮时序修复。
- 原生Goal/原子发送外部限制未变；本次恢复后的阻塞审计重新计数为第1轮，仍有构建收尾工作可推进，不标complete或blocked。

### 本轮构建已收尾

- 第一轮重打包因旧EXE被运行中进程占用而 WinError 5 失败；没有终止用户进程。
- 修复版新路径：`release/quota-resume-update/CodexAgentConsole-v2.2.0.exe`，13489177 bytes。
- SHA256：`4666707ae53d463e1a11b951b91cfbf9e93906e3cc6181fa686a84ce875bc75c`。
- 全套123项测试 PASS；新包observe函数字节码与当前源码一致；仅本轮测试进程及其子进程的GUI启动/关闭 PASS，exit 0。旁附 `.validation.json`。
- 仍未达成完整目标：原生Goal恢复/原子条件发送接口缺失，真实额度耗尽自然恢复周期 NOT_RUN。本轮保留active，未标complete。

## 2026-10-20 恢复目标后的第二轮接口审计

- 直接读取当前运行的 `OpenAI.Codex_26.1013.4894.0` app.asar（不是旧临时提取件），精确定位 `.vite/build/src-DsLVGakC.js` 和 `main-C4DYk222.js`。
- 当前源内 `thread/goal/set` 仅通过renderer manager.sendRequest可达；owner/follower注册方法表没有对应Goal handler。现有follower start-turn不改变Goal状态。未调用未知mutation或编辑官方App文件。
- native expectedTurnId条件属于interrupt/steer语义，不能转作start-turn原子条件；外部接口障碍未改变。
- 已请求用户提供一个真实额度中断且尚未手动继续的任务ID/名称，以完成真实恢复验证，不通过刻意耗尽额度制造测试。
- 当前为恢复后的第2轮审计；源码/新候选包维持上一轮验证版本，完整目标仍未实现。

## 2026-10-20 恢复目标后的第三轮阻塞审计

- 本轮实查新候选EXE SHA256与validation完全一致；本机监控enabled=true，恢复记录仍为空，无真实额度中断任务可执行验证。
- 实际运行进程仍为release上级目录旧候选（PID32116/20728）；修复版在quota-resume-update，未强制关闭或替换用户运行程序。
- 恢复后的连续三轮均确认同一外部接口阻碍：当前安装版本缺少可达的原生Goal恢复和条件发送接口；没有新的外部状态、用户提供的真实失败任务或已支持的安全入口。
- 已完成本轮可独立推进的修复、123项测试、构建、归档对应及GUI检查；不能用这些证据代替真实额度周期或缺失接口。完整目标按blocked处理，不标complete。
- 下一步需要真实中断任务样本以验证普通会话，及可达的Goal/条件发送接口（或用户明确调整约束）。本地已开启的监控保持原状，不额外建立轮询自动任务。

## 2026-10-27：截图模型容量错误支持

- 用户新增范围：`Selected model is at capacity. Please try a different model.` 也支持续跑。
- 真实只读证据：会话01a1769e-0de7-7151-8dc4-2fdc18a4f6ce历史失败轮次01a17d87-bfdd-75a0-a1c5-f4cd60ab1c07的error.codexErrorInfo=serverOverloaded，message与截图完全一致；最新轮次active/inProgress、Goal active，未插入新任务或打断。
- 现有工作树已有容量分类初步代码，增量修复其重复发送、历史最大计数永久耗尽、每轮重复写盘和异步新错误抢先回调计数丢失问题；未回滚其它未提交改动。
- 方案：3/6/12/24/30/30分钟退避，最多6次；按continuation_turn_id传递同一故障链计数，独立新请求归零；每个failed turn最多派发一次；未知结果不重试；旧版可能已发送的waiting记录转uncertain。
- 验证：11项新容量fixture、全套135项测试、py_compile和git diff --check均PASS；DeepSeek只读评审完成并自行复核，未采纳可能重启active任务的建议。
- 打包：2.2.2构建中，exec session46282；完成后须新包GUI smoke与SHA256记录。
- 边界：原生Goal循环恢复及原子条件发送的旧限制不在本次容量分类改动中解决，未声称完成旧全目标；真实服务端自然容量恢复完整周期NOT_RUN。

### 容量任务窗口验证与修复

- 首轮源码GUI smoke发现5列表格仍写入4列数据，容量列缺失导致错位；已在_update_items写入kind标签并增加legacy/容量行5列回归。
- 修复后源码GUI smoke PASS：容量/等待重试/预计恢复时刻均在对应列。全套136项测试、py_compile、diff-check PASS。
- 首轮2.2.2包完成且启动关闭PASS，但UI修复晚于首轮包，因此当前重新封装，exec session83118。最终交付须覆盖首轮2.2.2校验记录，不把旧包验证当新包验证。

### 2.2.2 最终本地交付

- `release/CodexAgentConsole-v2.2.2.exe`：13492281 bytes，SHA256 `ef08091c9fe1d1f95f98a0806213985392d01f78ad0509d77cdf836b03df9ef4`。
- 最终包容量core及UI函数字节码与当前源码一致；独立临时CODEX_HOME启动/关闭PASS（exit0），只关闭本次smoke子进程树。
- 136项测试PASS，容量窗口对应列PASS，最终diff-check PASS。未提交/推送/发布，未替换或终止用户旧控制台。
- 本次容量错误扩展已交付；真实过载自然恢复未刻意制造，原生Goal循环恢复及原子发送限制仍保留。
