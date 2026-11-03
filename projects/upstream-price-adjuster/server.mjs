import { createReadStream, existsSync, statSync } from 'node:fs';
import { createServer } from 'node:http';
import { extname, join, normalize, relative, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { DEFAULT_PORT } from './src/config.js';
import { createDaiBrowserSession } from './src/dai-browser-session.js';
import { createDaiSyncController } from './src/dai-sync-controller.js';

const packagedRuntime = Boolean(process.pkg || process.isSea || process.argv[1]?.toLowerCase().endsWith('.exe'));
const defaultRoot = process.argv.includes('--server') || process.argv.includes('--worker') || packagedRuntime
  ? resolve(process.execPath, '..')
  : resolve(fileURLToPath(new URL('.', import.meta.url)));
const MAX_JSON_BODY_BYTES = 16 * 1024;
const PUBLIC_FILES = new Set([
  'index.html',
  'pricing.html',
  'styles.css',
  'sync-console.css',
  'src/app.js',
  'src/config.js',
  'src/pricing.js',
  'src/sync-console-app.js'
]);
const MIME_TYPES = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8'
};
const DASHBOARD_SERVICE = 'upstream-price-adjuster-dashboard';

function errorMessage(error) {
  return error instanceof Error ? error.message : String(error);
}

function isPlainObject(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function isLoopbackRequest(request) {
  return new Set(['127.0.0.1', '::1', '::ffff:127.0.0.1']).has(request.socket?.remoteAddress);
}

function resolvePublicPath(root, urlPath) {
  const pathname = decodeURIComponent(new URL(urlPath, 'http://127.0.0.1').pathname);
  const requested = pathname === '/' ? 'index.html' : pathname.replace(/^[/\\]+/, '');
  const filePath = normalize(join(root, requested));
  const relativePath = relative(root, filePath);
  if (
    relativePath === '..'
    || relativePath.startsWith(`..${sep}`)
    || filePath === root
    || !PUBLIC_FILES.has(relativePath.replaceAll('\\', '/'))
  ) {
    return null;
  }
  return filePath;
}

function sendJson(response, statusCode, body) {
  response.writeHead(statusCode, {
    'Content-Type': 'application/json; charset=utf-8',
    'Cache-Control': 'no-store'
  });
  response.end(JSON.stringify(body));
}

function sendText(response, statusCode, body, headers = {}) {
  response.writeHead(statusCode, {
    'Content-Type': 'text/plain; charset=utf-8',
    'Cache-Control': 'no-store',
    ...headers
  });
  response.end(body);
}

async function readJsonBody(request) {
  const chunks = [];
  let size = 0;
  for await (const chunk of request) {
    size += chunk.length;
    if (size > MAX_JSON_BODY_BYTES) {
      throw new Error('请求体过大');
    }
    chunks.push(chunk);
  }
  if (size === 0) return {};
  try {
    const body = JSON.parse(Buffer.concat(chunks).toString('utf8'));
    if (!isPlainObject(body)) throw new Error('API 请求体必须是 JSON 对象');
    return body;
  } catch (error) {
    if (error.message === 'API 请求体必须是 JSON 对象') throw error;
    throw new Error('API 请求体不是有效 JSON');
  }
}

async function prepareBrowserSession(body, controller, browserSession) {
  const loaded = await controller.loadConfig(body);
  if (!loaded.config.browser) {
    throw new Error('当前配置未启用 browser 模式；请在配置中加入 browser.loginUrl');
  }
  browserSession.configure(loaded.config.browser);
  return loaded;
}

async function handleApiRequest(request, response, pathname, controller, browserSession) {
  if (!isLoopbackRequest(request)) {
    sendJson(response, 403, { error: '该本地 API 只接受 127.0.0.1 请求' });
    return;
  }
  if (pathname === '/api/sync/status') {
    if (request.method !== 'GET') {
      sendText(response, 405, 'Method Not Allowed', { Allow: 'GET' });
      return;
    }
    sendJson(response, 200, {
      service: DASHBOARD_SERVICE,
      apiVersion: 1,
      browser: browserSession.getStatus(),
      ...controller.getStatus()
    });
    return;
  }
  if (!['/api/sync/validate', '/api/sync/start', '/api/sync/preview', '/api/sync/stop', '/api/sync/browser/configure', '/api/sync/browser/open', '/api/sync/browser/save', '/api/sync/browser/close'].includes(pathname)) {
    sendJson(response, 404, { error: 'API Not Found' });
    return;
  }
  if (request.method !== 'POST') {
    sendText(response, 405, 'Method Not Allowed', { Allow: 'POST' });
    return;
  }

  try {
    const body = await readJsonBody(request);
    if (pathname === '/api/sync/validate') {
      const result = await controller.validateConfig(body);
      if (result.config.browser) browserSession.configure(result.config.browser);
      sendJson(response, 200, { ...result, browser: browserSession.getStatus() });
    } else if (pathname === '/api/sync/start') {
      sendJson(response, 200, await controller.start(body));
    } else if (pathname === '/api/sync/preview') {
      sendJson(response, 200, await controller.preview(body));
    } else if (pathname === '/api/sync/browser/configure') {
      await prepareBrowserSession(body, controller, browserSession);
      sendJson(response, 200, { browser: browserSession.getStatus() });
    } else if (pathname === '/api/sync/browser/open') {
      await prepareBrowserSession(body, controller, browserSession);
      sendJson(response, 200, { browser: await browserSession.openLogin() });
    } else if (pathname === '/api/sync/browser/save') {
      sendJson(response, 200, { browser: await browserSession.saveSession() });
    } else if (pathname === '/api/sync/browser/close') {
      sendJson(response, 200, { browser: await browserSession.close() });
    } else {
      sendJson(response, 200, await controller.stop());
    }
  } catch (error) {
    sendJson(response, 400, { error: errorMessage(error) });
  }
}

function handleStaticRequest(request, response, root) {
  if (request.method !== 'GET' && request.method !== 'HEAD') {
    sendText(response, 405, 'Method Not Allowed', { Allow: 'GET, HEAD' });
    return;
  }
  let filePath;
  try {
    filePath = resolvePublicPath(root, request.url || '/');
  } catch {
    sendText(response, 400, 'Bad Request');
    return;
  }
  if (!filePath || !existsSync(filePath) || !statSync(filePath).isFile()) {
    sendText(response, 404, 'Not Found');
    return;
  }
  const type = MIME_TYPES[extname(filePath).toLowerCase()] || 'application/octet-stream';
  response.writeHead(200, {
    'Content-Type': type,
    'Cache-Control': 'no-store'
  });
  if (request.method === 'HEAD') {
    response.end();
  } else {
    createReadStream(filePath).pipe(response);
  }
}

/** Create a loopback-only dashboard server. Construction does not start the sync CLI. */
export function createDashboardServer({ root = defaultRoot, controller, browserSession } = {}) {
  const assetRoot = resolve(root);
  const syncController = controller ?? createDaiSyncController({ root: assetRoot });
  const session = browserSession ?? createDaiBrowserSession({ root: assetRoot });
  const server = createServer(async (request, response) => {
    let pathname;
    try {
      pathname = new URL(request.url || '/', 'http://127.0.0.1').pathname;
    } catch {
      sendText(response, 400, 'Bad Request');
      return;
    }
    if (pathname.startsWith('/api/')) {
      await handleApiRequest(request, response, pathname, syncController, session);
      return;
    }
    handleStaticRequest(request, response, assetRoot);
  });
  server.syncController = syncController;
  server.browserSession = session;
  server.on('close', () => {
    void syncController.stop();
    void session.close();
  });
  return server;
}

function startFromCommandLine() {
  const port = Number(process.env.PORT || DEFAULT_PORT);
  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    throw new Error('PORT 必须是 1 到 65535 之间的整数');
  }
  const server = createDashboardServer();
  server.listen(port, '127.0.0.1', () => {
    console.log(`D-AI 同名分组同步控制台已启动：http://127.0.0.1:${port}/`);
  });
  let shuttingDown = false;
  const shutdown = () => {
    if (shuttingDown) return;
    shuttingDown = true;
    server.syncController.stop().finally(() => server.close());
  };
  process.once('SIGINT', shutdown);
  process.once('SIGTERM', shutdown);
}

const invokedAsCli = !packagedRuntime && process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url);
if (invokedAsCli) {
  try {
    startFromCommandLine();
  } catch (error) {
    console.error(`本地控制台无法启动：${errorMessage(error)}`);
    process.exitCode = 1;
  }
}
