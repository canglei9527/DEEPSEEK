import { spawn, spawnSync } from 'node:child_process';
import { request as httpRequest } from 'node:http';
import { fileURLToPath } from 'node:url';
import { resolve } from 'node:path';

const packagedRuntime = Boolean(process.pkg || process.isSea || process.argv[1]?.toLowerCase().endsWith('.exe'));
const root = packagedRuntime ? resolve(process.execPath, '..') : resolve(fileURLToPath(new URL('..', import.meta.url)));
const DEFAULT_PORT = 4173;
const DEFAULT_PROBE_TIMEOUT_MS = 450;
const DEFAULT_READY_TIMEOUT_MS = 8_000;
const MAX_PORT_ATTEMPTS = 30;

function errorMessage(error) {
  return error instanceof Error ? error.message : String(error);
}

function normalizePort(value, fallback = DEFAULT_PORT) {
  if (value === undefined || value === null || value === '') return fallback;
  const port = Number(value);
  if (!Number.isInteger(port) || port < 1 || port > 65_535) {
    throw new Error('PORT 必须是 1 到 65535 之间的整数');
  }
  return port;
}

function parseStatusBody(chunks) {
  try {
    const body = JSON.parse(Buffer.concat(chunks).toString('utf8'));
    return body
      && typeof body === 'object'
      && body.service === 'upstream-price-adjuster-dashboard'
      && body.apiVersion === 1
      && ['stopped', 'running', 'stopping'].includes(body.state);
  } catch {
    return false;
  }
}

/**
 * Probe only the loopback API. `dashboard` means this is our current server,
 * `occupied` means another service owns the port, and `free` means no listener.
 */
export function probeDashboardPort(port, {
  timeoutMs = DEFAULT_PROBE_TIMEOUT_MS,
  requestImpl = httpRequest
} = {}) {
  return new Promise((resolveProbe) => {
    let settled = false;
    const finish = (result) => {
      if (settled) return;
      settled = true;
      resolveProbe(result);
    };
    let request;
    try {
      request = requestImpl({
        host: '127.0.0.1',
        port,
        path: '/api/sync/status',
        method: 'GET',
        timeout: timeoutMs,
        headers: { Accept: 'application/json' }
      }, (response) => {
        const chunks = [];
        let size = 0;
        response.on('data', (chunk) => {
          size += chunk.length;
          if (size <= 32 * 1024) chunks.push(chunk);
        });
        response.once('end', () => {
          if (response.statusCode === 200 && parseStatusBody(chunks)) finish('dashboard');
          else finish('occupied');
        });
        response.once('error', () => finish('occupied'));
      });
      request.once('timeout', () => {
        request.destroy();
        finish('occupied');
      });
      request.once('error', (error) => {
        if (error?.code === 'ECONNREFUSED' || error?.code === 'ENOTFOUND') finish('free');
        else finish('occupied');
      });
      request.end();
    } catch {
      finish('occupied');
    }
  });
}

export async function chooseDashboardPort({
  startPort = DEFAULT_PORT,
  maxAttempts = MAX_PORT_ATTEMPTS,
  probePort = probeDashboardPort
} = {}) {
  const initialPort = normalizePort(startPort);
  if (!Number.isInteger(maxAttempts) || maxAttempts < 1) {
    throw new Error('maxAttempts 必须是正整数');
  }
  for (let offset = 0; offset < maxAttempts; offset += 1) {
    const port = initialPort + offset;
    if (port > 65_535) break;
    const result = await probePort(port);
    if (result === 'dashboard') return { port, reused: true };
    if (result === 'free') return { port, reused: false };
  }
  throw new Error(`无法找到可用的本机端口（已检查 ${initialPort}-${Math.min(65_535, initialPort + maxAttempts - 1)}）`);
}

function waitForDashboard(port, {
  timeoutMs = DEFAULT_READY_TIMEOUT_MS,
  probePort = probeDashboardPort,
  child
} = {}) {
  const startedAt = Date.now();
  return new Promise((resolveReady) => {
    let timer;
    let settled = false;
    const finish = (ready) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolveReady(ready);
    };
    const poll = async () => {
      if (settled) return;
      if (child?.exitCode !== null && child?.exitCode !== undefined) {
        finish(false);
        return;
      }
      try {
        if (await probePort(port) === 'dashboard') {
          finish(true);
          return;
        }
      } catch {
        // Keep polling until the bounded startup timeout.
      }
      if (Date.now() - startedAt >= timeoutMs) {
        finish(false);
        return;
      }
      setTimeout(poll, 100);
    };
    timer = setTimeout(() => finish(false), timeoutMs + 100);
    void poll();
  });
}

function spawnDashboardServer(port) {
  const args = packagedRuntime ? ['--server'] : ['server.mjs'];
  const child = spawn(process.execPath, args, {
    cwd: root,
    env: { ...process.env, PORT: String(port) },
    detached: true,
    stdio: 'ignore',
    windowsHide: true,
    shell: false
  });
  child.once('error', () => {});
  child.unref();
  return child;
}

function browserExists(executable) {
  if (process.platform !== 'win32') return false;
  return spawnSync('where.exe', [executable], { stdio: 'ignore', windowsHide: true }).status === 0;
}

export function openDashboardApp(url, {
  spawnImpl = spawn,
  platform = process.platform,
  existsImpl = browserExists
} = {}) {
  if (platform === 'win32') {
    for (const executable of ['msedge.exe', 'chrome.exe']) {
      if (!existsImpl(executable)) continue;
      const browser = spawnImpl(executable, [`--app=${url}`], {
        detached: true,
        stdio: 'ignore',
        windowsHide: true,
        shell: false
      });
      browser.unref?.();
      return { launcher: executable, url };
    }
    const fallback = spawnImpl('cmd.exe', ['/d', '/c', 'start', '', url], {
      detached: true,
      stdio: 'ignore',
      windowsHide: true,
      shell: false
    });
    fallback.unref?.();
    return { launcher: 'default-browser', url };
  }

  const executable = platform === 'darwin' ? 'open' : 'xdg-open';
  const fallback = spawnImpl(executable, [url], { detached: true, stdio: 'ignore', shell: false });
  fallback.unref?.();
  return { launcher: executable, url };
}

export async function launchDashboard({
  startPort = normalizePort(process.env.PORT, DEFAULT_PORT),
  choosePort = chooseDashboardPort,
  probePort = probeDashboardPort,
  spawnServer = spawnDashboardServer,
  openApp = openDashboardApp,
  log = console.log,
  maxAttempts = MAX_PORT_ATTEMPTS
} = {}) {
  let candidate = await choosePort({ startPort, maxAttempts, probePort });
  if (candidate.reused) {
    const url = `http://127.0.0.1:${candidate.port}/`;
    openApp(url);
    log(`已复用运行中的 D-AI 同名分组同步控制台：${url}`);
    return { ...candidate, url, child: null };
  }

  for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
    const child = spawnServer(candidate.port);
    const ready = await waitForDashboard(candidate.port, { probePort, child });
    if (ready) {
      const url = `http://127.0.0.1:${candidate.port}/`;
      openApp(url);
      log(`D-AI 同名分组同步控制台已启动：${url}`);
      return { ...candidate, url, child };
    }
    try { child.kill(); } catch { /* only our child is targeted */ }
    candidate = await choosePort({
      startPort: candidate.port + 1,
      maxAttempts: Math.max(1, maxAttempts - attempt - 1),
      probePort
    });
    if (candidate.reused) {
      const url = `http://127.0.0.1:${candidate.port}/`;
      openApp(url);
      log(`已复用运行中的 D-AI 同名分组同步控制台：${url}`);
      return { ...candidate, url, child: null };
    }
  }
  throw new Error('本地控制台启动失败：端口绑定或 API 就绪检查未通过');
}

const invokedAsCli = !packagedRuntime && process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url);
if (invokedAsCli) {
  launchDashboard().catch((error) => {
    console.error(`桌面控制台无法启动：${errorMessage(error)}`);
    process.exitCode = 1;
  });
}
