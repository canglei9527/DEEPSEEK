#!/usr/bin/env node
/**
 * PostToolUse: advance the workflow stage from durable tool evidence.
 *
 * Upstream derives the stage from durable session events (tool calls), which
 * is what makes stages survive resume and compaction. ZCode gives hooks the
 * tool name and whether plan mode is active, so that is the evidence used
 * here — a mutation made while planning is not development progress.
 *
 * Emits nothing: this hook only records state. Injecting on every tool call
 * would be a per-turn attention tax for no routing benefit.
 */

import {
  readConfig, readHookInput, readState, sessionIdOf, writeState, inPlanMode,
} from './lib/state.mjs'
import { advanceStage } from './lib/router-core.mjs'

const input = await readHookInput()
const config = readConfig()
if (!config.enabled || config.verbosity === 'off') process.exit(0)

const sessionId = sessionIdOf(input)
const state = readState(sessionId)

const toolName = input.tool_name || input.toolName || ''
if (!toolName) process.exit(0)

const stage = advanceStage(state.stage, [toolName], '', inPlanMode(input))
if (stage !== state.stage) {
  writeState(sessionId, { ...state, stage })
}
