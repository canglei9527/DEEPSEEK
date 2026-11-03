// test/server.test.mjs
//
// Integration tests for the dual-model-gui server.
// Run from tools/dual-model-gui:  `node --test test/`
// Starts the server in-process via `startServer` against isolated temp
// fixtures; it never touches the real ~/.dsh/settings.yaml. Depends only on
// node:test / node:assert and the already-vendored "yaml" package.

import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import {
  mkdtemp, writeFile, readFile, readdir, rm, stat,
} from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const { startServer } = await import('../server.mjs');
const { parseDocument } = await import('yaml');

function fixtureText() {
  return [
    '# 顶部注释行 - do not touch',
    'ui-onboarding:',
    '  welcomeNoticeVersion: 2026-08-13.1',
    'llm-pi-ai:',
    '  providers:',
    '    opencode-go:',
    '      apiKeyEnv: OPENCODE_TEST_KEY',
    'agent-presets:',
    '  default: arid-dualmodel',
    'agent-default-model:',
    '  provider: opencode-go',
    '  model: deepseek-v4-flash',
    '  reasoningEffort: max',
    'arid-dual-model:',
    '  # executor section comment',
    '  executor:',
    '    provider: opencode-go',
    '    model: deepseek-v4-flash',
    'custom-user-section:',
    '  keepMe: true',
    '  nested:',
    '    note: 用户手动加的无关配置',
    '',
  ].join('\n');
}

let tmpDir;
let settingsPath;
let backupDir;
let serverInst;
let base;

before(async () => {
  tmpDir = await mkdtemp(path.join(os.tmpdir(), 'dmg-test-'));
  settingsPath = path.join(tmpDir, 'settings.yaml');
  backupDir = path.join(tmpDir, 'settings-backups');
  await writeFile(settingsPath, fixtureText(), 'utf8');

  serverInst = await startServer({ settings: settingsPath, port: 0, backupDir });
  base = `http://127.0.0.1:${serverInst.actualPort}`;
  // wait until the server actually accepts connections
  for (let i = 0; i < 50; i++) {
    try {
      const r = await fetch(`${base}/api/health`);
      if (r.ok) break;
    } catch { /* retry */ }
    await new Promise((res) => setTimeout(res, 100));
  }
});

after(async () => {
  if (serverInst) await serverInst.close();
  if (tmpDir) await rm(tmpDir, { recursive: true, force: true });
});

async function fileExists(p) {
  try { await stat(p); return true; } catch { return false; }
}

async function readBackupNames() {
  try { return (await readdir(backupDir)).sort(); } catch { return []; }
}

// --- (a) GET /api/state + initial backup creation -------------------------
test('a) state reports executor/providers/planner and creates initial backup', async () => {
  const res = await fetch(`${base}/api/state`);
  const json = await res.json();
  assert.equal(res.status, 200);
  assert.equal(json.executor.provider, 'opencode-go');
  assert.equal(json.executor.model, 'deepseek-v4-flash');
  assert.deepEqual(json.providers, ['opencode-go']);
  assert.equal(json.planner.provider, 'opencode-go');
  assert.equal(json.planner.model, 'deepseek-v4-flash');
  assert.equal(json.defaults.provider, 'opencode-go');
  assert.equal(json.defaults.model, 'deepseek-v4-flash');
  const initial = json.backups.find((b) => b.kind === 'initial');
  assert.ok(initial, 'an initial backup should exist after first state read');
  assert.equal(await readFile(path.join(backupDir, initial.file), 'utf8'), fixtureText());
});

// --- (b) PUT changes model, preserves comments/bytes ----------------------
test('b) PUT updates executor.model preserving comments & unrelated sections', async () => {
  const before = await readFile(settingsPath, 'utf8');
  const res = await fetch(`${base}/api/executor`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ provider: 'opencode-go', model: 'deepseek-v4.5-test' }),
  });
  const r = await res.json();
  assert.equal(res.status, 200);
  assert.equal(r.ok, true);
  assert.equal(r.changed, true);
  assert.ok(r.backup && r.backup.file && r.backup.kind === 'auto');

  const after = await readFile(settingsPath, 'utf8');
  // leaf-diff: only the executor's model value changed (4-space indent under
  // arid-dual-model.executor), not agent-default-model.model or comments.
  const expected = before.replace('    model: deepseek-v4-flash', '    model: deepseek-v4.5-test');
  assert.equal(after, expected, 'only the executor.model leaf should change');

  const doc = parseDocument(after);
  assert.equal(doc.errors.length, 0, 'patched file must remain parseable');
  assert.equal(doc.toJS()['arid-dual-model'].executor.model, 'deepseek-v4.5-test');
  assert.equal(doc.toJS()['arid-dual-model'].executor.provider, 'opencode-go');
});

// --- (c) PUT same value -> no change, no new backup, mtime unchanged ------
test('c) PUT with identical value is a no-op', async () => {
  const st1 = await stat(settingsPath);
  const backupsBefore = (await readBackupNames()).length;
  const res = await fetch(`${base}/api/executor`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ provider: 'opencode-go', model: 'deepseek-v4.5-test' }),
  });
  const r = await res.json();
  assert.equal(res.status, 200);
  assert.equal(r.ok, true);
  assert.equal(r.changed, false);
  assert.equal(r.backup, null);
  const st2 = await stat(settingsPath);
  assert.equal(st2.mtimeMs, st1.mtimeMs, 'mtime should be unchanged');
  assert.equal((await readBackupNames()).length, backupsBefore, 'no new backup');
});

// --- (d) PUT empty provider/model -> 400 ----------------------------------
test('d) PUT with empty provider/model returns 400', async () => {
  for (const body of [{ provider: '', model: 'x' }, { provider: 'p', model: '  ' }, {}]) {
    const res = await fetch(`${base}/api/executor`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    assert.equal(res.status, 400, `400 for ${JSON.stringify(body)}`);
  }
});

// --- (e) restore to initial backup -----------------------------------------
test('e) restore resets content and takes a fresh auto backup', async () => {
  const initial = (await readBackupNames()).find((n) => n.startsWith('settings-initial-'));
  assert.ok(initial);
  const backupsBefore = (await readBackupNames()).length;

  const res = await fetch(`${base}/api/restore`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ file: initial }),
  });
  const r = await res.json();
  assert.equal(res.status, 200);
  assert.equal(r.ok, true);
  assert.equal(r.backup.kind, 'auto', 'restore must auto-backup first');

  const backupNames = await readBackupNames();
  assert.equal(backupNames.length, backupsBefore + 1);
  assert.ok(backupNames.some((n) => n.startsWith('settings-') && !n.includes('initial') && !n.includes('manual')));

  assert.equal(
    await readFile(settingsPath, 'utf8'),
    await readFile(path.join(backupDir, initial), 'utf8'),
  );
});

// --- (f) path traversal attempts -> 404 -----------------------------------
test('f) path traversal in backup name is rejected', async () => {
  for (const q of [
    'file=..%2F..%2Fsettings.yaml',
    'file=..%2F..%2F..%2Fetc%2Fpasswd',
    'file=settings-initial-19990101-000000.yaml', // nonexistent
  ]) {
    const res = await fetch(`${base}/api/backup?${q}`);
    assert.equal(res.status, 404, `404 for ${q}`);
  }
  const res2 = await fetch(`${base}/api/restore`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ file: '../../settings.yaml' }),
  });
  assert.equal(res2.status, 404);
});

// --- (g) DELETE backup (manual cleans up; initial warns) -------------------
test('g) DELETE backup works; deleting initial returns a warning', async () => {
  const mk = await fetch(`${base}/api/backup`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ kind: 'manual' }),
  });
  const mkJson = await mk.json();
  assert.equal(mk.status, 200);
  assert.equal(mkJson.ok, true);
  assert.ok(mkJson.file.startsWith('settings-manual-'));

  const del = await fetch(`${base}/api/backup?file=${encodeURIComponent(mkJson.file)}`, { method: 'DELETE' });
  const delJson = await del.json();
  assert.equal(del.status, 200);
  assert.equal(delJson.ok, true);
  assert.equal(delJson.warning, undefined);
  assert.equal(await fileExists(path.join(backupDir, mkJson.file)), false);

  const initial = (await readBackupNames()).find((n) => n.startsWith('settings-initial-'));
  assert.ok(initial);
  const delInit = await fetch(`${base}/api/backup?file=${encodeURIComponent(initial)}`, { method: 'DELETE' });
  const delInitJson = await delInit.json();
  assert.equal(delInit.status, 200);
  assert.equal(delInitJson.ok, true);
  assert.equal(typeof delInitJson.warning, 'string', 'deleting initial should warn');
});

// --- corrupt YAML smoke (isolated server in-process) -----------------------
test('h) corrupt settings: state ok with nulls, PUT returns 409 without touching file', async () => {
  const dir2 = await mkdtemp(path.join(os.tmpdir(), 'dmg-corrupt-'));
  const s2 = path.join(dir2, 'settings.yaml');
  const b2 = path.join(dir2, 'settings-backups');
  const BAD = 'a: [unclosed\n';
  await writeFile(s2, BAD, 'utf8');

  const inst2 = await startServer({ settings: s2, port: 0, backupDir: b2 });
  const base2 = `http://127.0.0.1:${inst2.actualPort}`;

  const getState = await fetch(`${base2}/api/state`);
  const stateJ = await getState.json();
  assert.equal(getState.status, 200);
  assert.equal(stateJ.executor, null);
  assert.equal(stateJ.providers, null);

  const put = await fetch(`${base2}/api/executor`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ provider: 'opencode-go', model: 'x' }),
  });
  assert.equal(put.status, 409);
  assert.equal(await readFile(s2, 'utf8'), BAD, 'file must remain untouched');

  await inst2.close();
  await rm(dir2, { recursive: true, force: true });
});
