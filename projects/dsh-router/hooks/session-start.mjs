#!/usr/bin/env node
/**
 * SessionStart: deliver the one-time routing bootstrap.
 *
 * Upstream injects START_GUIDE once per session from `phase_begin` and then
 * never again. Here the same role is played by this hook, guarded by the
 * durable `guided` flag so a resume or compaction does not repeat it.
 */

import {
  eventNameOf, emitContext, inPlanMode, readConfig, readHookInput, readState,
  sessionIdOf, writeState,
} from './lib/state.mjs'
import { FIDELITY_NOTE, ROUTER_VERSION, bootstrapBlock } from './lib/guidance.mjs'

const input = await readHookInput()
const eventName = eventNameOf(input, 'SessionStart')
const sessionId = sessionIdOf(input)
const config = readConfig()

if (!config.enabled || config.verbosity === 'off') process.exit(0)

const state = readState(sessionId)

// A fresh SessionStart means a fresh session record. `compact` resumes the
// same conversation, so only `startup`/`clear` reset the stage.
const source = input.source || input.reason || 'startup'
const fresh = source === 'startup' || source === 'clear'
const next = fresh
  ? { ...state, stage: 0, band: null, task: '', guided: false }
  : state

// On resume/compact, re-state where we are rather than replaying the intro.
if (!fresh && next.guided) {
  const { stageBlock } = await import('./lib/guidance.mjs')
  const band = next.band ?? 'weak'
  const block = stageBlock({
    stage: next.stage,
    mode: band,
    modelId: next.model,
    task: next.task,
    complex: true,
    reason: next.band === null ? 'resume:unrouted' : 'resume',
  })
  emitContext(eventName, next.band === null
    // Don't print a band the session never locked — it would read as a decision.
    ? block.replace(/^\[dsh-router[^\n]*\n/, `[dsh-router ${ROUTER_VERSION}] resumed before any prompt was routed; the next message will classify.\n`)
    : block)
  process.exit(0)
}

const parts = [bootstrapBlock()]
if (inPlanMode(input)) {
  parts.push('Plan mode is active: nothing here lifts the plan boundary. Explore and plan; do not implement.')
}
parts.push(FIDELITY_NOTE)
emitContext(eventName, parts.join('\n\n'))

next.guided = true
if (typeof input.model === 'string' && input.model) next.model = input.model
writeState(sessionId, next)
