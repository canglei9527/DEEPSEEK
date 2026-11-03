# 双模型（两个真实模型）使用方法

ARID 的"双模型分工" = **两个不同的模型端点**，通过
**主 agent（规划模型）+ 执行者子代理（执行模型）** 表达。

**省 tokens 的本质**：规划/审查（读上下文、出方案）用高等级模型，值得烧；
**反复执行+测试迭代（token 大头）走便宜的执行模型**——两条独立路由。

## 配置执行模型（持久，重启不丢）

执行模型由预设自带的本地插件 `arid-dualmodel-ext5.cjs` 提供，
**每次调用 `subagent_executor` 时实时读取** `~/.dsh/settings.yaml` 的
`arid-dual-model.executor` 段，改配置立即生效、重启保留：

```yaml
# ~/.dsh/settings.yaml
arid-dual-model:
  executor:
    provider: opencode-go      # 执行模型用的 provider（route 名）
    model: deepseek-v4-flash   # 执行模型
```

- 未配置时回退默认 `opencode-go / deepseek-v4-flash`。
- provider 必须是 DSH Models 页（`llm-pi-ai.providers`）里已注册的 route，
  且其 API key 已配置（credentials）。
- 规划模型 = 主 agent 的模型：工作台 composer 底部模型座位，
  或 `agent-default-model` 设置段。

## 工作流

1. 主 agent 用**规划模型**研究仓库、产出方案（plan mode）。
2. 方案批准后，主 agent 调 `subagent_executor` 工具，把落地编辑 + 跑测试委派给
   **执行模型**子代理。
3. 执行结果反复不达标 → 主 agent 回到规划角色重新出方案（升级求助）。

## 模型从哪来

**Models** 页（设置 → Models）：

- 官方 provider（pi-ai catalog，几十个）：填 API key 即激活。
- OpenAI 兼容网关：加自定义 provider（hand-declared route），
  填 `api: openai-completions` + `baseURL` + 模型列表 + key 引用，等价 settings.yaml：

```yaml
llm-pi-ai:
  providers:
    molifang:
      displayName: 魔方网关
      apiKeyEnv: MOLIFANG_API_KEY
      api: openai-completions
      baseURL: https://molifangapi.com/v1
      models:
        - id: gpt-5.6-sol
          contextWindow: 200000
```

## 当前实现状态与边界（诚实说明）

| 项 | 状态 |
|---|---|
| 规划模型选择 | ✅ 持久化（agent-default-model） |
| 思考等级（规划） | ✅ 持久化 |
| 执行模型选择 | ✅ 持久化 + 生效（`arid-dual-model.executor` 段，本地插件实时读取） |
| 子代理真实用执行模型 | ✅（每次调用读配置，改完立即生效） |
| 重启后自动恢复 | ✅ 设置段在 settings.yaml、插件随 preset 挂载，重启不丢 |

> **GUI 设置页的诚实说明**：DSH 的设置页（设置 → 插件）只渲染 host 组合注册的
> 命名空间卡片；agent preset 无法注册 client 侧 UI。因此没有「双模型」GUI 设置页，
> 配置请直接编辑 `~/.dsh/settings.yaml`（如上）。之前文档声称的"设置页由动态插件提供"
> 不可靠——动态插件重启即失，已废弃该说法。

## 验证两个模型真的分工

发个任务，观察：主 agent 输出走**规划模型**；`subagent_executor` 工具调用卡走**执行模型**
（子代理 request/header 的 provider/model 来自 `arid-dual-model.executor`）。
