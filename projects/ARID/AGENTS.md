# AGENTS.md — ARID 项目协作指引

ARID 是把 aider-gui 的**核心思想**在 DeepSeek Harness（DSH）上重实现的项目。
目标不是再造一个 GUI 框架，而是把三个核心思想变成 DSH 扩展 + 一个双击启动器：

1. **双模型分工 + 升级流程**（规划/审查 → 执行 → 验证 → 失败升级重新规划）
2. **多平台模型/密钥管理**（含 OpenAI 兼容网关 base_url）
3. **飞书远程控制**（机器人驱动 + 白名单 + 单飞）

## 关键事实（先读，避免方向性错误）

- **DSH 不是 GUI 框架，也不是 aider 封装。** 它是 TypeScript/Node.js + Cordis 插件架构的
  agent 运行时。它**自带** web 工作台 GUI（`dsh web`），已覆盖会话历史、文件树、
  模型选择、设置面板——这就是"窗口 GUI"的落地形态。
- **不要重写 DSH 的 web GUI，不要改 shipped preset，不要动 host composition。**
  扩展走 agent preset（本会话可挂载）+ 独立 worker + 双击启动器。
- **双模型分工用 DSH 原生语义表达**：规划 = 主 agent（规划模型）+ plan-mode；
  执行 = `subagent_executor` 工具委派给执行者子代理（`agentOptions.model` 指定执行模型）；
  升级求助 = goal 的 blocked/resume。**不依赖 aider 的 --architect/--editor-model。**
  （两个真实模型的分工与配置见 `docs/dual-model.md`）
- **密钥纪律**：任何输出/日志/命令绝不包含明文 Key（见 src/roles.ts redactedSummary）。

## 目录职责（详细映射见 ai-context/INDEX.md）

| 路径 | 职责 |
|---|---|
| `src/roles.ts` | 双模型角色连接模型（provider/网关/base_url/key 校验、脱敏、env 映射） |
| `src/feishu/bot.ts` | 飞书远程控制核心（路由/白名单/单飞，纯逻辑可测） |
| `src/feishu/text.ts` | 回复分段 + @提及剥离（纯函数） |
| `src/feishu/worker-entry.ts` | 飞书 worker 装配 + 事件归一化 + 环境变量读取 |
| `src/feishu/lark-transport.ts` | 官方 lark SDK 长连接 transport（WSClient + EventDispatcher + reply） |
| `src/feishu/dsh-executor.ts` | 把飞书任务交给 `dsh --profile headless` 跑 |
| `src/feishu/worker-main.ts` | 飞书 worker 主入口（真实 lark 长连接） |
| `test/` | 契约测试（node:test + tsx） |
| `start-arid.bat` | 双击启动 DSH web 工作台 |
| `.env.example` | 飞书 worker 环境变量示例 |
| `docs/dual-model.md` | 双模型（两个真实模型）使用方法与配置指南 |
| `~/.dsh/.agent-presets/arid-dualmodel/` | 双模型 agent 预设（不在本仓库内） |

## 命令

```bash
npm test        # tsx --test（47 契约测试）
npm run typecheck  # tsc --noEmit
npm install     # 安装依赖
npx tsx src/feishu/worker-main.ts   # 飞书长连接 worker（需 .env）
```

## 约定

- TypeScript，ESM（`"type": "module"`），NodeNext 解析——import 用 `.js` 后缀。
- 纯逻辑模块不 import DSH 运行时；飞书 worker 是独立进程，靠子进程 spawn 复用 DSH。
- 业务决策的规范来源：角色连接 → `src/roles.ts`；飞书路由 → `src/feishu/bot.ts`。
- 修改 agent 预设后必须 `arid_preset_validate`（或 standingKeyFor）确认能挂载。
