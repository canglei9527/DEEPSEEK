# Codex Agent Console

一个非官方的 Codex Windows 桌面控制台，用于管理主模型、子代理默认值和双模型协调策略，并查看本机 Token 与缓存命中统计。

当前版本：`2.2.2`

> 本项目是社区工具，不是 OpenAI 官方产品。它只修改 Codex 官方支持的本地配置项。

## 下载

从 [GitHub Releases](https://github.com/canglei9527/codex-agent-console/releases/latest) 下载最新的 `CodexAgentConsole-v*.exe`，双击即可运行。目标电脑不需要安装 Python，但需要已经安装并使用过 Codex。

## 功能

- 主模型：`model`、`model_reasoning_effort`
- 子代理：`agents.default_subagent_model`、`agents.default_subagent_reasoning_effort`
- 模式：`agents.enabled = true|false`
- 双模型协作：启用时写入全局 `~/.codex/AGENTS.md`（非空 `AGENTS.override.md` 优先）。高阶子模型负责制定计划、拆解步骤、评审与关键决策；主模型按计划动手实现并验证，失败两次或遇到架构取舍时才回头问子模型
- 规划子模型：每次派发显式传入 GUI 选择的模型、思考级别和 `fork_turns = "none"`，避免完整历史分叉继承主模型；保存时也在 `~/.codex/agents/` 写入受控代理文件
- 完成优先：变更、修复和构建任务必须用工具推进到可验证结果，不能用“应该如何做”的建议或未来计划替代执行；子模型给出的计划必须具体到文件与验收点，主模型负责落地和收尾
- 模式切换：“双模型协作 / 单代理”两个按钮，右侧字段随模式启用或禁用；“保存并应用”写入配置并重载 Desktop 后台，“仅保存”只写配置
- 并发：`agents.max_concurrent_threads_per_session`
- 统计：主任务与规划子模型分别显示输入、缓存输入、输出、推理输出、总 Token、缓存命中率，以及会话实际使用的模型/思考级别；“今天”按本机当地时间 00:00 起算，系统 guardian 后台会话不混入子模型统计
- API 诊断：可测试主模型、子代理或两者，支持 1/3/5 次采样与 10–600 秒超时
- 诊断指标：连通成功率、首响应、API 总耗时、CLI 收尾耗时、输入/缓存/输出 Token 和 Tokens/s
- 错误定位：区分 401 认证失败、404 模型不可用、429 请求受限、超时、网络错误和其他 CLI 故障，并保留每次请求的完整错误详情
- 自定义 API：持久化保存多个 OpenAI 兼容接口，可勾选多个接口顺序测速
- 接口类型：支持 Chat Completions、Responses API、Legacy Completions 和 Claude Messages（Anthropic），按类型生成请求体并解析流式事件
- 基础 URL：只需填写例如 `https://api.example.com/v1`，自动补全具体接口路径；可从 `/v1/models` 自动获取模型
- 自定义测速：比较成功率、平均首响应、平均总耗时、耗时波动、Tokens/s 和缓存命中率，并标出最快与最稳定接口
- API Key 安全：支持 Bearer、`x-api-key` 和无鉴权；Key 使用 Windows DPAPI 加密，不明文写入配置或显示在界面中
- 接入 DSH：内置本机 OpenAI 兼容网关，可把 Codex 模型（如 `gpt-6-luna` / `max`）作为其它 agent 运行时的主模型，工具仍由调用方执行
- 接入 Codex：让 Codex 保留主模型，把 DeepSeek 作为常驻子模型（MCP 工具 `ask_deepseek`），一项目一会话，负责 UI/审美、架构、复杂方案审阅，必要时直接改文件

配置保存前会备份为 `~/.codex/config.toml.codex-agent-console.bak`。控制台只增删全局 `AGENTS.md` 或 `AGENTS.override.md` 中自己带标记的协调策略，不会覆盖已有的用户规则；旧版本写入 `developer_instructions` 的标记会自动迁移清除。点击“保存并应用”后，程序只重载经过路径、命令行和父进程校验的 Desktop `app-server`，不会关闭 Desktop 窗口；运行中的任务会中断，已有任务不会热切换，新任务会使用新设置。未检测到 Desktop 时，设置会在下次启动或新建任务时加载。

## 模型容量不足自动续跑（2.2.2）

出现 `Selected model is at capacity. Please try a different model.`（结构化 `serverOverloaded`）后，也可使用“额度续跑”窗口的“开启自动续跑”。截图中的真实错误字段已在本机会话快照中核实。当前仍在运行的任务不会被插入额外续跑。

沿用原模型，首次等待3分钟；如果续跑产生的新轮次再次因容量失败，依次等待6、12、24分钟，此后每次最多等待30分钟。同一连续故障最多发送6次；不查询订阅重置时间、不自动切换模型。同一失败轮次只发送一次，必须确认新轮次；发送结果不确定时停止自动重发。手动停止、新指令、手动暂停目标或“取消本次”会阻止恢复。等待期间阻塞休眠，不反复写相同记录。

新版程序：`release/CodexAgentConsole-v2.2.2.exe`。运行时保持控制台及Codex桌面端开启，打开“额度续跑”并启用监控；可以最小化。更新时自行正常关闭旧控制台，再启动新版，避免单实例监控锁仍由旧程序持有。监控设置和取消记录延续保存。

验证：136项测试通过，包含11项容量失败回放、指数退避、连续上限、发送去重、手动停止保护、旧版记录迁移及等待不重复写盘测试。截图任务已在真实快照中读到 `serverOverloaded`，但当时已继续运行；未为测试打断任务或制造真实服务过载。桌面发送接口的最后瞬间竞态和原生Goal循环恢复仍受已有接口限制。

## 额度续跑（2.2.0）

历史2.2.0候选：`release/quota-resume-update/CodexAgentConsole-v2.2.0.exe`。最新容量续跑版本见上节2.2.2。

工具栏“额度续跑”可启用监控，默认关闭，保存后在控制台下次启动时继续监听。控制台和 Codex 桌面端都需要保持运行；可以最小化控制台，关闭主窗口会停止监控。支持 Windows 当前桌面本机 IPC 协议版本 11，不兼容时停止恢复。

只接受最新执行轮次中结构化的订阅额度失败。手动中断、目标暂停、新指令、完成、归档及“取消本次”会阻止该次恢复。等待服务端报告的重置时刻后，重新读取额度权限和订阅窗口，再复核同一桌面会话，发送一次续跑。未知发送结果不自动重试，不消费重置券、不购买额度。记录保存在 `~/.codex/codex-agent-console-resume.json`。

限制：当前桌面 owner 接口不能恢复原生 Goal 的 `usageLimited` 状态，该情况显示“暂不可恢复”，需在 Codex 中恢复目标。普通任务通过原会话发送续跑；桌面接口没有原子条件发送能力，双重实时复核缩小了人工操作与发送之间的竞态，但不能宣称竞态不存在。尚未用真实额度耗尽和自然重置做完整实测。

验证：123 项既有及新增测试通过（包含 Goal/乐观轮次/运行状态先到达保护）；真实只读桌面接入与账户额度查询通过，GUI 打开/重开/关闭通过。一次20秒只读监听测得进程 CPU 时间0.047秒，只代表该样本。

## API 诊断

点击主窗口的“API 诊断”打开独立诊断窗口。测试会通过本机 Codex CLI 和现有认证发起真实、只读、禁用子代理的最小请求，不读取 API Key。每次测试都会产生 Token，并进入正常的本机会话统计；默认只执行 1 次。

“API 总耗时”取自 Codex 的 `turn.completed` 事件，“CLI 收尾”单独记录请求完成后本地进程退出所需时间。Codex CLI 的 JSONL 输出不是逐 Token 流；窗口会尽可能从会话 `token_count` 事件刷新累计输出与实时吞吐，最终 Token 数以 `turn.completed.usage` 为准。

CLI 调用方式依据 [Codex CLI reference](https://developers.openai.com/codex/cli/reference/)。

## 官方订阅与自定义 API

Codex 官方订阅使用 ChatGPT 登录，不是可粘贴到 HTTP 接口中的 API Key。测试官方订阅时，点击主窗口的“API 诊断”，或在“自定义 API”窗口点击“测试 Codex 官方订阅”；程序会复用本机 Codex CLI 的现有登录状态。官方登录方式见 [Codex authentication](https://developers.openai.com/codex/auth/)。

“自定义 API”用于独立购买的 OpenAI Platform API Key、Anthropic API Key、第三方兼容 API 或自建服务。点击“新增 API”，填写名称、基础 URL、接口类型和鉴权方式；点击“获取模型”即可从兼容的 `/v1/models` 接口读取模型，再保存并测速。例如 OpenAI 兼容接口基础 URL 可以填写 `https://api.openai.com/v1`，Claude 原生接口可以填写 `https://api.anthropic.com/v1`。程序会根据所选类型使用 `/chat/completions`、`/responses`、`/completions` 或 Anthropic `/messages`；选择 Claude Messages 时默认使用 `x-api-key` 和 `anthropic-version: 2023-06-01`。其 API 用量和 Codex/ChatGPT 订阅分开计费。

默认执行 3 次以计算稳定性。测试按 API 顺序逐个执行，避免并发抢占本机带宽影响比较；每次都会产生对应服务的实际 Token 消耗。远程接口必须使用 HTTPS，只有 localhost 可使用 HTTP。

勾选“缓存命中测试”后，每个 API 每轮会连续请求两次相同的约 2K Token 输入：第一条用于预热，第二条用于读取缓存命中率。明细会保留预热和检测结果，对比表只汇总检测请求。Claude Messages 会在缓存测试请求中附带 `cache_control: {"type":"ephemeral"}` 和提示词缓存 Beta 头；仅当服务端在 `usage` 中返回输入 Token 和缓存输入 Token 时显示命中率；没有这些字段的兼容接口显示 `--`。

## 接入 DeepSeek Harness（本地网关）

主窗口右上角的“接入 DSH”会启动一个只监听 `127.0.0.1` 的 OpenAI 兼容网关：

- `GET /v1/models`、`POST /v1/chat/completions`，支持 SSE 流式
- 每次模型调用 = 一次 `codex exec`：调用方的系统提示、对话、工具结果和工具清单会被渲染成一条决策提示，Codex 返回“最终回答”或“工具调用”，工具由调用方自己执行
- 可设置并发上限、超时和访问令牌；网关只绑定回环地址，工作目录固定，Codex 以只读沙箱运行

在 DSH 的 `~/.dsh/profiles/<profile>/cordis.patch.yml` 追加一条路由（窗口里可一键复制）：

```yaml
- id: llm-codex-local
  name: '@deepseek-ai/dsh-llm-pi-ai'
  config:
    providers:
      codex-local:
        displayName: Codex 本机网关
        api: openai-completions
        baseURL: http://127.0.0.1:8765/v1
        models:
          - id: gpt-6-luna@max
            name: gpt-6-luna / max
            contextWindow: 272000
```

窗口里的“写入 DSH 配置”会在备份并校验 YAML 后自动追加这条路由；也可以点“复制配置”手动粘贴。填了访问令牌时，还要点“写入环境变量”把 `CODEX_GATEWAY_TOKEN` 写进用户环境变量，并完全重启 DSH——凭据解析不到，模型不会出现在选择器里。勾选“同时把 DSH 子代理设为 DeepSeek”时，片段还会写入 `tool-subagent` 与 `tool-subagent-fork` 的 `agentOptions`，让 DSH 自己派出的子代理走 DeepSeek，而不是继承 luna。重启 DSH 后，在模型选择里选 `gpt-6-luna@max`。每次模型调用都会启动一个 Codex 进程，单次延迟通常在 20–60 秒，用量计入你的 Codex 订阅或额度；响应慢时优先降低思考级别或换更小的模型。

## 接入 Codex（luna 主模型 + DeepSeek 常驻子模型）

适合“主模型额度便宜但审美一般、另一个模型更聪明但要按次调用”的组合：

- Codex 里的模型继续当主模型，持有上下文、执行和验证；
- 遇到 UI/布局/配色/交互/文案、架构与接口取舍、同一问题连续失败两次、或跨三个文件以上的改动前，主模型调用 MCP 工具 `ask_deepseek`；
- 子模型是**一个持久会话**（`dsh --profile headless --session-id …`），按项目各存一个，记得住之前的评审结论；它有自己的文件和命令工具，所以自己读仓库，必要时直接改；
- 主模型只传“具体问题 + 文件路径/行号”，不把整段上下文倒给子模型。

在“接入 Codex”窗口里：

1. 填 DeepSeek 端点、模型、API Key（DPAPI 加密保存），点“保存配置”；“测试端点”只验证接口可用。思考级别提供 `low` / `high` / `max`（`deepseek-v4.1-flash` 会拒绝 `medium` 和 `xhigh`），默认 `high`。
2. 点“写入 MCP 配置”，会在 `~/.codex/config.toml` 追加 `[mcp_servers.deepseek_advisor]`（写前备份并校验 TOML）。
3. 点“写入 AGENTS 规则”，会在 `~/.codex/AGENTS.md` 追加一段带标记的“何时必须咨询”的规则（写前备份，不会覆盖你自己写的规则）。
4. 重启 Codex，新会话即可看到 `ask_deepseek` 工具；用“试问一句”可以在控制台里先验证一次真实调用。

每次咨询会启动一个 `dsh` 进程（约 10–20 秒），失败时主模型会继续干活，并在最终回复里说明“子模型不可用”。同一个项目想从头再来，在“顾问会话”里选中它点“重置所选”。

## 系统要求

- Windows 10 或 Windows 11
- 已安装 Codex，并存在可读写的 `~/.codex` 目录
- 从源码运行或构建时需要 Python 3.11+

## 运行

双击 `start.bat`，或运行：

```powershell
py -3.11 codex_agent_console.py
```

## 打包

```powershell
powershell -ExecutionPolicy Bypass -File .\build_windows.ps1
```

生成的 `dist\CodexAgentConsole.exe` 可复制到其他已安装 Codex 的 Windows 电脑使用，不要求目标电脑安装 Python。

## 隐私与安全

统计只读取 `~/.codex/sessions/**/*.jsonl` 中的会话元数据和 `token_count` 事件，不读取或展示对话正文、API Key 或认证文件。详细说明见 [SECURITY.md](SECURITY.md)。

## 测试

```powershell
py -3.11 -m unittest -v
```

## 许可证

[MIT](LICENSE)
