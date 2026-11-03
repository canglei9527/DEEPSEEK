# ai-context 索引

本文件是 ARID 的目录→职责映射表，供 AI 导航。规范来源（single source of truth）用 **加粗** 标出。

## 模块边界

| 职责 | 规范来源/实现 | 公共接口 |
|---|---|---|
| 双模型角色连接（provider/网关/base_url/key 校验、脱敏、双角色预检、env 映射） | **`src/roles.ts`** | `makeRoleConnection` / `isConfigured` / `validatePair` / `redactedSummary` / `connectionFromEnv` / `buildRoleEnvMap` |
| 飞书远程控制核心（路由 `改/状态/停止`、白名单、单飞、超时、结果摘要） | **`src/feishu/bot.ts`** | `FeishuRemoteBot` / `FeishuMessage` / `FeishuTransport` / `TaskExecutor` |
| 回复分段 + @提及剥离 | **`src/feishu/text.ts`** | `splitReplyText` / `stripMentions` |
| 飞书 worker 装配 + 事件归一化 + 环境变量读取 | `src/feishu/worker-entry.ts` | `buildBot` / `normalizeEvent` / `extractText` / `allowedDirsFromEnv` |
| 官方 lark SDK 长连接 transport | `src/feishu/lark-transport.ts` | `createLarkTransport` / `startLarkWorker` / `ReplyClient` |
| 把飞书任务交给 DSH headless 跑（子进程 spawn） | `src/feishu/dsh-executor.ts` | `buildDshTaskExecutor` |
| 飞书 worker 主入口 | `src/feishu/worker-main.ts` | `main()` |
| 双模型 agent 预设（DSH 侧） | `~/.dsh/.agent-presets/arid-dualmodel/agent.cordis.yml` + `preset.yml` + `arid-dualmodel-ext5.cjs`（本地插件：注册 `arid-dual-model` settings 命名空间 + 提供动态 `subagent_executor` 工具，执行模型实时读 settings.yaml，重启不丢） | （roster 挂载） |
| 桌面启动器 | **`start-arid.bat`**（纯 ASCII/CRLF，禁非 ASCII——cmd 按 ANSI 码页解析，中文会闪退） | 桌面快捷方式「Aider双模型控制台」→ 本文件；`ARID_WORKDIR`/`ARID_PORT`/`ARID_WIN_W`/`ARID_WIN_H` 可覆盖；幂等（已在运行则只开窗口）；独立窗口 = Chrome/Edge 应用模式 + 隔离 profile（`%TEMP%\arid-web-%RANDOM%`），避免占用浏览器标签页和后台实例转发问题 |
| 配置 GUI | **`config-gui/server.mjs`** + `config-gui/index.html` | `configure.bat` 启动；支持模型、网关、API Key、飞书配置和一键默认还原；默认快照位于 `config-gui/defaults/` 且被 `.gitignore` 排除 |

## 关键决策记录（ADR 摘要）

1. **GUI 形态 = DSH 自带 web 工作台 + 双击 bat**（用户确认）。不重写 GUI 框架。
2. **双模型分工不套 aider 参数**，用 DSH 原生 plan-mode + goal 表达。等价语义，非逐行翻译。
3. **飞书 worker 是独立 Node 进程**，靠 `dsh --profile headless` 子进程复用 DSH 会话能力，
   与 aider-gui 的 `run_feishu_bot.py` 独立长连接形式一致。
4. **密钥纪律沿用**：api_key 经环境变量注入子进程，任何日志/摘要/命令不含明文 Key。

## 测试

- `test/roles.test.ts` — 角色连接契约（网关 base_url、脱敏、双角色预检、env 映射）
- `test/feishu.text.test.ts` — 分段/@提及纯函数
- `test/feishu.bot.test.ts` — 飞书路由/白名单/单飞/结果摘要
- `test/feishu.worker-entry.test.ts` — 事件归一化/content 解析/环境变量解析
- `test/feishu.lark-transport.test.ts` — lark reply transport（stub 注入）

## 未完成（已知）

- 飞书端到端需要真实 FEISHU_APP_ID/SECRET（`npx tsx src/feishu/worker-main.ts` 已接线，
  未在真实飞书环境验证长连接）。
- 双模型角色的 `dsh headless` 子进程如何**选择模型**取决于 profile 的 settings/credentials
  配置；worker 通过 ARID_*_MODEL/_BASE_URL/_API_KEY 环境变量传递角色事实，但 DSH headless
  读取哪个 env 名由 profile 的 llm 适配器决定（本仓库未接管，属 profile 配置范畴）。
- 双模型双密钥未做 DSH settings-UI 定制页（agent preset 无法注册 client 侧设置页 UI；
  执行模型配置走 `~/.dsh/settings.yaml` 的 `arid-dual-model.executor` 段，见 docs/dual-model.md；
  规划模型用 DSH 原生 Models 页 + credentials；飞书 worker 用 .env，二者分离）。
