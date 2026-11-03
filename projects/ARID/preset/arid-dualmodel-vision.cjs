/*
 * ARID 双模型扩展插件 — 审查者视觉（v1）
 *
 * 为「规划/审查者」角色新增视觉审查能力：`subagent_vision` 工具，
 * 委派一个视觉审查子代理（MiMo-V2.5 多模态视觉版）用 read_image 工具
 * 读取指定图片并返回视觉审查报告，供基于图像内容的验收检查使用。
 *
 * 风格与 arid-dualmodel-ext6.cjs 保持一致：
 * - 零依赖，构造 tools 注册表原生 ToolDefinition；
 * - 生命周期全部归入 apply 的 fiber（ctx.effect）；
 * - 服务用 ctx.inject 等待。
 *
 * 视觉模型配置实时从 settings 的 `arid-dual-model.vision` 段读取
 * （默认 opencode-go / mimo-v2.5），改配置立即生效。
 */

const DEFAULT_VISION = { provider: 'opencode-go', model: 'mimo-v2.5' }
const VISION_PERSONA = `你是视觉审查专家（MiMo-V2.5 多模态）。
用 read_image 工具读取指定图片，基于视觉内容输出客观审查报告：
图像内容描述、与审查要求相关的发现、问题点、结论（通过/不通过）。
不要编造图片中不存在的内容。`
const DEFAULT_VISION_PROMPT = 'Describe this image in detail and review it against the acceptance criteria implied by context; report findings, issues, and a verdict.'

/** 从 settings 读取视觉模型配置；无配置回退默认。 */
function visionFromSettings(settings) {
  const cfg = settings === undefined ? undefined : settings.get('arid-dual-model')
  const vis = cfg && typeof cfg === 'object' ? cfg.vision : undefined
  if (!vis || typeof vis !== 'object') return { ...DEFAULT_VISION }
  return {
    provider: typeof vis.provider === 'string' && vis.provider ? vis.provider : DEFAULT_VISION.provider,
    model: typeof vis.model === 'string' && vis.model ? vis.model : DEFAULT_VISION.model,
  }
}

/** 零依赖 schemastery 兼容 schema：可调用 + toJSON（describe 用）。 */
function makeVisionSchema() {
  const schema = (value) => (value === undefined ? {} : value)
  schema.toJSON = () => ({
    uid: 1,
    refs: {
      1: { type: 'object', meta: { default: {} }, dict: { vision: 2 } },
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
  name: 'dsh-arid-dualmodel-vision',
  inject: ['tools', 'subagents', 'settings'],
  async apply(ctx) {
    // 1) 注册持久 settings 命名空间 `arid-dual-model.vision`：工具可读
    try {
      ctx.effect(() => ctx.settings.register('arid-dual-model', makeVisionSchema(), {
        base: { vision: { ...DEFAULT_VISION } },
      }))
    } catch (error) {
      ctx.logger?.warn?.('arid-dualmodel-vision: settings namespace register failed: %s', String(error))
    }

    // 2) 注册 subagent_vision 工具：每次调用实时读取视觉模型配置
    ctx.effect(() => ctx.tools.register({
      name: 'subagent_vision',
      description: `Spawn a VISION review subagent (MiMo-V2.5 multimodal) that reads an image via the read_image tool and returns a visual review. Used by the planner/reviewer role for image-based acceptance checks. It does not consume this conversation's context. The vision subagent returns its result, not its intermediate steps. Give it a complete, standalone prompt: it does not share this conversation's context, so include everything it needs — especially the absolute path to the image. This tool runs in the background by default, immediately returns a durable subagent id, and keeps the child conversation available for later turns. When that run settles, the runtime sends the parent a notice containing its outcome and any final assistant message; send_message starts a later turn in the same child conversation. Set run_in_background: false only when your next action depends on receiving the result.`,
      parameters: {
        type: 'object',
        properties: {
          image_path: {
            type: 'string',
            description: 'Absolute path to the image file for the vision subagent to read via the read_image tool.',
          },
          prompt: {
            type: 'string',
            description: 'The review requirements / acceptance criteria for the image inspection. If omitted, a default English prompt is used asking for a detailed description plus findings, issues, and a verdict.',
          },
          run_in_background: {
            type: 'boolean',
            description: 'Whether to run in the background and return a durable subagent id immediately. Defaults to true. Set false to wait for the result when your next action depends on it.',
          },
        },
        required: ['image_path'],
      },
      output: {
        schema: { type: 'object' },
        render: (_args, value) => [{
          type: 'text',
          text: value && value.kind === 'continuable'
            ? `started vision subagent ${value.subagentId}`
            : value && value.kind === 'foreground'
              ? outputValueText(value.output || [])
              : String(value),
        }],
      },
      isConcurrencySafe: () => true,
      async execute(args, exec) {
        const parent = exec.agent
        if (!parent) throw new Error('subagent_vision requires a calling agent (exec.agent was undefined)')

        // 实时读取视觉模型配置（settings → 默认）
        const vision = visionFromSettings(ctx.get('settings'))
        const promptText = typeof args.prompt === 'string' && args.prompt.trim()
          ? args.prompt.trim()
          : DEFAULT_VISION_PROMPT

        const reviewPrompt =
`[VISION REVIEW REQUEST]
Image to inspect (absolute path): ${args.image_path}

Review requirements:
${promptText}

Instructions to the vision subagent:
1. Call the read_image tool with image_path exactly as given above to load the image into your multimodal context.
2. Carefully inspect the visual content of the loaded image.
3. Produce an objective visual review report with these sections: image content description, findings relevant to the review requirements, issues/problems, and a verdict (PASS/FAIL).
4. Base every statement strictly on what is actually visible in the image. Do not fabricate content that is not present.
Return the complete report as your final output.`

        const request = {
          label: args.prompt ? `vision: ${String(args.prompt).slice(0, 60)}` : `vision: ${args.image_path}`,
          prompt: [{ type: 'text', text: reviewPrompt }],
          parent,
          agentOptions: { provider: vision.provider, model: vision.model },
          persona: VISION_PERSONA,
          maxDepth: 2,
        }

        if (args.run_in_background !== false) {
          const { childId } = await ctx.subagents.startContinuable({
            provider: 'spawn',
            label: args.prompt ? `vision: ${String(args.prompt).slice(0, 60)}` : `vision: ${args.image_path}`,
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
