import { spawn } from 'node:child_process';
import { readFile } from 'node:fs/promises';
import { extname, isAbsolute, relative, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { normalizeDaiSyncConfig } from './dai-rest-adapter.js';
import { validateSyncInterval } from './dai-sync-core.js';
import { createDaiBrowserSession } from './dai-browser-session.js';

export const DEFAULT_DASHBOARD_CONFIG_PATH = 'dai-sync.config.json';

const MAX_LOG_ENTRIES = 300;
const MAX_EVENT_ENTRIES = 100;
const MAX_LOG_LINE_LENGTH = 2_000;
const DEFAULT_STOP_GRACE_MS = 4_000;
const packagedRuntime = Boolean(process.argv.includes('--server') || process.argv.includes('--worker') || process.pkg || process.isSea || process.argv[1]?.toLowerCase().endsWith('.exe'));
const projectRoot = packagedRuntime ? resolve(process.execPath, '..') : resolve(fileURLToPath(new URL('..', import.meta.url)));
const cliPath = packagedRuntime ? process.execPath : fileURLToPath(new URL('../bin/dai-group-sync.mjs', import.meta.url));

function errorMessage(error) {
  return error instanceof Error ? error.message : String(error);
}

function copyJson(value) {
  return value === undefined ? undefined : JSON.parse(JSON.stringify(value));
}

function trimCollection(items, limit) {
  return items.slice(-limit);
}

function redactLogLine(value, secretValues = []) {
  let line = String(value)
    .replace(/[\r\n]+/g, ' ')
    .replace(/\b(Bearer|Basic)\s+[^\s,;]+/gi, '$1 [REDACTED]')
    .replace(/\b(authorization)\s*[:=]\s*[^\s,;]+/gi, '$1: [REDACTED]');
  for (const secret of secretValues) {
    if (typeof secret !== 'string' || secret.length < 3) continue;
    line = line.split(secret).join('[REDACTED]');
  }
  return line.slice(0, MAX_LOG_LINE_LENGTH);
}

function resolveConfigFile(root, configPath) {
  if (typeof configPath !== 'string' || !configPath.trim()) {
    throw new Error('请提供项目目录内的同步配置 JSON 文件名');
  }
  const absolutePath = resolve(root, configPath.trim());
  const relativePath = relative(root, absolutePath);
  if (
    !relativePath
    || isAbsolute(relativePath)
    || relativePath === '..'
    || relativePath.startsWith(`..${sep}`)
  ) {
    throw new Error('同步配置必须位于项目目录内');
  }
  if (extname(absolutePath).toLowerCase() !== '.json') {
    throw new Error('同步配置必须是 .json 文件');
  }
  return { absolutePath, relativePath };
}

function environmentNameFromReference(value) {
  if (typeof value === 'string') {
    const match = value.match(/^env:([A-Za-z_][A-Za-z0-9_]*)$/);
    return match?.[1] ?? null;
  }
  if (value && typeof value === 'object' && !Array.isArray(value) && typeof value.env === 'string') {
    return value.env;
  }
  return null;
}

function listEnvironmentRequirements(headers, environment) {
  if (!headers || typeof headers !== 'object' || Array.isArray(headers)) return [];
  const requirements = [];
  for (const value of Object.values(headers)) {
    const name = environmentNameFromReference(value);
    if (!name || requirements.some((item) => item.name === name)) continue;
    requirements.push({
      name,
      available: typeof environment[name] === 'string' && environment[name].length > 0
    });
  }
  return requirements;
}

function makeSafeConfigSummary({ relativePath, config, intervalMs, environment }) {
  return {
    configPath: relativePath,
    baseUrl: config.baseUrl,
    allowApply: config.allowApply,
    allowLifecycleApply: config.allowLifecycleApply,
    allowDisableStale: config.allowDisableStale,
    allowEnableRestored: config.allowEnableRestored,
    intervalMs,
    decimals: config.decimals,
    adjustment: config.adjustment,
    tolerance: config.tolerance,
    browser: config.browser
      ? {
        loginUrl: config.browser.loginUrl,
        requestPageUrl: config.browser.requestPageUrl,
        executablePath: config.browser.executablePath,
        storagePath: config.browser.storagePath
      }
      : null,
    requiredEnvironment: listEnvironmentRequirements(config.headers, environment)
  };
}

function parseCycleSummary(line) {
  const rateMatch = line.match(/^第\s+(\d+)\s+轮\s+\[(dry-run|apply)]\s+上游=(\d+)\s+分组=(\d+)\s+匹配=(\d+)\s+一致=(\d+)\s+待更新=(\d+)\s+跳过=(\d+)(?:\s+已更新=(\d+)\s+尝试=(\d+))?/);
  if (rateMatch) {
    const [, cycle, mode, upstreamCount, groupCount, matched, unchanged, pending, skipped, updated, attempted] = rateMatch;
    return {
      type: 'rate',
      cycle: Number(cycle),
      mode,
      upstreamCount: Number(upstreamCount),
      groupCount: Number(groupCount),
      matched: Number(matched),
      unchanged: Number(unchanged),
      pending: Number(pending),
      skipped: Number(skipped),
      ...(updated === undefined ? {} : { updated: Number(updated), attempted: Number(attempted) })
    };
  }
  const lifecycleMatch = line.match(/^第\s+(\d+)\s+轮\s+\[(dry-run|apply)]\s+创建分组=(\d+)\s+关联上游=(\d+)\s+创建规则=(\d+)\s+更新倍率=(\d+)\s+待恢复规则=(\d+)\s+待恢复分组=(\d+)\s+待停用分组=(\d+)\s+跳过=(\d+)(?:\s+已完成=(\d+)\s+尝试=(\d+))?/);
  const legacyLifecycleMatch = line.match(/^第\s+(\d+)\s+轮\s+\[(dry-run|apply)]\s+创建分组=(\d+)\s+关联上游=(\d+)\s+创建规则=(\d+)\s+待停用规则=(\d+)\s+待停用分组=(\d+)\s+跳过=(\d+)(?:\s+已完成=(\d+)\s+尝试=(\d+))?/);
  if (!lifecycleMatch && !legacyLifecycleMatch) return null;
  if (legacyLifecycleMatch) {
    const [, cycle, mode, createGroups, associateUpstreams, createRules, disableRules, disableGroups, skipped, completed, attempted] = legacyLifecycleMatch;
    return {
      type: 'lifecycle',
      cycle: Number(cycle),
      mode,
      createGroups: Number(createGroups),
      associateUpstreams: Number(associateUpstreams),
      createRules: Number(createRules),
      updates: 0,
      enableRules: 0,
      enableGroups: 0,
      disableRules: 0,
      disableGroups: Number(disableGroups),
      pending: Number(createGroups) + Number(associateUpstreams) + Number(createRules) + Number(disableGroups),
      skipped: Number(skipped),
      ...(completed === undefined ? {} : { completed: Number(completed), attempted: Number(attempted) })
    };
  }
  const [, cycle, mode, createGroups, associateUpstreams, createRules, updates, enableRules, enableGroups, disableGroups, skipped, completed, attempted] = lifecycleMatch;
  return {
    type: 'lifecycle',
    cycle: Number(cycle),
    mode,
    createGroups: Number(createGroups),
    associateUpstreams: Number(associateUpstreams),
    createRules: Number(createRules),
    updates: Number(updates),
    enableRules: Number(enableRules),
    enableGroups: Number(enableGroups),
    disableRules: 0,
    disableGroups: Number(disableGroups),
    pending: Number(updates) + Number(createGroups) + Number(associateUpstreams) + Number(createRules) + Number(enableRules) + Number(enableGroups) + Number(disableGroups),
    skipped: Number(skipped),
    ...(completed === undefined ? {} : { completed: Number(completed), attempted: Number(attempted) })
  };
}

function parseMismatch(line) {
  const match = line.match(/^\[(?:预览|待更新)]\s+("(?:\\.|[^"\\])*")\s+×([^\s]+)\s+->\s+×([^\s]+)$/);
  if (!match) return null;
  try {
    const name = JSON.parse(match[1]);
    if (typeof name !== 'string') return null;
    return { name, currentMultiplier: match[2], desiredMultiplier: match[3] };
  } catch {
    return null;
  }
}

function parseSkipped(line) {
  const match = line.match(/^\[(?:生命周期)?跳过]\s+([a-z0-9-]+)(?:[:：]\s*(.*?))?(?:（(.*)）)?$/i);
  if (!match) return null;
  return {
    code: match[1],
    ...(match[2] ? { detail: match[2] } : {}),
    ...(match[3] ? { message: match[3] } : {})
  };
}

function parseLifecycleAction(line) {
  const match = line.match(/^\[生命周期(?:预览|待执行|已完成)]\s+(创建分组|关联上游|创建规则|更新倍率|恢复规则|恢复分组|停用规则|停用分组|create-group|associate-upstream|create-rule|update-group|enable-rule|enable-group|disable-rule|disable-group)\s+("(?:\\.|[^"\\])*")(?:\s+模型=("(?:\\.|[^"\\])*"))?$/);
  if (!match) return null;
  try {
    return {
      action: match[1],
      name: JSON.parse(match[2]),
      ...(match[3] ? { modelCode: JSON.parse(match[3]) } : {})
    };
  } catch {
    return null;
  }
}

function delay(milliseconds) {
  return new Promise((resolvePromise) => setTimeout(resolvePromise, milliseconds));
}

/**
 * Owns one local child process running the existing safe CLI. It never resolves
 * or returns credential values: config validation only reports env-var names and presence.
 */
export class DaiSyncController {
  constructor({
    root = projectRoot,
    cli = cliPath,
    nodeExecutable = process.execPath,
    spawnImpl = spawn,
    readFileImpl = readFile,
    environment = process.env,
    now = () => new Date(),
    stopGraceMs = DEFAULT_STOP_GRACE_MS
  } = {}) {
    this.root = resolve(root);
    this.cli = cli;
    this.nodeExecutable = nodeExecutable;
    this.spawnImpl = spawnImpl;
    this.readFileImpl = readFileImpl;
    this.environment = environment;
    this.now = now;
    this.stopGraceMs = stopGraceMs;

    this.child = null;
    this.state = 'stopped';
    this.activity = null;
    this.mode = null;
    this.intervalMs = null;
    this.startedAt = null;
    this.lastCycle = null;
    this.lastExit = null;
    this.config = null;
    this.logs = [];
    this.mismatches = [];
    this.skipped = [];
    this.lifecycleActions = [];
    this.pendingMismatches = [];
    this.pendingSkipped = [];
    this.pendingLifecycleActions = [];
    this.lineBuffers = { stdout: '', stderr: '' };
    this.exitPromise = null;
    this.resolveExit = null;
    this.stopTask = null;
    this.starting = false;
    this.secretValues = [];
  }

  getStatus() {
    const currentMismatches = this.pendingMismatches.length ? this.pendingMismatches : this.mismatches;
    const currentSkipped = this.pendingSkipped.length ? this.pendingSkipped : this.skipped;
    const currentLifecycleActions = this.pendingLifecycleActions.length ? this.pendingLifecycleActions : this.lifecycleActions;
    return copyJson({
      state: this.state,
      activity: this.activity,
      mode: this.mode,
      lifecycle: this.lifecycle === true,
      disableStale: this.disableStale === true,
      enableRestored: this.enableRestored === true,
      intervalMs: this.intervalMs,
      startedAt: this.startedAt,
      lastCycle: this.lastCycle,
      lastExit: this.lastExit,
      config: this.config,
      mismatches: currentMismatches,
      lifecycleActions: currentLifecycleActions,
      skipped: currentSkipped,
      logs: this.logs
    });
  }

  async validateConfig(request = {}) {
    const loaded = await this.loadConfig(request);
    this.config = loaded.summary;
    return { config: copyJson(loaded.summary), status: this.getStatus() };
  }

  async start(request = {}) {
    this.assertIdle();
    this.starting = true;
    try {
      return await this.startInternal(request);
    } finally {
      this.starting = false;
    }
  }

  async startInternal(request = {}) {
    const apply = request.apply === true;
    const lifecycle = request.lifecycle === true;
    const disableStale = request.disableStale === true;
    const enableRestored = request.enableRestored === true;
    const loaded = await this.loadConfig(request);
    if (loaded.config.browser) this.assertSavedBrowserSession(loaded.config.browser);
    if (disableStale && !lifecycle) {
      throw new Error('参数错误：停用失效模型必须先选择“供应商模型生命周期同步”');
    }
    if (disableStale && !apply) {
      throw new Error('参数错误：停用失效模型必须将监视器模式切换为 apply');
    }
    if (apply && !loaded.config.allowApply) {
      throw new Error('安全拦截：配置中的 allowApply 必须明确为 true 才能启动 apply');
    }
    if (lifecycle && apply && !loaded.config.allowLifecycleApply) {
      throw new Error('安全拦截：配置中的 allowLifecycleApply 必须明确为 true 才能启动生命周期 apply');
    }
    if (disableStale && !loaded.config.allowDisableStale) {
      throw new Error('安全拦截：配置中的 allowDisableStale 必须明确为 true 才能停用失效模型');
    }
      if (enableRestored && (!lifecycle || !apply)) {
        throw new Error('参数错误：恢复已识别资源必须先选择生命周期 apply');
      }
      if (enableRestored && !loaded.config.allowEnableRestored) {
        throw new Error('安全拦截：配置中的 allowEnableRestored 必须明确为 true 才能恢复已识别资源');
      }
    return this.launch(loaded, { activity: 'watch', apply, lifecycle, disableStale, enableRestored });
  }

  async preview(request = {}) {
    this.assertIdle();
    this.starting = true;
    try {
      const lifecycle = request.lifecycle === true;
      const disableStale = request.disableStale === true;
      const loaded = await this.loadConfig(request);
      if (loaded.config.browser) this.assertSavedBrowserSession(loaded.config.browser);
      if (disableStale && !lifecycle) {
        throw new Error('参数错误：预览失效模型需要选择生命周期模式');
      }
      return this.launch(loaded, { activity: 'preview', apply: false, lifecycle, disableStale: false });
    } finally {
      this.starting = false;
    }
  }

  assertSavedBrowserSession(browserConfig) {
    const session = createDaiBrowserSession({ root: this.root });
    const status = session.configure(browserConfig);
    if (!status.session.saved) {
      throw new Error('浏览器登录会话尚未保存；请先打开 D-AI 登录并在完成登录后保存会话');
    }
  }

  async stop() {
    if (!this.child) return this.getStatus();
    if (this.stopTask) return this.stopTask;
    const child = this.child;
    this.stopTask = this.stopChild(child).finally(() => {
      this.stopTask = null;
    });
    return this.stopTask;
  }

  assertIdle() {
    if (this.starting || this.child || this.state === 'running' || this.state === 'stopping') {
      throw new Error('同步器已经在运行；请先停止当前任务');
    }
  }

  async loadConfig({ configPath = DEFAULT_DASHBOARD_CONFIG_PATH, intervalMs, adjustment } = {}) {
    const location = resolveConfigFile(this.root, configPath);
    let parsed;
    try {
      parsed = JSON.parse(await this.readFileImpl(location.absolutePath, 'utf8'));
    } catch (error) {
      if (error instanceof SyntaxError) {
        throw new Error('同步配置不是有效 JSON');
      }
      throw new Error('无法读取本地同步配置；请确认文件位于项目目录内');
    }

    let config;
    try {
      config = normalizeDaiSyncConfig(parsed);
    } catch (error) {
      throw new Error(`同步配置校验失败：${errorMessage(error)}`);
    }
    let effectiveIntervalMs = config.intervalMs;
    if (adjustment !== undefined) {
      if (!Number.isFinite(adjustment) || adjustment < 0 || adjustment > 1) {
        throw new Error('调整值必须是 0 到 1 之间的有限数字');
      }
      config.adjustment = adjustment;
    }
    if (intervalMs !== undefined) {
      if (!Number.isInteger(intervalMs)) {
        throw new Error('轮询间隔必须是不小于 1000 毫秒的整数');
      }
      effectiveIntervalMs = validateSyncInterval(intervalMs);
    }
    return {
      absolutePath: location.absolutePath,
      config,
      intervalMs: effectiveIntervalMs,
      summary: makeSafeConfigSummary({
        relativePath: location.relativePath,
        config,
        intervalMs: effectiveIntervalMs,
        environment: this.environment
      })
    };
  }

  launch(loaded, { activity, apply, lifecycle = false, disableStale = false, enableRestored = false }) {
    const args = packagedRuntime
      ? ['--worker', '--config', loaded.absolutePath, activity === 'watch' ? '--watch' : '--once', apply ? '--apply' : '--dry-run', '--interval-ms', String(loaded.intervalMs), '--adjustment', String(loaded.config.adjustment), ...(lifecycle ? ['--lifecycle'] : []), ...(disableStale ? ['--disable-stale'] : []), ...(enableRestored ? ['--enable-restored'] : [])]
      : [
        this.cli,
        '--config', loaded.absolutePath,
        activity === 'watch' ? '--watch' : '--once',
        apply ? '--apply' : '--dry-run',
        '--interval-ms', String(loaded.intervalMs),
        '--adjustment', String(loaded.config.adjustment),
        ...(lifecycle ? ['--lifecycle'] : []),
        ...(disableStale ? ['--disable-stale'] : []),
        ...(enableRestored ? ['--enable-restored'] : [])
      ];
    let child;
    try {
      child = this.spawnImpl(this.nodeExecutable, args, {
        cwd: this.root,
        env: this.environment,
        shell: false,
        stdio: ['ignore', 'pipe', 'pipe'],
        windowsHide: true
      });
    } catch (error) {
      throw new Error(`无法启动本地同步进程：${errorMessage(error)}`);
    }
    if (!child || typeof child.once !== 'function') {
      throw new Error('无法启动本地同步进程');
    }

    this.child = child;
    this.state = 'running';
    this.activity = activity;
    this.mode = apply ? 'apply' : 'dry-run';
    this.lifecycle = lifecycle;
    this.disableStale = disableStale;
    this.enableRestored = enableRestored;
    this.intervalMs = loaded.intervalMs;
    this.startedAt = this.now().toISOString();
    this.lastExit = null;
    this.config = loaded.summary;
    this.secretValues = listEnvironmentRequirements(loaded.config.headers, this.environment)
      .map((item) => this.environment[item.name])
      .filter((value) => typeof value === 'string' && value.length > 2);
    this.mismatches = [];
    this.skipped = [];
    this.lifecycleActions = [];
    this.pendingMismatches = [];
    this.pendingSkipped = [];
    this.pendingLifecycleActions = [];
    this.lineBuffers = { stdout: '', stderr: '' };
    this.exitPromise = new Promise((resolvePromise) => {
      this.resolveExit = resolvePromise;
    });
    this.appendLog('system', `${activity === 'watch' ? '已启动监视器' : '已启动一次预览'}（${this.mode}）`);
    this.attachChild(child);
    return this.getStatus();
  }

  attachChild(child) {
    child.stdout?.on('data', (chunk) => this.captureChunk('stdout', chunk));
    child.stderr?.on('data', (chunk) => this.captureChunk('stderr', chunk));
    child.once('error', (error) => {
      this.appendLog('stderr', `同步进程错误：${errorMessage(error)}`);
      this.finishChild(child, null, null);
    });
    child.once('close', (code, signal) => this.finishChild(child, code, signal));
  }

  captureChunk(stream, chunk) {
    const text = this.lineBuffers[stream] + String(chunk);
    const lines = text.split(/\r?\n/);
    this.lineBuffers[stream] = lines.pop() ?? '';
    for (const line of lines) this.captureLine(stream, line);
  }

  captureLine(stream, line) {
    const safeLine = redactLogLine(line, this.secretValues);
    if (!safeLine) return;
    this.appendLog(stream, safeLine);
    const mismatch = parseMismatch(safeLine);
    if (mismatch) {
      this.pendingMismatches = trimCollection([...this.pendingMismatches, mismatch], MAX_EVENT_ENTRIES);
    }
    const lifecycleAction = parseLifecycleAction(safeLine);
    if (lifecycleAction) {
      this.pendingLifecycleActions = trimCollection([...this.pendingLifecycleActions, lifecycleAction], MAX_EVENT_ENTRIES);
    }
    const skipped = parseSkipped(safeLine);
    if (skipped) {
      this.pendingSkipped = trimCollection([...this.pendingSkipped, skipped], MAX_EVENT_ENTRIES);
    }
    const summary = parseCycleSummary(safeLine);
    if (summary) {
      this.lastCycle = { ...summary, at: this.now().toISOString() };
      this.mismatches = this.pendingMismatches;
      this.lifecycleActions = this.pendingLifecycleActions;
      this.skipped = this.pendingSkipped;
      this.pendingMismatches = [];
      this.pendingLifecycleActions = [];
      this.pendingSkipped = [];
    }
  }

  appendLog(stream, line) {
    this.logs = trimCollection([...this.logs, {
      at: this.now().toISOString(),
      stream,
      line: redactLogLine(line, this.secretValues)
    }], MAX_LOG_ENTRIES);
  }

  finishChild(child, code, signal) {
    if (child !== this.child) return;
    for (const stream of ['stdout', 'stderr']) {
      if (this.lineBuffers[stream]) {
        this.captureLine(stream, this.lineBuffers[stream]);
        this.lineBuffers[stream] = '';
      }
    }
    const wasStopping = this.state === 'stopping';
    this.child = null;
    this.state = 'stopped';
    this.activity = null;
    this.lastExit = {
      at: this.now().toISOString(),
      code: typeof code === 'number' ? code : null,
      signal: signal ?? null,
      stoppedByUser: wasStopping
    };
    this.appendLog('system', wasStopping ? '同步进程已停止' : `同步进程已退出（代码 ${code ?? '未知'}）`);
    this.resolveExit?.();
    this.resolveExit = null;
    this.exitPromise = null;
  }

  async stopChild(child) {
    this.state = 'stopping';
    this.appendLog('system', '正在请求同步进程安全停止…');
    try {
      child.kill('SIGTERM');
    } catch (error) {
      this.appendLog('stderr', `无法请求同步进程停止：${errorMessage(error)}`);
    }

    const exitedNormally = await Promise.race([
      this.exitPromise ?? Promise.resolve(),
      delay(this.stopGraceMs).then(() => false)
    ]);
    if (exitedNormally === false && this.child === child) {
      this.appendLog('system', '同步进程未在等待时间内退出，正在强制停止');
      try {
        child.kill('SIGKILL');
      } catch (error) {
        this.appendLog('stderr', `无法强制停止同步进程：${errorMessage(error)}`);
      }
      await Promise.race([this.exitPromise ?? Promise.resolve(), delay(250)]);
    }
    return this.getStatus();
  }
}

export function createDaiSyncController(options) {
  return new DaiSyncController(options);
}
