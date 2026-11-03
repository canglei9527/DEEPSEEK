/**
 * guidance: the near-field text this plugin injects.
 *
 * PORTED from dsh-routing-suite preset/router-standard/router-bootstrap-v34.mjs
 * (v1.20.0 line, MIT). Upstream injects three things, at three different
 * places; ZCode has exactly one injection channel (hook `additionalContext`),
 * so all three are composed here:
 *
 *   1. persona conditioning  -> `directiveFor(mode, modelId)`
 *      Upstream swaps the SYSTEM PROMPT persona. ZCode exposes no
 *      system-prompt hook, so the same conditioning is restated as a
 *      near-field working-style directive. Faithful in substance,
 *      approximate in position — see the fidelity note in router-core.mjs.
 *
 *   2. stage workflow        -> `stageBlock(stage, ...)`
 *      Upstream's strict-stage protocol plus the "task echo" line. The stage
 *      guides are kept in substance; DSH-only tool names (engram_*,
 *      tools_catalog, phase_advance, delivery_check, dev_page_check) are
 *      remapped to ZCode's native counterparts or dropped where ZCode has no
 *      equivalent. Dropped capabilities are listed in renderers below rather
 *      than silently implied.
 *
 *   3. proactivity protocol  -> `PROACTIVITY` (upstream PRESSURE_GUIDE)
 *
 * Depth-adaptive dispatch (upstream v20): simple tasks get fast-convergence
 * guidance, complex tasks get decision-closure deep guidance.
 */

import { bandFor, isComplexTask, taskEcho } from './router-core.mjs'

const ROUTER_VERSION = 'z0.1.0'

/** Upstream PRESSURE_GUIDE, kept in substance; ZCode names its own tools. */
export const PROACTIVITY =
  'Proactivity: every turn, before awaiting the user, scan for the next actionable item — '
  + 'unfinished work, unverified claims, reversible improvements, unfixed warnings. '
  + 'Choose one and act; report what you did and why. '
  + 'Ask only when the choice belongs to the user (preference, budget, irreversible/destructive, external approval). '
  + 'Two or more dependent steps: think step by step, but do not stop to ask permission for reversible work. '
  + 'Depth is our call: a small result gets a small thought, a consequential fork gets full reasoning.'

/**
 * Working-style directives — the near-field form of the measured personas.
 * The conditioning of each band is preserved: react = doer, spec = read-first
 * planner, weak = route yourself per task.
 */
const DIRECTIVE_REACT =
  'Working style — react (hands-on production). Deliver working output fast: write or edit the artifact, then '
  + 'verify it by reading and running it. Keep the loop tight — produce, verify, fix. Do not build test harnesses, '
  + 'scaffolding, or ceremony the user did not ask for. Finish with a usable deliverable and a short summary.'

const DIRECTIVE_SPEC =
  'Working style — spec (inspect-and-plan). Ground the work before changing it: read the existing implementation, '
  + 'trace how it actually behaves, then make the minimal compatible change. Prefer reusing existing functions and '
  + 'patterns over new machinery. State the root cause or the design decision in one line before you edit, and '
  + 'verify the change against the behavior you were asked to correct.'

const DIRECTIVE_MIXED =
  'Working style — mixed. Ground the work with a read, then work directly: write or edit the artifact and verify by '
  + 'reading and running it. Do not over-plan, do not skip inspection.'

function directiveWeak(modelId) {
  // Upstream: flash gets the neutral + classify + recall/anti-runaway anchors
  // because the spec-sentence weak persona ANTI-routes on flash. Non-flash
  // gets the spec sentence + classify instruction.
  if (/flash/i.test(String(modelId || ''))) {
    return 'Working style — route yourself. Before acting, decide the task type (build or fix) and adopt the matching '
      + 'style: build → hands-on production; fix → inspect-and-plan. Briefly review what you have already done in this '
      + 'session and continue from where you left off; do not repeat completed steps. Do not run environment checks '
      + '(echo, whoami, uname, node --version, date) or exhaustive scans. Think deeply first, then produce.'
  }
  return 'Working style — route yourself. This task did not classify cleanly. Before acting, decide the task type '
    + '(build or fix) and adopt the matching style: build → hands-on production; fix → inspect-and-plan.'
}

/** Near-field working-style directive for a band. */
export function directiveFor(mode, modelId) {
  switch (bandFor(mode)) {
    case 'spec': return DIRECTIVE_SPEC
    case 'mixed': return DIRECTIVE_MIXED
    case 'weak': return directiveWeak(modelId)
    default: return DIRECTIVE_REACT
  }
}

/** Upstream STAGES, minus the tool-gating (ZCode cannot gate tools). */
export const STAGES = [
  {
    name: 'understanding',
    guide:
      'Ground first: read the actual code and ask for what you cannot infer. Do not settle on the first obvious '
      + 'reading. Break the request into its dimensions (what the user wants, how it should behave, what "good" '
      + 'looks like) and surface the genuinely ambiguous ones. Decide each ambiguity by its impact: if one reading is '
      + 'clearly the common meaning, state that assumption in one sentence so the user can correct it and continue; if '
      + 'two readings would materially change the result, ask one focused question with concrete options. Do not '
      + 'under-ask (assume everything) nor over-ask (interrogate trivia). If the task is complex, record a plan.',
  },
  {
    name: 'planning',
    guide:
      'Design the approach, cover edge cases, define what "done" means, and decide acceptance criteria — not just a '
      + 'list of steps. Assetize your context: keep the task goal + the current decision + the live evidence in the '
      + 'attention window; let settled exploration drop. When two independent concerns are polluting one thread, or a '
      + 'sub-problem is eating the mainline budget, push it into a subagent instead of keeping it in the same stream. '
      + 'Complete when the plan is decision-complete: what to build, how it fits, what success looks like.',
  },
  {
    name: 'development',
    guide:
      'Make the artifact real, then self-check it against the plan and the acceptance criteria. Re-read before '
      + 're-editing; inspect changed lines with Read/Grep rather than re-printing whole files. Avoid local optima: if '
      + 'the same detail resists for several rounds with no convergence, step back — is it blocking the deliverable or '
      + 'is it polish? Preserve a working version, finish the rest, and re-attack the stubborn detail fresh.',
  },
  {
    name: 'verification',
    guide:
      'Verify the real artifact, not a summary of it. Run it, check evidence, look for defects, and review visuals '
      + 'honestly — for anything a user sees (pages, images, UI), capture and actually look at it rather than asserting '
      + 'it works. On failure, hold a quick hypothesis-audit before touching the implementation: name in one line which '
      + 'assumption you are now re-checking and what NEW evidence you just gained. If the code is actually correct, say '
      + 'so and move on — do not manufacture a bug to justify rework. Until the artifact passes its own check, do not '
      + 'declare the task delivered: delivery is a gate, not a progress label.',
  },
]

/** Text signals that move a stage forward, checked in addition to tool calls. */
export const ADVANCE_SIGNALS = [
  /开始开发|进入开发|着手实现|开始实现|write the code/i,
  /完成|finished|done|验证/i,
]

/**
 * Render the near-field block for one turn.
 *
 * Kept deliberately compact: upstream measured that per-turn fixed guidance
 * is near-cache-neutral, but an unbounded preamble would still be an
 * attention tax. The stage guide is the only long section and it is stable
 * for the whole stage, so it caches.
 */
export function stageBlock({ stage, mode, modelId, task = '', complex = false, reason = 'auto' }) {
  const guide = STAGES[Math.min(stage, STAGES.length - 1)]
  const taskLine = taskEcho(task) ? `\nTask: ${taskEcho(task)}` : ''
  const depth = complex
    ? 'Think deeply about the architecture, edge cases, and integration points. Do not spend reasoning on the '
      + 'environment or tooling. Produce when your information is complete. End each reasoning block with a decision '
      + 'or an information need.'
    : 'This reads as a small task: converge fast. One step where one step suffices; do not spend reasoning on the '
      + 'environment or tooling.'

  return [
    `[dsh-router ${ROUTER_VERSION}] band=${bandFor(mode)} stage=${stage}/3 (${guide.name}) route=${reason}`,
    directiveFor(mode, modelId),
    `Stage — ${guide.name}: ${guide.guide}`,
    `Depth — ${depth}`,
    PROACTIVITY + taskLine,
  ].join('\n')
}

/** One-time bootstrap text (SessionStart) — upstream START_GUIDE, adapted. */
export function bootstrapBlock() {
  return [
    `[dsh-router ${ROUTER_VERSION}] Routed session context is active. This note appears once per session.`,
    'Each user message gets a near-field routing block: the task is classified (build → hands-on production, '
    + 'fix → inspect-and-plan, ambiguous → route yourself), and the current workflow stage is restated with its guide.',
    'Stages advance on real progress, never on tool use alone: understanding (task grounded, ambiguities stated or '
    + 'asked) → planning (approach decision-complete) → development (artifact exists and self-checked) → verification '
    + '(the real artifact verified). If you believe the stage is wrong, say so in one line and continue — the stage is '
    + 'a guide, not a gate.',
    PROACTIVITY,
  ].join('\n')
}

/**
 * Capability honesty note, surfaced once so the port does not silently
 * promise DSH behaviour it cannot deliver.
 */
export const FIDELITY_NOTE =
  'Port note: upstream (dsh-routing-suite) swaps the system prompt persona and gates the tool catalog per stage. '
  + 'ZCode exposes neither, so this port conditions via near-field context and reports the stage instead of gating '
  + 'tools. Tool availability is never restricted by this plugin.'

export { isComplexTask, ROUTER_VERSION }
