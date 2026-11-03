/**
 * router-core: reasoning-mode routing logic (zero dependencies).
 *
 * PORTED from dsh-routing-suite preset/router-standard/router-core.mjs
 * (v34 / v1.20.0 line, MIT). The measured parts — the classification
 * regexes, the band thresholds, the persona strings, the complexity
 * heuristic — are kept byte-identical so the port stays faithful and
 * testable. Only the tool-name vocabulary is remapped to ZCode's native
 * tool set.
 *
 * BEHAVIORAL REALITY (measured upstream, 21-point x n=2 on v4-pro): model
 * behavior along the react<->spec axis collapses into THREE stable regions,
 * not a continuum — spec [0, 0.15], a transition band [0.2, 0.45] (unstable
 * mix, avoid), and react [0.5, 1.0] (11 mode values behave identically).
 * The numeric interface therefore maps onto three behavior bands;
 * "continuous" tuning is an illusion at the model layer.
 *
 * FOURTH MODE — weak (internal routing): a weak-persona domain where the
 * model routes itself from the task. The optimal weak persona is
 * model-specific:
 *   - pro:   spec sentence + few-shot routing instruction
 *   - flash: neutral + explicit "classify then act" instruction
 *
 *   mode 0    -> pure spec  — plan-first, collective, read-first tools
 *   mode 0.3  -> mixed      — transition band (trap; only explicit opt-in)
 *   mode 1    -> pure react — doer, produce-verify-fix, test-suppressed
 *   mode W    -> weak       — internal routing (model decides per task)
 *
 * FIDELITY GAP (ZCode): upstream swaps the SYSTEM PROMPT persona, which is
 * what its measurements condition on. ZCode exposes no system-prompt hook,
 * so the persona strings below are preserved for fidelity and reference,
 * while `directiveFor()` carries the same conditioning as a near-field
 * working-style directive injected into the live request. This is a
 * tail-position approximation of a system-prompt swap, not an equivalent.
 */

export const MODE_SPEC = 0
export const MODE_MIXED = 0.3
export const MODE_REACT = 1
export const MODE_WEAK = 'weak'

/** Upstream persona strings — preserved verbatim for fidelity. */
export const SPEC_PERSONA = 'You are a helpful software engineer assistant.'

export const MIXED_PERSONA =
  'You are a helpful software engineer assistant.\n'
  + 'Work directly: prefer writing or editing code over describing plans. '
  + 'Verify your changes by reading and running them.'

export const REACT_PERSONA =
  'You are a hands-on software engineer who delivers working output fast.\n'
  + 'Work directly: write or edit code, then verify it by reading and running. '
  + 'Keep the loop tight — produce, verify, fix — and do not build test '
  + 'harnesses, scaffolding, or ceremony the user did not ask for. '
  + 'Finish with a usable deliverable and a short summary.'

/** Weak (internal-routing) personas — model-specific optimum.
 *  pro:   spec sentence + classify instruction — the few-shot variants and
 *         the recall/converge anchors HURT Pro.
 *  flash: neutral + classify + recall/converge/anti-runaway anchors.
 */
export const WEAK_PRO =
  'You are a helpful software engineer assistant.\n'
  + 'Before acting, decide the task type (build or fix) and adopt the matching '
  + 'style: build → hands-on production; fix → inspect-and-plan.'

export const WEAK_FLASH =
  'You are a helpful assistant.\n'
  + 'Before acting, decide the task type (build or fix) and adopt the matching '
  + 'style: build → hands-on production; fix → inspect-and-plan.\n'
  + 'Before acting, briefly review what you have already done in this session and continue from where you left off; do not repeat completed steps. Do not run environment checks (echo, whoami, uname, node --version, date) or exhaustive grep/glob scans.\n'
  + 'Think deeply first, then produce.'

/** Complexity heuristic: long or architecturally-worded tasks are COMPLEX.
 *  Simple tasks get fast-convergence guidance; complex tasks get deep
 *  exploration guidance (depth-adaptive, upstream v19). Regex kept identical. */
const COMPLEX_RE = /(重构|架构|全面|详细|设计|系统|优化|分析|survey|overview|architecture|refactor|comprehensive|detailed|design|system|optimize|analyze)/i

export function isComplexTask(text) {
  return typeof text === 'string' && (text.length > 120 || COMPLEX_RE.test(text))
}

/** True when the routed model id is a Flash-family model. */
export function isFlashModel(modelId) {
  return typeof modelId === 'string' && /flash/i.test(modelId)
}

/** Quantize a mode to one of the four measured behavior bands. */
export function bandOf(mode) {
  if (mode === 'weak') return 'weak'
  const m = clamp01(mode)
  if (m < 0.2) return 'spec' // measured stable spec region (0..0.15)
  if (m < 0.5) return 'transition' // measured unstable band — avoid
  return 'react' // measured stable react region (0.5..1 behave alike)
}

/** Persona for a mode; weak picks the model-specific internal-routing text. */
export function personaFor(mode, modelId) {
  switch (bandOf(mode)) {
    case 'spec': return SPEC_PERSONA
    case 'transition': return MIXED_PERSONA
    case 'weak': return isFlashModel(modelId) ? WEAK_FLASH : WEAK_PRO
    default: return REACT_PERSONA
  }
}

/**
 * First-turn core tools per band. Upstream remaps the toolkit to the band's
 * measured RL attractor; ZCode exposes one fixed catalog and cannot gate
 * tools, so this is INFORMATIONAL ONLY — it reports the tool surface the
 * band's working style leans on, and is never used to restrict anything.
 * Tool names remapped to ZCode's native set.
 */
export function coreFor(mode) {
  switch (bandOf(mode)) {
    case 'spec': return ['Read', 'Grep', 'Glob', 'Edit'] // read-first
    case 'transition': return ['Read', 'Write', 'Edit', 'Glob', 'Grep'] // union
    case 'weak': return ['Read', 'Write', 'Edit'] // RL shape: shell + editor
    default: return ['Read', 'Write', 'Edit'] // write-first
  }
}

/** Human-readable band name for a mode value. */
export function bandFor(mode) {
  const b = bandOf(mode)
  return b === 'transition' ? 'mixed' : b
}

/** Test-suppression strength for a mode (informational). */
export function testinessFor(mode) {
  switch (bandOf(mode)) {
    case 'react': return 'suppressed'
    case 'spec': return 'normal'
    default: return 'light'
  }
}

const REACT_RE = /(开发|创建|写一个|生成|从零|做一个|游戏|网页|网站|构建|新项目|搭建|实现|做出|上线|落地|脚本|工具|应用|build|create|develop|generate|implement|make a|new project)/gi
const SPEC_RE = /(修复|修一下|调试|重构|维护|排查|报错|出错|崩溃|优化|审查|review|fix|debug|refactor|maintain|repair|broken|break|为什么|异常|故障|迁移|升级|兼容)/gi

function countHits(regex, text) {
  return [...text.matchAll(regex)].length
}

/**
 * Classify a task text into a mode. Clear keyword evidence picks a stable
 * band (1 react / 0 spec); AMBIGUOUS or unmatched text returns 'weak' —
 * the internal-routing mode, where the model decides per task.
 */
export function classifyTask(text) {
  const react = countHits(REACT_RE, text)
  const spec = countHits(SPEC_RE, text)
  if (react > spec) return 1
  if (spec > react) return 0
  return 'weak'
}

export function clamp01(v) {
  return Math.min(1, Math.max(0, Number(v) || 0))
}

/**
 * Advance the stage from durable evidence (tool calls) plus the current user
 * text. Pure and idempotent, so replaying a session reproduces its stage
 * exactly (resume-safe, upstream v0.8).
 *
 * The gates are sequential, not exclusive: when the evidence covers several
 * levels at once the stage CASCADES (upstream behaviour, verified — a stage-0
 * call holding todo_write + write + bash returns 3). That is a replay
 * function, not a step function. Callers that should move one level per event
 * simply pass one event's worth of evidence at a time, which is what the
 * hooks do.
 *
 * Tool names remapped: todo_write→TodoWrite, write→Write, edit→Edit,
 * pwsh/bash→Bash. The text signals are kept identical.
 *
 * `inPlanMode` holds advancement that a mutation would otherwise trigger:
 * writing in plan mode is not development progress. This is a port-local
 * addition — upstream has no plan-mode check here, because DSH withholds the
 * mutation tools in plan mode instead.
 */
export function advanceStage(stage, toolNames, text, inPlanMode = false) {
  const names = Array.isArray(toolNames) ? toolNames : []
  const words = typeof text === 'string' ? text : ''
  let s = stage
  if (s === 0 && (names.includes('TodoWrite') || /开始开发|进入开发|着手实现|开始实现|write the code/i.test(words))) s = 1
  if (s === 1 && !inPlanMode && names.some((n) => ['Write', 'Edit'].includes(n))) s = 2
  if (s === 2 && (names.includes('Bash') || /完成|finished|done|验证/i.test(words))) s = 3
  return s
}

/** Parse a user/agent-supplied mode token: number 0-100, 0.0-1.0, or a band name. */
export function parseMode(token) {
  if (token === undefined || token === null) return null
  const t = String(token).trim().toLowerCase()
  if (t === 'auto') return 'auto'
  if (t === 'weak' || t === 'router') return 'weak'
  if (t === 'spec' || t === 'spec-lean') return 0
  if (t === 'balanced' || t === 'mixed') return 0.3 // transition-band center
  if (t === 'react' || t === 'react-lean') return 1
  const n = Number(t)
  if (!Number.isFinite(n)) return null
  if (t.includes('.')) return clamp01(n)
  return clamp01(n / 100)
}

/** Truncate a task echo the way upstream's firstUserTask does (160 chars). */
export function taskEcho(text, limit = 160) {
  const t = typeof text === 'string' ? text.replace(/\s+/g, ' ').trim() : ''
  if (!t) return ''
  return t.length > limit ? t.slice(0, limit) + '…' : t
}
