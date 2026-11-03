#!/usr/bin/env node
/**
 * UserPromptSubmit: classify the task and inject the near-field routing block.
 *
 * This is the direct analogue of upstream's `agent/pre-step` handler: the
 * guidance lands in the SAME request as the user's message, so it costs no
 * extra API round-trip and stays cache-neutral (upstream measured 92-94%
 * cache hits on per-turn fixed guidance).
 *
 * BAND LOCK: upstream classifies the session's first REAL user message and
 * commits — behavior phase-transitions on the first request, so the band is
 * not re-derived per turn. This port keeps that, with one refinement: a band
 * that landed on `weak` was never cleanly classified (it is the
 * ambiguous-fallback), so if a later prompt carries clear one-sided keyword
 * evidence, `weak` upgrades to that band once. A band that classified
 * cleanly is never re-derived this session.
 */

import {
  eventNameOf, emitContext, inPlanMode, promptText, readConfig, readHookInput,
  readState, sessionIdOf, writeState,
} from './lib/state.mjs'
import { advanceStage, bandFor, classifyTask, isComplexTask, parseMode } from './lib/router-core.mjs'
import { stageBlock } from './lib/guidance.mjs'

const input = await readHookInput()
const eventName = eventNameOf(input, 'UserPromptSubmit')
const config = readConfig()

if (!config.enabled || config.verbosity === 'off') process.exit(0)

const text = promptText(input)
if (!text) process.exit(0)

const sessionId = sessionIdOf(input)
const state = readState(sessionId)
const planMode = inPlanMode(input)

// ── band: lock on first classification, upgrade only from the weak fallback ──
// NB: a forced `spec` parses to the number 0, which is falsy — compare against
// null/'auto' rather than truthiness, or forcing spec silently falls through
// to keyword classification.
const configured = parseMode(config.mode)
let mode
let reason

if (configured !== null && configured !== 'auto') {
  mode = configured
  reason = `config:${config.mode}`
} else {
  const classified = classifyTask(text)
  const isCleanBand = classified === 0 || classified === 1

  if (state.band === null) {
    mode = classified
    reason = isCleanBand ? 'classified:first' : 'classified:ambiguous'
  } else if (state.band === 'weak' && isCleanBand) {
    mode = classified
    reason = 'classified:upgrade-from-weak'
  } else {
    mode = state.band
    reason = 'locked'
  }
}

// ── stage: advance on this turn's text signals, then on tool evidence ──
const stage = advanceStage(state.stage, [], text, planMode)
const task = state.task || text

const block = stageBlock({
  stage,
  mode,
  modelId: input.model || state.model || '',
  task,
  complex: isComplexTask(text),
  reason,
})

if (config.verbosity === 'compact') {
  // Compact keeps the routing decision and the stage line, drops the prose.
  const lines = block.split('\n')
  emitContext(eventName, lines.slice(0, 2).join('\n'))
} else {
  emitContext(eventName, block)
}

writeState(sessionId, {
  ...state,
  stage,
  band: mode,
  task,
  model: input.model || state.model || '',
})
