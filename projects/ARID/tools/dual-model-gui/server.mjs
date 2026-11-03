// server.mjs
//
// ARID dual-model execution configuration GUI — HTTP backend.
// Node 20+ runtime, ESM, zero third-party runtime dependencies except the
// already-vendored "yaml" package (imported via `import 'yaml'`; falls back to
// the absolute path of the local node_modules copy). No `npm install` runs.
//
// This server edits the `arid-dual-model.executor` provider/model leaves of the
// user settings document at <~/.dsh>/settings.yaml. It deliberately mirrors the
// writer-protocol of @deepseek-ai/dsh-atomic-write so it does not clobber or
// race an in-flight write by the DSH runtime itself:
//   * writeFileAtomic-like commit: write <file>.<hex>.tmp (flag:'wx', mode 0o600)
//     then fs.rename over the target. Never delete-then-create.
//   * withFileLock-like mutex: acquire <settings.yaml>.lock (wx, mode 0o600,
//     writes `pid\n`), retry on EEXIST with exponential backoff (20ms..200ms,
//     2s deadline), then remove the lock in a finally.
// The DSH settings service (chokidar watcher on settings.yaml, debounceMs 100)
// hot-reloads an externally replaced file within ~100ms, so the DSH runtime
// picks up the new executor model on the next subagent_executor call with no
// restart and no IPC.
//
// Endpoints (all JSON; errors are {ok:false, error:<中文>}):
//   GET  /                    -> index.html (static)
//   GET  /api/health          -> {ok:true}
//   GET  /api/state           -> settingsPath, executor, planner, providers,
//                                defaults, backups (+ auto-create initial backup)
//   PUT  /api/executor        -> {provider,model}; patch arid-dual-model.executor
//   POST /api/backup          -> create manual/initial backup
//   GET  /api/backup?file=..  -> raw text of one backup (path-traversal guarded)
//   POST /api/restore         -> {file}; auto-backup then replace settings
//   DELETE /api/backup?file=  -> delete a backup (path-traversal guarded)
//
// Backup naming:
//   settings-<yyyyMMdd-HHmmss>.yaml        (auto)
//   settings-initial-<yyyyMMdd-HHmmss>.yaml(initial)
//   settings-manual-<yyyyMMdd-HHmmss>.yaml (manual)
// Auto backups are pruned to the 30 most recent auto+manual (by mtime);
// initial backups are never pruned.
//
// Usage:
//   node server.mjs [--settings <path>] [--port <n>] [--backup-dir <path>]
// The server is closure-based: `startServer(opts)` can be called in-process
// (used by the automated test to run against isolated temp fixtures), so the
// test needs no child processes.

import { createServer } from 'node:http';
import {
  readFile, writeFile, rename, rm, mkdir, readdir, stat,
} from 'node:fs/promises';
import { randomBytes } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { dirname, join, resolve, basename, sep } from 'node:path';
import os from 'node:os';

const __dirname = dirname(fileURLToPath(import.meta.url));

// ---------------------------------------------------------------------------
// YAML import — prefer the local installed copy; fall back to absolute path.
// ---------------------------------------------------------------------------
let yaml;
try {
  yaml = await import('yaml');
} catch {
  const entry = join(__dirname, 'node_modules', 'yaml', 'dist', 'index.js');
  const url = 'file:///' + encodeURI(entry).replace(/#/g, '%23').replace(/\?/g, '%3F');
  yaml = await import(url);
}
if (typeof yaml.parseDocument !== 'function') {
  throw new Error('yaml package is unavailable; install or vendor it first.');
}
const { parseDocument } = yaml;

// ---------------------------------------------------------------------------
// DSH-compatible atomic write + file lock.
// ---------------------------------------------------------------------------
async function writeFileAtomic(filename, content) {
  const temp = `${filename}.${randomBytes(6).toString('hex')}.tmp`;
  try {
    await writeFile(temp, content, { mode: 0o600, flag: 'wx' });
    await rename(temp, filename);
  } catch (err) {
    await rm(temp, { force: true });
    throw err;
  }
}

// Same lock-file protocol as @deepseek-ai/dsh-atomic-write's withFileLock.
async function withFileLock(filename, operation) {
  const lockPath = `${filename}.lock`;
  const deadline = Date.now() + 2000;
  let delay = 20;
  for (;;) {
    try {
      await writeFile(lockPath, `${process.pid}\n`, { mode: 0o600, flag: 'wx' });
      break;
    } catch (err) {
      if (err?.code !== 'EEXIST') throw err;
    }
    if (Date.now() >= deadline) {
      throw new Error(`等待写入锁超时（2s）：${lockPath}`);
    }
    await new Promise((r) => setTimeout(r, delay));
    delay = Math.min(delay * 2, 200);
  }
  try {
    return await operation();
  } finally {
    await rm(lockPath, { force: true });
  }
}

// ---------------------------------------------------------------------------
// Small helpers.
// ---------------------------------------------------------------------------
async function pathExists(p) {
  try { await stat(p); return true; } catch { return false; }
}

function sendJson(res, status, obj) {
  const body = JSON.stringify(obj);
  res.writeHead(status, {
    'Content-Type': 'application/json; charset=utf-8',
    'Content-Length': Buffer.byteLength(body),
  });
  res.end(body);
}

function readBody(req, limit = 1024 * 1024) {
  return new Promise((resolveP, reject) => {
    const chunks = [];
    let size = 0;
    req.on('data', (c) => {
      size += c.length;
      if (size > limit) { reject(new Error('请求体过大（上限 1MB）。')); req.destroy(); return; }
      chunks.push(c);
    });
    req.on('end', () => resolveP(Buffer.concat(chunks).toString('utf8')));
    req.on('error', reject);
  });
}

// Validate that `candidate` stays inside `dir` after resolution.
function inDir(dir, candidate) {
  const resolved = resolve(candidate);
  const root = resolve(dir);
  if (resolved !== root && !resolved.startsWith(root + sep)) return null;
  return resolved;
}

function nowStamp(d = new Date()) {
  const p = (n, l = 2) => String(n).padStart(l, '0');
  return `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}`;
}

// ---------------------------------------------------------------------------
// App factory: builds all handlers as closures over {settingsPath, backupDir}.
// ---------------------------------------------------------------------------
function buildApp(ctx) {
  const { settingsPath, backupDir } = ctx;
  const BACKUP_NAME_RE = /^settings(-(initial|manual))?-\d{8}-\d{6}(-\d+)?\.yaml$/;
  const MAX_AUTO_KEEP = 30;

  function classifyBackup(name) {
    if (!BACKUP_NAME_RE.test(name)) return null;
    if (name.startsWith('settings-initial-')) return 'initial';
    if (name.startsWith('settings-manual-')) return 'manual';
    return 'auto';
  }

  async function listBackups() {
    let files = [];
    try { files = await readdir(backupDir); } catch { return []; }
    const out = [];
    for (const name of files) {
      const kind = classifyBackup(name);
      if (!kind) continue;
      let size = 0, mtime = 0;
      try { const st = await stat(join(backupDir, name)); size = st.size; mtime = st.mtimeMs; } catch { continue; }
      out.push({ file: name, name, kind, size, mtime });
    }
    out.sort((a, b) => b.mtime - a.mtime);
    return out;
  }

  function backupName(kind) {
    const tag = kind === 'auto' ? '' : kind === 'initial' ? '-initial' : '-manual';
    return `settings${tag}-${nowStamp()}.yaml`;
  }

  async function writeBackup(kind) {
    await mkdir(backupDir, { recursive: true });
    // Same-second writes must not clobber one another: append -n if taken.
    let name = backupName(kind);
    let n = 1;
    while (await pathExists(join(backupDir, name))) {
      name = `${backupName(kind).replace(/\.yaml$/, '')}-${n}.yaml`;
      n += 1;
    }
    const dest = join(backupDir, name);
    const data = await readFile(settingsPath);
    await writeFile(dest, data);
    const st = await stat(dest);
    return { file: name, kind, size: st.size, mtime: st.mtimeMs };
  }

  async function pruneAuto() {
    const pruneable = (await listBackups())
      .filter((b) => b.kind !== 'initial')
      .sort((a, b) => b.mtime - a.mtime);
    if (pruneable.length <= MAX_AUTO_KEEP) return;
    for (const b of pruneable.slice(MAX_AUTO_KEEP)) {
      try { await rm(join(backupDir, b.file), { force: true }); } catch { /* ignore */ }
    }
  }

  async function ensureInitialBackup() {
    if ((await listBackups()).length > 0) return null;
    if (!(await pathExists(settingsPath))) return null;
    return writeBackup('initial');
  }

  async function readSettingsText() {
    return readFile(settingsPath, 'utf8');
  }

  function parseDocOrThrow(text) {
    const doc = parseDocument(text, { prettyErrors: true });
    if (doc.errors.length > 0) {
      const msg = doc.errors.map((e) => `${e.code || e.message}`).join('; ');
      const err = new Error(`settings.yaml 解析失败：${msg}`);
      err.statusCode = 409;
      throw err;
    }
    const j = doc.toJS();
    if (j !== null && (typeof j !== 'object' || Array.isArray(j))) {
      const err = new Error('settings.yaml 根节点必须是键值映射。');
      err.statusCode = 409;
      throw err;
    }
    return doc;
  }

  // Leaf-level patch of only the two executor leaves.
  function patchExecutor(doc, provider, model) {
    const root = doc.toJS() ?? {};
    const curProv = root?.['arid-dual-model']?.['executor']?.provider;
    const curModel = root?.['arid-dual-model']?.['executor']?.model;
    const changed = curProv !== provider || curModel !== model;
    if (!changed) return { changed: false };
    const hasSection = root['arid-dual-model'] !== undefined;
    if (!hasSection || root['arid-dual-model']['executor'] === undefined) {
      doc.setIn(['arid-dual-model', 'executor'], { provider, model });
    } else {
      doc.setIn(['arid-dual-model', 'executor', 'provider'], provider);
      doc.setIn(['arid-dual-model', 'executor', 'model'], model);
    }
    return { changed: true };
  }

  function readExecutor(root) {
    const e = root?.['arid-dual-model']?.['executor'];
    if (!e || typeof e !== 'object') return null;
    return {
      provider: typeof e.provider === 'string' ? e.provider : '',
      model: typeof e.model === 'string' ? e.model : '',
    };
  }

  function readPlanner(root) {
    const d = root?.['agent-default-model'];
    if (!d || typeof d !== 'object') return null;
    const out = {};
    if (typeof d.provider === 'string') out.provider = d.provider;
    if (typeof d.model === 'string') out.model = d.model;
    if (typeof d.reasoningEffort === 'string') out.reasoningEffort = d.reasoningEffort;
    return Object.keys(out).length ? out : null;
  }

  function readProviders(root) {
    const p = root?.['llm-pi-ai']?.['providers'];
    if (!p || typeof p !== 'object' || Array.isArray(p)) return null;
    return Object.keys(p);
  }

  // ---- handlers ----
  async function handleState() {
    let executor = null, planner = null, providers = null;
    if (await pathExists(settingsPath)) {
      try {
        const doc = parseDocOrThrow(await readSettingsText());
        const root = doc.toJS() ?? {};
        executor = readExecutor(root);
        planner = readPlanner(root);
        providers = readProviders(root);
      } catch {
        executor = null; planner = null; providers = null;
      }
    }
    await ensureInitialBackup();
    const backups = (await listBackups()).map((b) => ({
      file: b.file, name: b.file, kind: b.kind, size: b.size, mtime: b.mtime,
    }));
    return {
      settingsPath,
      executor,
      planner,
      providers,
      defaults: { provider: 'opencode-go', model: 'deepseek-v4-flash' },
      backups,
    };
  }

  async function handlePutExecutor(body) {
    const provider = typeof body?.provider === 'string' ? body.provider.trim() : '';
    const model = typeof body?.model === 'string' ? body.model.trim() : '';
    if (!provider) { const e = new Error('provider 必须是非空字符串。'); e.statusCode = 400; throw e; }
    if (!model) { const e = new Error('model 必须是非空字符串。'); e.statusCode = 400; throw e; }

    return withFileLock(settingsPath, async () => {
      if (!(await pathExists(settingsPath))) {
        const e = new Error('settings.yaml 不存在。'); e.statusCode = 404; throw e;
      }
      const doc = parseDocOrThrow(await readSettingsText()); // 409 on bad yaml, file untouched
      const { changed } = patchExecutor(doc, provider, model);
      if (!changed) return { ok: true, changed: false, backup: null };
      const backup = await writeBackup('auto');
      await pruneAuto();
      await writeFileAtomic(settingsPath, doc.toString());
      return { ok: true, changed: true, backup };
    });
  }

  async function handlePostBackup(body) {
    const kind = body?.kind === 'initial' || body?.kind === 'manual' ? body.kind : 'manual';
    if (!(await pathExists(settingsPath))) {
      const e = new Error('settings.yaml 不存在。'); e.statusCode = 404; throw e;
    }
    const backup = await writeBackup(kind);
    return { ok: true, file: backup.file };
  }

  async function resolveBackupFile(name) {
    if (!name) return null;
    const base = basename(name);
    if (base.includes('..') || base.includes('\\') || base.includes('/')) return null;
    const resolved = inDir(backupDir, join(backupDir, base));
    if (!resolved) return null;
    const kind = classifyBackup(base);
    if (!kind) return null;
    if (!(await pathExists(resolved))) return null;
    return { path: resolved, name: base, kind };
  }

  async function handleGetBackup(name) {
    const b = await resolveBackupFile(name);
    if (!b) { const e = new Error('备份文件未找到或路径非法。'); e.statusCode = 404; throw e; }
    return { content: await readFile(b.path, 'utf8') };
  }

  async function handleRestore(body) {
    const file = typeof body?.file === 'string' ? body.file : '';
    return withFileLock(settingsPath, async () => {
      const b = await resolveBackupFile(file);
      if (!b) { const e = new Error('备份文件未找到或路径非法。'); e.statusCode = 404; throw e; }
      if (!(await pathExists(settingsPath))) {
        const e = new Error('settings.yaml 不存在。'); e.statusCode = 404; throw e;
      }
      const backup = await writeBackup('auto'); // auto-backup current first
      await pruneAuto();
      const content = await readFile(b.path, 'utf8');
      parseDocOrThrow(content); // validate before overwriting
      await writeFileAtomic(settingsPath, content);
      return { ok: true, backup, restored: b.name };
    });
  }

  async function handleDeleteBackup(name) {
    const b = await resolveBackupFile(name);
    if (!b) { const e = new Error('备份文件未找到或路径非法。'); e.statusCode = 404; throw e; }
    await rm(b.path, { force: true });
    return {
      ok: true,
      warning: b.kind === 'initial'
        ? '你删除的是“初始”备份——它通常是唯一包含首次启动时配置的基线，删除后无法再一键恢复该版本。'
        : undefined,
    };
  }

  const server = createServer(async (req, res) => {
    const url = new URL(req.url, 'http://127.0.0.1');
    const pathname = url.pathname;
    try {
      if (req.method === 'GET' && pathname === '/') {
        const html = await readFile(join(__dirname, 'index.html'), 'utf8');
        res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
        res.end(html);
        return;
      }
      if (req.method === 'GET' && pathname === '/api/health') { sendJson(res, 200, { ok: true }); return; }
      if (req.method === 'GET' && pathname === '/api/state') { sendJson(res, 200, await handleState()); return; }
      if (req.method === 'PUT' && pathname === '/api/executor') {
        let body;
        try { body = await parseJsonBody(req); } catch (e) { sendJson(res, 400, { ok: false, error: e.message }); return; }
        sendJson(res, 200, await handlePutExecutor(body));
        return;
      }
      if (req.method === 'POST' && pathname === '/api/backup') {
        let body = {};
        try { body = (await parseJsonBody(req)) || {}; } catch (e) { sendJson(res, 400, { ok: false, error: e.message }); return; }
        sendJson(res, 200, await handlePostBackup(body));
        return;
      }
      if (req.method === 'GET' && pathname === '/api/backup') {
        const r = await handleGetBackup(url.searchParams.get('file') || '');
        res.writeHead(200, { 'Content-Type': 'text/plain; charset=utf-8' });
        res.end(r.content);
        return;
      }
      if (req.method === 'POST' && pathname === '/api/restore') {
        let body;
        try { body = await parseJsonBody(req); } catch (e) { sendJson(res, 400, { ok: false, error: e.message }); return; }
        sendJson(res, 200, await handleRestore(body));
        return;
      }
      if (req.method === 'DELETE' && pathname === '/api/backup') {
        sendJson(res, 200, await handleDeleteBackup(url.searchParams.get('file') || ''));
        return;
      }
      res.writeHead(404, { 'Content-Type': 'text/plain; charset=utf-8' });
      res.end('Not Found');
    } catch (err) {
      const status = typeof err.statusCode === 'number' ? err.statusCode : 500;
      sendJson(res, status, { ok: false, error: err.message || String(err) });
    }
  });

  return { server, ensureInitialBackup };
}

async function parseJsonBody(req) {
  const raw = await readBody(req);
  try { return JSON.parse(raw); } catch { throw new Error('请求体不是合法的 JSON。'); }
}

// ---------------------------------------------------------------------------
// startServer: options {settings, port, backupDir}. Listens on 127.0.0.1.
// Resolves when listening; returns {server, port, settings, backupDir, close}.
// ---------------------------------------------------------------------------
async function startServer(opts = {}) {
  const settings = resolve(opts.settings ?? join(os.homedir(), '.dsh', 'settings.yaml'));
  const backupDir = opts.backupDir
    ? resolve(opts.backupDir)
    : join(dirname(settings), 'settings-backups');
  const app = buildApp({ settingsPath: settings, backupDir });
  const server = app.server;
  const port = await new Promise((resolveP, reject) => {
    server.once('error', reject);
    server.listen(opts.port ?? 3090, '127.0.0.1', () => {
      const addr = server.address();
      resolveP(typeof addr === 'object' && addr ? addr.port : (opts.port ?? 3090));
    });
  });
  return {
    server,
    port,
    actualPort: port,
    settings,
    backupDir,
    close: () => new Promise((res) => server.close(res)),
  };
}

// ---------------------------------------------------------------------------
// CLI entry (only when run as the main module).
// ---------------------------------------------------------------------------
function isMain(metaUrl) {
  return process.argv[1] && metaUrl === import.meta.url;
}

if (isMain()) {
  // minimal parse of the documented CLI flags
  const argv = process.argv.slice(2);
  const flags = { settings: undefined, port: 3090, backupDir: undefined };
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === '--settings') flags.settings = argv[++i];
    else if (argv[i] === '--port') flags.port = parseInt(argv[i], 10);
    else if (argv[i] === '--backup-dir') flags.backupDir = argv[++i];
  }
  if (Number.isNaN(flags.port)) { console.error('--port 必须是整数'); process.exit(1); }

  const inst = await startServer(flags);
  console.log(`DSH_GUI_PORT=${inst.actualPort}`);
  console.log(`[ARID 双模型执行配置] http://127.0.0.1:${inst.actualPort}`);
  console.log(`[ARID 双模型执行配置] 配置文件: ${inst.settings}`);
  console.log(`[ARID 双模型执行配置] 备份目录: ${inst.backupDir}`);
  try {
    const created = await inst.ensureInitialBackup();
    if (created) console.log(`[ARID 双模型执行配置] 已生成 initial 备份: ${created.file}`);
  } catch (err) {
    console.error(`[ARID 双模型执行配置] initial 备份失败: ${err.message}`);
  }
}

export { startServer };
