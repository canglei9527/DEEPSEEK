/**
 * Differential test: the ported router core must agree with the upstream
 * router-core on every pure function it claims to have ported verbatim.
 *
 * Upstream is read from the dsh-routing-suite checkout, so a divergence
 * introduced by a future upstream edit shows up here as a failure instead of
 * silently drifting. Where the port deliberately differs (tool-name
 * vocabulary), the mapping is asserted explicitly rather than compared.
 *
 *   node --test tests/router-core.test.mjs
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { existsSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import * as port from '../hooks/lib/router-core.mjs'

const here = dirname(fileURLToPath(import.meta.url))

/**
 * Locate the upstream checkout. The differential assertions only mean
 * something where upstream is actually present (the source workspace), so
 * probe the known locations and SKIP when none exists — a missing sibling
 * checkout is not a test failure, and the plugin must stay installable
 * anywhere without its test suite breaking.
 */
const CANDIDATES = [
  process.env.DSH_ROUTING_SUITE, // explicit override
  join(here, '..', '..', '..', 'dsh-routing-suite', 'preset', 'router-standard', 'router-core.mjs'),
  join(here, '..', '..', 'dsh-routing-suite', 'preset', 'router-standard', 'router-core.mjs'),
]
const UPSTREAM = CANDIDATES.find((p) => p && existsSync(p)) ?? null

const hasUpstream = UPSTREAM !== null
const upstream = hasUpstream ? await import(`file://${UPSTREAM.replace(/\\/g, '/')}`) : null

const skipNoUpstream = hasUpstream
  ? {}
  : { skip: 'upstream dsh-routing-suite checkout not found; differential comparison skipped' }

/** Corpus spanning both keyword sets, the ambiguous middle, and non-ASCII. */
const CORPUS = [
  '',
  'build me a landing page for a coffee shop',
  '写一个贪吃蛇游戏',
  '从零搭建一个新项目',
  '创建脚本',
  'fix the login bug',
  '修复登录报错',
  '为什么这个页面崩溃了',
  '调试一下这个接口',
  '重构 auth 模块',
  '优化性能',
  'review 这段代码',
  'hello',
  'thanks',
  '帮我看下',
  'build and fix the thing', // 1 react / 1 spec -> tie -> weak
  '修复并重构这个模块',        // spec-heavy
  '开发一个新功能并修复旧 bug', // 1 react / 1 spec -> tie
  'a'.repeat(200),
  '设计一个系统架构',
  '请你全面详细地分析并优化整个系统架构，给出完整重构方案', // complex + spec
  'please implement a comprehensive new project from scratch', // react + complex
]

/** Make the differential coverage visible instead of a silent skip. */
test('differential coverage against upstream', () => {
  if (hasUpstream) {
    assert.ok(UPSTREAM, 'upstream resolved')
    console.log(`      comparing against upstream at ${UPSTREAM}`)
  } else {
    console.log('      upstream checkout NOT found — differential assertions were skipped; '
      + 'set DSH_ROUTING_SUITE to a dsh-routing-suite checkout to run them')
  }
})

test('classifyTask agrees with upstream on the whole corpus', skipNoUpstream, () => {
  for (const text of CORPUS) {
    assert.deepEqual(
      port.classifyTask(text), upstream.classifyTask(text),
      `classifyTask diverged for: ${JSON.stringify(text.slice(0, 60))}`,
    )
  }
})

test('bandOf agrees with upstream across the mode range', skipNoUpstream, () => {
  const modes = [0, 0.1, 0.15, 0.19, 0.2, 0.3, 0.45, 0.49, 0.5, 0.75, 1, 'weak', -5, 99, 0.3]
  for (const m of modes) {
    assert.equal(port.bandOf(m), upstream.bandOf(m), `bandOf diverged for ${m}`)
  }
})

test('personaFor agrees with upstream for both model families', skipNoUpstream, () => {
  for (const modelId of ['deepseek-v4-pro', 'deepseek-v4-flash', '', undefined]) {
    for (const mode of [0, 0.3, 1, 'weak']) {
      assert.equal(
        port.personaFor(mode, modelId), upstream.personaFor(mode, modelId),
        `personaFor diverged for mode=${mode} model=${modelId}`,
      )
    }
  }
})

test('isComplexTask and isFlashModel agree with upstream', skipNoUpstream, () => {
  for (const text of CORPUS) {
    assert.equal(port.isComplexTask(text), upstream.isComplexTask(text), `isComplexTask: ${text.slice(0, 40)}`)
  }
  for (const id of ['x-flash', 'FLASH', 'pro', '', undefined, null]) {
    assert.equal(port.isFlashModel(id), upstream.isFlashModel(id), `isFlashModel: ${id}`)
  }
})

test('parseMode agrees with upstream on tokens and numbers', skipNoUpstream, () => {
  const tokens = ['auto', 'weak', 'router', 'spec', 'spec-lean', 'balanced', 'mixed', 'react',
    'react-lean', '0', '50', '100', '0.4', '1.5', '-1', 'nonsense', '', null, undefined, '  0.25  ']
  for (const t of tokens) {
    assert.equal(port.parseMode(t), upstream.parseMode(t), `parseMode diverged for ${JSON.stringify(t)}`)
  }
})

test('clamp01 and bandFor agree with upstream', skipNoUpstream, () => {
  for (const v of [-1, 0, 0.5, 1, 2, '0.7', NaN, undefined, 'x']) {
    assert.equal(port.clamp01(v), upstream.clamp01(v), `clamp01 diverged for ${v}`)
    assert.equal(port.bandFor(v), upstream.bandFor(v), `bandFor diverged for ${v}`)
  }
})

test('bandFor collapses the transition band to "mixed"', () => {
  assert.equal(port.bandFor(0.3), 'mixed')
  assert.equal(port.bandFor(0), 'spec')
  assert.equal(port.bandFor(1), 'react')
  assert.equal(port.bandFor('weak'), 'weak')
})

// ── deliberate divergences: asserted, not compared ──

test('advanceStage maps upstream tool names onto ZCode tool names', () => {
  // upstream: todo_write -> 1 ; port: TodoWrite -> 1
  assert.equal(port.advanceStage(0, ['TodoWrite'], ''), 1)
  assert.equal(port.advanceStage(0, ['write'], ''), 0, 'upstream-only name must not advance the port')
  // upstream: write/edit/str_replace_editor -> 2 ; port: Write/Edit -> 2
  assert.equal(port.advanceStage(1, ['Write'], ''), 2)
  assert.equal(port.advanceStage(1, ['Edit'], ''), 2)
  assert.equal(port.advanceStage(1, ['str_replace_editor'], ''), 1, 'upstream-only name does not advance')
  // upstream: pwsh/bash -> 3 ; port: Bash -> 3
  assert.equal(port.advanceStage(2, ['Bash'], ''), 3)
  assert.equal(port.advanceStage(2, ['pwsh'], ''), 2, 'upstream-only name does not advance')
})

test('advanceStage cascades exactly like upstream on the same evidence', skipNoUpstream, () => {
  // Upstream's gates are sequential, not exclusive, so sufficient evidence
  // cascades in ONE call. Assert the port reproduces that, with the tool-name
  // mapping applied to the input rather than the comparison.
  const NAME_MAP = {
    todo_write: 'TodoWrite', write: 'Write', edit: 'Edit',
    str_replace_editor: 'Edit', pwsh: 'Bash', bash: 'Bash',
  }
  const cases = [
    [0, ['todo_write', 'write', 'bash']],
    [0, ['todo_write']],
    [0, ['write']],
    [1, ['write']],
    [1, ['edit']],
    [2, ['bash']],
    [2, ['pwsh']],
    [3, ['write', 'bash']],
    [0, []],
  ]
  for (const [stage, tools] of cases) {
    const mapped = tools.map((t) => NAME_MAP[t] ?? t)
    assert.equal(
      port.advanceStage(stage, mapped, ''),
      upstream.advanceStage(stage, tools, ''),
      `advanceStage diverged for stage=${stage} tools=${JSON.stringify(tools)}`,
    )
  }
  // Text-signal gates, compared directly.
  for (const text of ['开始开发', '进入开发', '着手实现', '开始实现', 'write the code', '完成', 'finished', 'done', '验证', 'nope']) {
    assert.equal(
      port.advanceStage(2, [], text),
      upstream.advanceStage(2, [], text),
      `advanceStage diverged on text signal: ${text}`,
    )
  }
})

test('advanceStage saturates at 3 and is idempotent', () => {
  assert.equal(port.advanceStage(3, ['TodoWrite', 'Write', 'Bash'], ''), 3, 'saturates at 3')
  const once = port.advanceStage(0, ['TodoWrite'], '')
  assert.equal(port.advanceStage(once, ['TodoWrite'], ''), 1, 'replay reproduces the same stage')
})

test('a mutation made in plan mode is not development progress', () => {
  assert.equal(port.advanceStage(1, ['Write'], '', false), 2)
  assert.equal(port.advanceStage(1, ['Write'], '', true), 1, 'plan-mode write must not enter development')
})

test('coreFor reports ZCode tool names and never gates anything', () => {
  const all = [0, 0.3, 1, 'weak'].flatMap((m) => port.coreFor(m))
  for (const name of all) {
    assert.ok(
      ['Read', 'Write', 'Edit', 'Grep', 'Glob'].includes(name),
      `coreFor returned a non-ZCode tool name: ${name}`,
    )
  }
})

test('taskEcho truncates at 160 chars the way upstream does', () => {
  assert.equal(port.taskEcho('short task'), 'short task')
  assert.equal(port.taskEcho('   '), '')
  const long = 'x'.repeat(200)
  const out = port.taskEcho(long)
  assert.equal(out.length, 161, '160 chars + ellipsis')
  assert.ok(out.endsWith('…'))
  assert.equal(port.taskEcho('a\n\nb   c'), 'a b c', 'whitespace is collapsed for a single-line echo')
})
