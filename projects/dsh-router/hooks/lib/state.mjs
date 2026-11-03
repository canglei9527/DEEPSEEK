/**
 * state: per-session router state, persisted on disk.
 *
 * Hooks run as one process per event, so nothing survives in memory between
 * them. This module is the single durable record: the current stage, the
 * band, the task echo, and whether the bootstrap note has been delivered.
 * It is written on every event so a crashed or reloaded session resumes at
 * the stage it actually reached (upstream's resume-safety requirement).
 *
 * Also holds the user-facing switch: `config.json` in the plugin data dir
 * with `{ enabled, verbosity, mode }`.
 */

import { mkdirSync, readFileSync, writeFileSync, renameSync, readdirSync, statSync, unlinkSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { homedir } from 'node:os'

const SESSION_TTL_MS = 30 * 24 * 60 * 60 * 1000 // 30 days

/**
 * Resolve the plugin data directory.
 *
 * The client sets ZCODE_PLUGIN_DATA when it runs a hook, and that is the only
 * path that matters in normal operation — it points at
 * `~/.zcode/cli/plugins/data/<plugin>@<marketplace>/`, one directory per
 * installed plugin. The fallback below matches that location so a manual
 * invocation (no env) reads and writes the SAME state the client uses; it
 * deliberately does not use `~/.zcode/plugin-data/`, which is a different
 * convention and would silently split the config in two.
 */
export function dataDir(env = process.env) {
  const configured = env.ZCODE_PLUGIN_DATA ?? env.CLAUDE_PLUGIN_DATA
  return configured && configured.length
    ? resolve(configured)
    : join(homedir(), '.zcode', 'cli', 'plugins', 'data', 'dsh-router@local-dsh-router')
}

function sessionsDir(env) {
  return join(dataDir(env), 'sessions')
}

/** Config defaults — the plugin is on unless the user writes otherwise. */
export const DEFAULT_CONFIG = { enabled: true, verbosity: 'full', mode: 'auto' }

export function readConfig(env = process.env) {
  try {
    const raw = readFileSync(join(dataDir(env), 'config.json'), 'utf8')
    const parsed = JSON.parse(raw)
    return {
      enabled: parsed.enabled !== false,
      verbosity: ['full', 'compact', 'off'].includes(parsed.verbosity) ? parsed.verbosity : 'full',
      // A numeric mode is part of the documented interface (0 spec, 30 mixed,
      // 100 react), so accept numbers as well as band names.
      mode: typeof parsed.mode === 'string' || typeof parsed.mode === 'number'
        ? parsed.mode
        : 'auto',
    }
  } catch {
    return { ...DEFAULT_CONFIG }
  }
}

function safeId(sessionId) {
  return String(sessionId || 'unknown').replace(/[^A-Za-z0-9._-]/g, '_').slice(0, 128)
}

export function defaultState() {
  return {
    stage: 0,
    band: null, // last cleanly-classified band for this session
    task: '',
    guided: false, // bootstrap note delivered
    model: '',
    updatedAt: 0,
  }
}

export function readState(sessionId, env = process.env) {
  try {
    const raw = readFileSync(join(sessionsDir(env), `${safeId(sessionId)}.json`), 'utf8')
    const parsed = JSON.parse(raw)
    return { ...defaultState(), ...parsed }
  } catch {
    return defaultState()
  }
}

export function writeState(sessionId, state, env = process.env) {
  const dir = sessionsDir(env)
  try {
    mkdirSync(dir, { recursive: true })
    const target = join(dir, `${safeId(sessionId)}.json`)
    const tmp = `${target}.${process.pid}.tmp`
    const next = { ...state, updatedAt: Date.now() }
    writeFileSync(tmp, JSON.stringify(next, null, 2), 'utf8')
    renameSync(tmp, target) // atomic on the same filesystem
    pruneSessions(dir)
    return next
  } catch {
    return state // state is best-effort; a write failure must never block a turn
  }
}

function pruneSessions(dir) {
  try {
    const now = Date.now()
    for (const name of readdirSync(dir)) {
      if (!name.endsWith('.json')) continue
      const p = join(dir, name)
      if (now - statSync(p).mtimeMs > SESSION_TTL_MS) unlinkSync(p)
    }
  } catch {
    /* pruning is opportunistic */
  }
}

/** Read one JSON event object from stdin (the ZCode hook protocol). */
export async function readHookInput() {
  let raw = ''
  process.stdin.setEncoding('utf8')
  for await (const chunk of process.stdin) raw += chunk
  try {
    const parsed = raw.trim() ? JSON.parse(raw) : {}
    return parsed && typeof parsed === 'object' ? parsed : {}
  } catch {
    return {}
  }
}

/** Emit additionalContext for an event; stdout must be a single JSON object. */
export function emitContext(eventName, context) {
  if (!context) return
  process.stdout.write(JSON.stringify({
    hookSpecificOutput: { hookEventName: eventName, additionalContext: context },
  }))
}

/**
 * Pull the user's prompt text out of a UserPromptSubmit payload. Field naming
 * has varied across client versions, so probe the known shapes in order and
 * fall back to any string content rather than emitting a misrouted block.
 */
export function promptText(input) {
  const candidates = [input.prompt, input.user_prompt, input.userPrompt, input.message, input.text]
  for (const c of candidates) {
    if (typeof c === 'string' && c.trim()) return c
    if (c && typeof c === 'object') {
      const content = Array.isArray(c.content) ? c.content : []
      const joined = content.map((x) => (typeof x === 'string' ? x : (x?.text ?? ''))).join(' ').trim()
      if (joined) return joined
    }
  }
  return ''
}

export function eventNameOf(input, fallback) {
  return input.hook_event_name || input.hookEventName || fallback
}

export function sessionIdOf(input) {
  return input.session_id || input.sessionId || 'unknown'
}

export function inPlanMode(input) {
  return input.permission_mode === 'plan' || input.permissionMode === 'plan'
}
