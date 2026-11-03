# ARID — aider-gui 核心思想在 DeepSeek Harness 上的重实现

把原 `aider-gui`（Flet + aider）的**三个核心思想**迁移到 [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness)（TypeScript + Cordis 插件架构），
不再修补充满问题的 Flet GUI，而是复用 DSH 自带的 web 工作台作为"窗口 GUI"。

## 为什么这样改

| 旧（aider-gui） | 新（ARID） |
|---|---|
| Flet 桌面窗口，一堆渲染/编码问题 | DSH 自带 web 工作台（`dsh web`），双击 `.bat` 启动 |
| aider `--architect`/`--editor-model` 双模型 | DSH 原生 **plan-mode**（规划/审查）+ 主 agent（执行）+ **goal**（升级求助） |
| 6 平台 + OpenAI 兼容网关密钥管理 | DSH 自带 settings-models + credentials（多平台 + base_url） |
| `feishu_bot.py` 独立长连接 | `src/feishu/` 独立 Node worker，复用 `dsh headless` 跑任务 |

## 三个保留的核心思想

1. **双模型分工 + 升级流程**：规划/审查（高等级，只读出方案）→ 执行（低等级，落地编辑）→ 验证 → 反复失败则升级重新规划。
   落地为 `arid-dualmodel` agent 预设（已挂载验证通过）。
2. **多平台模型/密钥管理**：`src/roles.ts` 提供 provider/网关 base_url/key 校验与脱敏；
   运行时用 DSH 自带 settings-models 管理。
3. **飞书远程控制**：`src/feishu/` 实现 `改 <目录> <任务>` / `状态` / `停止` 路由 + 白名单 + 单飞，
   语义与 `feishu_bot.py` 一致。

## 快速开始

### 1. 启动工作台（双击）

```bat
start-arid.bat
```

等价命令：`dsh web`（工作台地址 http://127.0.0.1:3080，端口占用自动顺延）。

### 2. 选择双模型预设

在工作台新建会话时选择预设 **「ARID 双模型模式」**（由 `arid-dualmodel` 提供）。
该预设的 agent 会按「规划 → 执行 → 验证 → 失败升级」工作。

### 3. 双模型（两个真实模型）怎么用

双模型 = **两个不同的模型端点**，不是同一个模型演两个角色：

| 角色 | 用的模型 | 在哪选 |
|---|---|---|
| **规划/审查**（高等级） | 主 agent 的模型 | composer 底部的**模型座位**，或 `agent-default-model` 设置 |
| **执行**（低等级） | 执行者子代理的模型 | `~/.dsh/settings.yaml` 的 `arid-dual-model.executor` 段（本地插件实时读取，改完立即生效） |

- 规划阶段：主 agent 用**规划模型**研究上下文、出方案。
- 执行阶段：主 agent 调 `subagent_executor` 工具，把「落地编辑 + 跑测试」委派给
  **执行模型**子代理。
- 要换执行模型：编辑 `~/.dsh/settings.yaml` 的 `arid-dual-model.executor` 段
  （provider/model），保存即生效，重启保留。默认 `opencode-go / deepseek-v4-flash`。

**注册新模型（含 OpenAI 兼容网关）**：DSH 的 Models 设置页（或 `~/.dsh/settings.yaml`
的 `llm-pi-ai:` 段）支持几十个 provider + 任意网关。加一个网关（如 molifang）：

```yaml
# ~/.dsh/settings.yaml —— 在 llm-pi-ai.providers 下加一个 hand-declared 网关 route
llm-pi-ai:
  providers:
    molifang:
      displayName: 魔方网关
      apiKeyEnv: MOLIFANG_API_KEY      # 密钥引用，实际值在 Models 页/credentials 存
      api: openai-completions
      baseURL: https://molifangapi.com/v1
      models:
        - id: gpt-5.6-sol
          contextWindow: 200000
    deepseek:                          # 执行模型用的 route（pi-ai 自带 catalog）
      apiKeyEnv: DEEPSEEK_API_KEY
```

然后在工作台 **Models 设置页**填对应 API key（写入 `~/.dsh/.credentials.yaml`，不落明文日志），
在模型座位选规划模型即可。详见 `docs/dual-model.md`。

### 4. 飞书远程控制（可选）

飞书侧准备（一次性）：创建企业自建应用 → 加机器人 → 开 `im:message` /
`im:message.receive` / `im:message.group_at_msg` 权限 → 事件订阅选**长连接** →
发布 → 拿 App ID / App Secret。

复制 `.env.example` 为 `.env` 并填写：

```dotenv
FEISHU_APP_ID=cli_xxx
FEISHU_APP_SECRET=xxx
ARID_FEISHU_ALLOWED_DIRS=E:\myrepo;E:\GLM5.2\hubway-smart-router
ARID_PLANNER_MODEL=gpt-5.6-sol
ARID_PLANNER_BASE_URL=https://your-gateway/v1
ARID_PLANNER_API_KEY=xxxx
ARID_EXECUTOR_MODEL=deepseek-chat
ARID_EXECUTOR_API_KEY=xxxx
```

然后：

```bash
npm install
npx tsx src/feishu/worker-main.ts   # 长连接模式，本地即可收消息，无需公网 IP
```

> transport 已接**官方 `@larksuiteoapi/node-sdk` 长连接**（WSClient + EventDispatcher），
> 消息路由/白名单/单飞在 `src/feishu/bot.ts`。完整变量见 `.env.example`。

## 开发

```bash
npm install        # 安装依赖
npm test           # 47 个契约测试
npm run typecheck  # tsc --noEmit
```

## 目录

见 `AGENTS.md` 与 `ai-context/INDEX.md`。

## License

MIT（沿用原 aider-gui 的 MIT 许可精神）。
