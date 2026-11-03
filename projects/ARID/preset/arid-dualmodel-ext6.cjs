/*
 * ARID 双模型扩展插件（固化版 v6 — 最终）
 *
 * 解决两个"一直不能用"的根因：
 * 1. 「双模型」设置之前靠动态插件提供，dsh web 重启即失。
 *    本插件随 agent preset 持久挂载：注册 `arid-dual-model` settings
 *    命名空间（agent 平面，subagent_executor 工具可读），重启后依然在。
 * 2. 执行模型之前硬编码在 agent.cordis.yml 里，改设置不生效。
 *    本插件提供的 subagent_executor 工具每次调用时实时读取
 *    `arid-dual-model.executor`（settings 段），改配置立即生效。
 *
 * 零依赖：不 require 任何 npm 包，构造的是 tools 注册表原生 ToolDefinition。
 * 生命周期全部归入 apply 的 fiber（ctx.effect），随 preset 挂载/卸载。
 * 服务用 ctx.inject 等待（预设挂载时 host 服务可能未就绪）。
 */

const DEFAULT_EXECUTOR = { provider: 'opencode-go', model: 'deepseek-v4-flash' }
const EXECUTOR_PERSONA = `你是执行者角色（双模型分工的低等级执行模型）。读取完整上下文，
严格按已批准的规划方案落地代码编辑，真实运行测试验证（工作目录下
test_*，最多 20 个、单测 30s 超时），返回通过/失败与尾部输出。
不要重新做规划决策，只执行与验证。`

/** 从 settings 读取执行模型；无配置回退默认。 */
function executorFromSettings(settings) {
  const cfg = settings === undefined ? undefined : settings.get('arid-dual-model')
  const ex = cfg && typeof cfg === 'object' ? cfg.executor : undefined
  if (!ex || typeof ex !== 'object') return { ...DEFAULT_EXECUTOR }
  return {
    provider: typeof ex.provider === 'string' && ex.provider ? ex.provider : DEFAULT_EXECUTOR.provider,
    model: typeof ex.model === 'string' && ex.model ? ex.model : DEFAULT_EXECUTOR.model,
  }
}

/** 零依赖 schemastery 兼容 schema：可调用 + toJSON（describe 用）。 */
function makeDualModelSchema() {
  const schema = (value) => (value === undefined ? {} : value)
  schema.toJSON = () => ({
    uid: 1,
    refs: {
      1: { type: 'object', meta: { default: {} }, dict: { executor: 2 } },
      2: { type: 'object', meta: { default: {} }, dict: { provider: 3, model: 4 } },
      3: { type: 'string', meta: {} },
      4: { type: 'string', meta: {} },
    },
  })
  return schema
}

/** 子代理 run 的 settle：completed 才算成功，其他 stopReason 报错；释放 run。 */
async function settleForegroundRun(run) {
  let result
  try {
    result = await run.result
  } catch (error) {
    try { await run.dispose() } catch { /* 释放失败不掩盖原错误 */ }
    throw error
  }
  const error = stopReasonError(result)
  try {
    await run.dispose()
  } catch (disposeError) {
    if (error === undefined) throw disposeError
    throw new Error(`${error}\ndispose failed: ${String(disposeError)}`)
  }
  if (error !== undefined) throw new Error(error)
  return {
    kind: 'foreground',
    runId: run.id,
    output: result.output,
  }
}

function stopReasonError(result) {
  switch (result.stopReason) {
    case 'completed': return undefined
    case 'aborted': return 'subagent run was cancelled'
    case 'error': return 'subagent run failed'
    case 'max-tokens': return 'subagent run hit its token limit before finishing'
    case 'refusal': return 'subagent declined the task'
    default: return `subagent run ended abnormally (${String(result.stopReason)})`
  }
}

/** 输出块 → 纯文本。 */
function outputValueText(values) {
  return values
    .filter((value) => typeof value === 'object' && value !== null && !Array.isArray(value) && value.type === 'text' && typeof value.text === 'string')
    .map((value) => value.text)
    .join('')
}

module.exports = {
  name: 'dsh-arid-dualmodel-ext',
  inject: ['tools', 'subagents', 'settings'],
  async apply(ctx) {
    // 1) 注册持久 settings 命名空间：工具可读，重启后依然在
    try {
      ctx.effect(() => ctx.settings.register('arid-dual-model', makeDualModelSchema(), {
        base: { executor: { ...DEFAULT_EXECUTOR } },
      }))
    } catch (error) {
      ctx.logger?.warn?.('arid-dualmodel: settings namespace register failed: %s', String(error))
    }

    // 2) 注册 subagent_executor 工具：每次调用实时读取执行模型配置
    ctx.effect(() => ctx.tools.register({
      name: 'subagent_executor',
      description: 'Delegate a self-contained task to a subagent (a separate agent that works in its own context) to offload focused, independent work — the EXECUTOR of the ARID dual-model split, running on the configured executor model. It does not consume this conversation\'s context. The executor returns its result, not its intermediate steps. Give it a complete, standalone prompt: it does not see this conversation. This tool runs in the background by default, immediately returns a durable subagent id, and keeps the child conversation available for later turns. When that run settles, the runtime sends the parent a notice containing its outcome and any final assistant message; `send_message` starts a later turn in the same child conversation. Set `run_in_background: false` only when your next action depends on receiving the result.',
      parameters: {
        type: 'object',
        properties: {
          description: {
            type: 'string',
            description: 'A short (3-5 word) description of the delegated task, for display.',
          },
          prompt: {
            type: 'string',
            description: 'The complete, self-contained task for the executor subagent. It does not share this conversation\'s context, so include everything it needs.',
          },
          run_in_background: {
            type: 'boolean',
            description: 'Whether to run in the background and return a durable subagent id immediately. Defaults to true. Set false to wait for the result when your next action depends on it.',
          },
        },
        required: ['description', 'prompt'],
      },
      output: {
        schema: { type: 'object' },
        render: (_args, value) => [{
          type: 'text',
          text: value && value.kind === 'continuable'
            ? `started subagent ${value.subagentId}`
            : value && value.kind === 'foreground'
              ? outputValueText(value.output || [])
              : String(value),
        }],
      },
      isConcurrencySafe: () => true,
      async execute(args, exec) {
        const parent = exec.agent
        if (!parent) throw new Error('subagent_executor requires a calling agent (exec.agent was undefined)')

        // 实时读取执行模型配置（settings → 默认）
        const executor = executorFromSettings(ctx.get('settings'))
        const request = {
          label: args.description,
          prompt: [{ type: 'text', text: args.prompt }],
          parent,
          agentOptions: { provider: executor.provider, model: executor.model },
          persona: EXECUTOR_PERSONA,
          maxDepth: 3,
        }

        if (args.run_in_background !== false) {
          const { childId } = await ctx.subagents.startContinuable({
            provider: 'spawn',
            label: args.description,
            request,
            signal: exec.signal,
          })
          return { kind: 'continuable', subagentId: childId }
        }
        const run = await ctx.subagents.start('spawn', { ...request, signal: exec.signal })
        return settleForegroundRun(run)
      },
    }))
  },
}
