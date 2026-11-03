/**
 * Integration test: drive the real hook scripts as child processes, exactly
 * the way ZCode does (one JSON object on stdin, one on stdout), and assert
 * the injected context.
 *
 * The point is to cover the wiring the unit tests cannot see: config
 * handling, the stdout contract, band locking across turns, and the
 * regressions that manual smoke-testing already found once.
 *
 *   node --test tests/hooks.integration.test.mjs
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { mkdtempSync, rmSync, writeFileSync, readFileSync, existsSync, readdirSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { tmpdir, homedir } from 'node:os'

const here = dirname(fileURLToPath(import.meta.url))
const ROOT = join(here, '..')

/** Run a hook with a fresh data dir; return {stdout, stderr, parsed, context}. */
function runHook(script, input, { dataDir, env = {} } = {}) {
  const stdout = execFileSync(process.execPath, [join(ROOT, 'hooks', script)], {
    input: typeof input === 'string' ? input : JSON.stringify(input),
    encoding: 'utf8',
    env: { ...process.env, ZCODE_PLUGIN_DATA: dataDir, ...env },
  })
  let parsed = null
  try { parsed = JSON.parse(stdout) } catch { /* asserted by the caller */ }
  return { stdout, parsed, context: parsed?.hookSpecificOutput?.additionalContext ?? '' }
}

/** A fresh data dir per test so state never leaks between cases. */
function withDataDir(fn) {
  const dir = mkdtempSync(join(tmpdir(), 'dsh-router-test-'))
  try {
    return fn(dir)
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }
}

function writeConfig(dir, config) {
  writeFileSync(join(dir, 'config.json'), JSON.stringify(config), 'utf8')
}

const header = (ctx) => ctx.split('\n')[0]

// ── stdout contract ──

test('every hook emits a single parseable JSON object with the right event name', () => {
  withDataDir((dir) => {
    const cases = [
      ['session-start.mjs', { hook_event_name: 'SessionStart', session_id: 'S', source: 'startup' }, 'SessionStart'],
      ['user-prompt-submit.mjs', { hook_event_name: 'UserPromptSubmit', session_id: 'S', prompt: '写一个游戏' }, 'UserPromptSubmit'],
    ]
    for (const [script, input, expectedEvent] of cases) {
      const { stdout, parsed } = runHook(script, input, { dataDir: dir })
      assert.ok(stdout.trimStart().startsWith('{'), `${script}: stdout must start with {`)
      assert.equal(parsed?.hookSpecificOutput?.hookEventName, expectedEvent)
      assert.equal(typeof parsed?.hookSpecificOutput?.additionalContext, 'string')
    }
  })
})

test('PostToolUse stays silent (state only, no per-tool context tax)', () => {
  withDataDir((dir) => {
    const { stdout } = runHook('post-tool-use.mjs',
      { hook_event_name: 'PostToolUse', session_id: 'S', tool_name: 'Write' }, { dataDir: dir })
    assert.equal(stdout, '', 'PostToolUse must not inject context')
  })
})

// ── routing ──

test('build prompts route to react, fix prompts route to spec', () => {
  withDataDir((dir) => {
    const react = runHook('user-prompt-submit.mjs',
      { hook_event_name: 'UserPromptSubmit', session_id: 'A', prompt: '帮我写一个贪吃蛇游戏' }, { dataDir: dir })
    assert.match(header(react.context), /band=react/)
    assert.match(header(react.context), /route=classified:first/)

    const spec = runHook('user-prompt-submit.mjs',
      { hook_event_name: 'UserPromptSubmit', session_id: 'B', prompt: '修复登录时的报错' }, { dataDir: dir })
    assert.match(header(spec.context), /band=spec/)
  })
})

test('an ambiguous prompt lands on weak, then upgrades on clear evidence', () => {
  withDataDir((dir) => {
    const first = runHook('user-prompt-submit.mjs',
      { hook_event_name: 'UserPromptSubmit', session_id: 'S', prompt: '帮我看下这个东西' }, { dataDir: dir })
    assert.match(header(first.context), /band=weak/)
    assert.match(header(first.context), /route=classified:ambiguous/)

    const second = runHook('user-prompt-submit.mjs',
      { hook_event_name: 'UserPromptSubmit', session_id: 'S', prompt: '修复崩溃报错' }, { dataDir: dir })
    assert.match(header(second.context), /band=spec/)
    assert.match(header(second.context), /route=classified:upgrade-from-weak/)
  })
})

test('a cleanly classified band locks and does not flip on a later prompt', () => {
  withDataDir((dir) => {
    runHook('user-prompt-submit.mjs',
      { hook_event_name: 'UserPromptSubmit', session_id: 'S', prompt: '写一个游戏' }, { dataDir: dir })
    const later = runHook('user-prompt-submit.mjs',
      { hook_event_name: 'UserPromptSubmit', session_id: 'S', prompt: '修复这个报错' }, { dataDir: dir })
    assert.match(header(later.context), /band=react/, 'band was locked at first classification')
    assert.match(header(later.context), /route=locked/)
  })
})

test('the task echo prefers the first task of the session', () => {
  withDataDir((dir) => {
    runHook('user-prompt-submit.mjs',
      { hook_event_name: 'UserPromptSubmit', session_id: 'S', prompt: '写一个游戏' }, { dataDir: dir })
    const later = runHook('user-prompt-submit.mjs',
      { hook_event_name: 'UserPromptSubmit', session_id: 'S', prompt: '再加一个功能' }, { dataDir: dir })
    assert.match(later.context, /Task: 写一个游戏/)
  })
})

// ── config ──

test('forcing spec via config works (regression: 0 is falsy)', () => {
  withDataDir((dir) => {
    writeConfig(dir, { mode: 'spec' })
    const { context } = runHook('user-prompt-submit.mjs',
      { hook_event_name: 'UserPromptSubmit', session_id: 'S', prompt: '写一个游戏' }, { dataDir: dir })
    assert.match(header(context), /band=spec/, 'config mode:spec must override keyword classification')
    assert.match(header(context), /route=config:spec/)
  })
})

test('forcing each band via config overrides classification', () => {
  withDataDir((dir) => {
    for (const [value, expected] of [['react', 'react'], ['spec', 'spec'], ['weak', 'weak'], ['mixed', 'mixed'], [0, 'spec'], [100, 'react'], [30, 'mixed']]) {
      writeConfig(dir, { mode: value })
      const { context } = runHook('user-prompt-submit.mjs',
        { hook_event_name: 'UserPromptSubmit', session_id: `S${value}`, prompt: '写一个游戏' }, { dataDir: dir })
      assert.match(header(context), new RegExp(`band=${expected}\\b`), `mode:${value} should route to ${expected}`)
    }
  })
})

test('verbosity:off and enabled:false both suppress output entirely', () => {
  withDataDir((dir) => {
    for (const cfg of [{ verbosity: 'off' }, { enabled: false }]) {
      writeConfig(dir, cfg)
      const { stdout, context } = runHook('user-prompt-submit.mjs',
        { hook_event_name: 'UserPromptSubmit', session_id: 'S', prompt: '写一个游戏' }, { dataDir: dir })
      assert.equal(stdout, '', `${JSON.stringify(cfg)} must produce no stdout`)
      assert.equal(context, '')
    }
  })
})

test('verbosity:compact keeps the routing header and directive only', () => {
  withDataDir((dir) => {
    writeConfig(dir, { verbosity: 'compact' })
    const { context } = runHook('user-prompt-submit.mjs',
      { hook_event_name: 'UserPromptSubmit', session_id: 'S', prompt: '修复报错' }, { dataDir: dir })
    const lines = context.split('\n')
    assert.equal(lines.length, 2, 'compact = header + directive, no stage guide')
    assert.match(lines[0], /band=spec/)
    assert.match(lines[1], /Working style/)
  })
})

// ── stage state ──

test('stages advance on tool evidence and survive to the next prompt', () => {
  withDataDir((dir) => {
    runHook('post-tool-use.mjs', { hook_event_name: 'PostToolUse', session_id: 'S', tool_name: 'TodoWrite' }, { dataDir: dir })
    runHook('post-tool-use.mjs', { hook_event_name: 'PostToolUse', session_id: 'S', tool_name: 'Write' }, { dataDir: dir })
    const { context } = runHook('user-prompt-submit.mjs',
      { hook_event_name: 'UserPromptSubmit', session_id: 'S', prompt: '继续' }, { dataDir: dir })
    assert.match(header(context), /stage=2\/3 \(development\)/)
  })
})

test('a mutation in plan mode does not advance the stage (regression)', () => {
  withDataDir((dir) => {
    runHook('post-tool-use.mjs', { hook_event_name: 'PostToolUse', session_id: 'S', tool_name: 'TodoWrite' }, { dataDir: dir })
    runHook('post-tool-use.mjs',
      { hook_event_name: 'PostToolUse', session_id: 'S', tool_name: 'Write', permission_mode: 'plan' }, { dataDir: dir })
    const state = JSON.parse(readFileSync(join(dir, 'sessions', 'S.json'), 'utf8'))
    assert.equal(state.stage, 1, 'plan-mode write must not enter development')
  })
})

test('session state is written and resumable', () => {
  withDataDir((dir) => {
    runHook('user-prompt-submit.mjs',
      { hook_event_name: 'UserPromptSubmit', session_id: 'S', prompt: '修复报错' }, { dataDir: dir })
    const state = JSON.parse(readFileSync(join(dir, 'sessions', 'S.json'), 'utf8'))
    assert.equal(state.band, 0, 'spec band persisted as 0')
    assert.equal(state.task, '修复报错')
    assert.equal(state.stage, 0)
    assert.ok(state.updatedAt > 0)
  })
})

// ── SessionStart ──

test('SessionStart delivers the bootstrap once, then only re-states', () => {
  withDataDir((dir) => {
    const first = runHook('session-start.mjs',
      { hook_event_name: 'SessionStart', session_id: 'S', source: 'startup' }, { dataDir: dir })
    assert.match(first.context, /appears once per session/)
    assert.match(first.context, /Tool availability is never restricted/)

    const resumed = runHook('session-start.mjs',
      { hook_event_name: 'SessionStart', session_id: 'S', source: 'resume' }, { dataDir: dir })
    assert.doesNotMatch(resumed.context, /appears once per session/, 'bootstrap must not repeat')
    assert.match(resumed.context, /resumed before any prompt was routed/)

    const compacted = runHook('session-start.mjs',
      { hook_event_name: 'SessionStart', session_id: 'S', source: 'compact' }, { dataDir: dir })
    assert.doesNotMatch(compacted.context, /appears once per session/)
  })
})

test('a fresh startup resets the stage', () => {
  withDataDir((dir) => {
    runHook('post-tool-use.mjs', { hook_event_name: 'PostToolUse', session_id: 'S', tool_name: 'TodoWrite' }, { dataDir: dir })
    runHook('session-start.mjs', { hook_event_name: 'SessionStart', session_id: 'S', source: 'clear' }, { dataDir: dir })
    const state = JSON.parse(readFileSync(join(dir, 'sessions', 'S.json'), 'utf8'))
    assert.equal(state.stage, 0, 'a fresh session starts at stage 0')
    assert.equal(state.band, null)
  })
})

test('plan mode at SessionStart does not lift the plan boundary', () => {
  withDataDir((dir) => {
    const { context } = runHook('session-start.mjs',
      { hook_event_name: 'SessionStart', session_id: 'S', source: 'startup', permission_mode: 'plan' }, { dataDir: dir })
    assert.match(context, /Plan mode is active/)
  })
})

// ── robustness ──

test('malformed or empty input never crashes a hook or emits junk', () => {
  withDataDir((dir) => {
    for (const raw of ['', 'not json', '[]', 'null', '{}']) {
      for (const script of ['session-start.mjs', 'user-prompt-submit.mjs', 'post-tool-use.mjs']) {
        const stdout = execFileSync(process.execPath, [join(ROOT, 'hooks', script)], {
          input: raw, encoding: 'utf8', env: { ...process.env, ZCODE_PLUGIN_DATA: dir },
        })
        if (stdout.trim()) {
          assert.ok(stdout.trimStart().startsWith('{'), `${script} emitted junk for input ${JSON.stringify(raw)}`)
          JSON.parse(stdout) // must parse
        }
      }
    }
  })
})

test('a prompt with no text produces no injection', () => {
  withDataDir((dir) => {
    const { stdout } = runHook('user-prompt-submit.mjs',
      { hook_event_name: 'UserPromptSubmit', session_id: 'S' }, { dataDir: dir })
    assert.equal(stdout, '')
  })
})

test('the default config is on when no config file exists', () => {
  withDataDir((dir) => {
    assert.equal(existsSync(join(dir, 'config.json')), false)
    const { context } = runHook('user-prompt-submit.mjs',
      { hook_event_name: 'UserPromptSubmit', session_id: 'S', prompt: '写一个游戏' }, { dataDir: dir })
    assert.notEqual(context, '', 'router must be enabled by default')
  })
})

// ── data directory resolution ──

test('ZCODE_PLUGIN_DATA wins when the client sets it', async () => {
  const { dataDir } = await import('../hooks/lib/state.mjs')
  assert.equal(dataDir({ ZCODE_PLUGIN_DATA: '/tmp/from-zcode' }), resolve('/tmp/from-zcode'))
  assert.equal(dataDir({ CLAUDE_PLUGIN_DATA: '/tmp/from-claude' }), resolve('/tmp/from-claude'))
  assert.equal(
    dataDir({ ZCODE_PLUGIN_DATA: '/tmp/z', CLAUDE_PLUGIN_DATA: '/tmp/c' }),
    resolve('/tmp/z'),
    'ZCODE wins over CLAUDE',
  )
})

test('the no-env fallback matches the client convention (regression)', async () => {
  // ZCode stores per-plugin data at ~/.zcode/cli/plugins/data/<name>@<marketplace>/.
  // The fallback must agree, or a manual run reads and writes a DIFFERENT config
  // than the running client — which silently ignores the user's settings.
  const { dataDir } = await import('../hooks/lib/state.mjs')
  assert.equal(
    dataDir({}),
    join(homedir(), '.zcode', 'cli', 'plugins', 'data', 'dsh-router@local-dsh-router'),
  )
  assert.ok(
    !dataDir({}).includes(join('.zcode', 'plugin-data')),
    'must not use the ~/.zcode/plugin-data convention, which the client does not read for this plugin',
  )
})

test('the documented config path is the one the hooks actually read (regression)', async () => {
  // Guards the user-facing failure where a documented path is not the path the
  // code uses, so editing the documented file has no effect.
  const { readdirSync } = await import('node:fs')
  const docFiles = ['README.md', 'skills/router-mode/SKILL.md', 'commands/router-status.md']
  for (const rel of docFiles) {
    const text = readFileSync(join(ROOT, rel), 'utf8')
    if (!text.includes('config.json')) continue
    assert.ok(
      text.includes('cli/plugins/data/') || text.includes('ZCODE_PLUGIN_DATA'),
      `${rel} documents config.json but not the plugin data directory the hooks read from`,
    )
    assert.ok(
      !/\$?\{?HOME\}?\/\.zcode\/plugin-data\/dsh-router/.test(text) && !text.includes('~/.zcode/plugin-data/dsh-router'),
      `${rel} still points at the wrong data directory`,
    )
  }
})

test('the plugin never writes outside its data directory', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'dsh-router-test-'))
  try {
    runHook('user-prompt-submit.mjs',
      { hook_event_name: 'UserPromptSubmit', session_id: 'S', prompt: '写一个游戏' }, { dataDir: dir })
    assert.ok(existsSync(join(dir, 'sessions', 'S.json')), 'state lives under the data dir')
    // The only artefact besides config.json (absent here) is sessions/<id>.json.
    assert.deepEqual(readdirSync(dir), ['sessions'])
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }
})
