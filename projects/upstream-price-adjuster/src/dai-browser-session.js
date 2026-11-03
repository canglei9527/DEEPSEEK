import { chmod, mkdir } from 'node:fs/promises';
import { existsSync, statSync } from 'node:fs';
import { dirname, isAbsolute, relative, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

export const DEFAULT_DAI_BROWSER_STORAGE_PATH = 'runtime/dai-browser-storage.json';

const packagedRuntime = process.argv.includes('--server') || process.argv.includes('--worker') || process.pkg || process.isSea || process.argv[1]?.toLowerCase().endsWith('.exe');
const projectRoot = packagedRuntime ? resolve(process.execPath, '..') : resolve(fileURLToPath(new URL('..', import.meta.url)));
const BLOCKED_PROFILE_OPTIONS = [
  'userDataDir',
  'profileDirectory',
  'profilePath',
  'chromeProfile',
  'persistentContext'
];
const SESSION_STORAGE_SNAPSHOT_KEY = '__dai_sync_session_storage_snapshot__';
const REQUEST_HEADERS_STORAGE_KEY = '__dai_sync_request_headers__';

function errorMessage(error) {
  return error instanceof Error ? error.message : String(error);
}

function normalizeHttpUrl(value, label) {
  if (typeof value !== 'string' || !value.trim()) {
    throw new Error(`${label} 必须是非空 http(s) URL`);
  }
  let parsed;
  try {
    parsed = new URL(value.trim());
  } catch {
    throw new Error(`${label} 必须是有效的 http(s) URL`);
  }
  if (!['http:', 'https:'].includes(parsed.protocol) || parsed.username || parsed.password) {
    throw new Error(`${label} 必须是不含凭据的 http(s) URL`);
  }
  if (parsed.search || parsed.hash) {
    throw new Error(`${label} 不允许查询参数或片段，避免把认证值写入本地配置或状态`);
  }
  return parsed.toString();
}

function normalizeOptionalPath(value, label) {
  if (value === undefined || value === null || value === '') return null;
  if (typeof value !== 'string' || !value.trim()) {
    throw new Error(`${label} 必须是非空路径`);
  }
  return value.trim();
}

function normalizeStoragePathInput(value) {
  const storagePath = normalizeOptionalPath(value, 'browser.storagePath')
    ?? DEFAULT_DAI_BROWSER_STORAGE_PATH;
  const normalized = storagePath.replaceAll('\\', '/');
  if (
    isAbsolute(storagePath)
    || !normalized.startsWith('runtime/')
    || normalized.split('/').some((part) => part === '..' || !part)
    || !normalized.toLowerCase().endsWith('.json')
  ) {
    throw new Error('browser.storagePath 必须是 runtime/ 目录内的相对 .json 文件');
  }
  return normalized;
}

function assertNoProfileReuse(input) {
  for (const key of BLOCKED_PROFILE_OPTIONS) {
    if (input?.[key] !== undefined) {
      throw new Error(`不支持 ${key}：登录窗口始终使用 Playwright 的临时隔离上下文，不会复用本机 Chrome 个人资料`);
    }
  }
}

function uniquePaths(paths, platform) {
  const seen = new Set();
  return paths.filter((value) => {
    if (typeof value !== 'string' || !value.trim()) return false;
    const normalized = platform === 'win32' ? value.toLowerCase() : value;
    if (seen.has(normalized)) return false;
    seen.add(normalized);
    return true;
  });
}

/** Return known Google Chrome binary locations without launching or reading any browser profile. */
export function getChromeExecutableCandidates({
  platform = process.platform,
  environment = process.env
} = {}) {
  const configured = [
    environment.DAI_CHROME_EXECUTABLE_PATH,
    environment.CHROME_PATH,
    environment.GOOGLE_CHROME_PATH
  ];

  if (platform === 'win32') {
    const programFiles = [
      environment.PROGRAMFILES,
      environment['PROGRAMFILES(X86)'],
      environment.ProgramW6432,
      'C:\\Program Files',
      'C:\\Program Files (x86)'
    ];
    return uniquePaths([
      ...configured,
      ...programFiles.map((folder) => folder && `${folder}\\Google\\Chrome\\Application\\chrome.exe`),
      environment.LOCALAPPDATA && `${environment.LOCALAPPDATA}\\Google\\Chrome\\Application\\chrome.exe`
    ], platform);
  }
  if (platform === 'darwin') {
    return uniquePaths([
      ...configured,
      '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
      '/Applications/Google Chrome Beta.app/Contents/MacOS/Google Chrome'
    ], platform);
  }
  return uniquePaths([
    ...configured,
    '/usr/bin/google-chrome',
    '/usr/bin/google-chrome-stable',
    '/usr/bin/chromium-browser',
    '/usr/bin/chromium'
  ], platform);
}

/**
 * Detect an installed Chrome executable. An explicitly configured but missing path is
 * treated as an error instead of silently switching to another browser.
 */
export function detectChromeExecutable({
  executablePath,
  platform = process.platform,
  environment = process.env,
  existsSyncImpl = existsSync
} = {}) {
  const configuredPath = normalizeOptionalPath(executablePath, 'executablePath');
  if (configuredPath) {
    if (existsSyncImpl(configuredPath)) {
      return { available: true, executablePath: configuredPath, source: 'configured' };
    }
    return {
      available: false,
      executablePath: null,
      source: 'configured',
      reason: '配置的 Chrome executablePath 不存在或不可访问'
    };
  }

  const candidate = getChromeExecutableCandidates({ platform, environment })
    .find((path) => existsSyncImpl(path));
  if (candidate) return { available: true, executablePath: candidate, source: 'detected' };
  return {
    available: false,
    executablePath: null,
    source: 'not-found',
    reason: '未找到已安装的 Google Chrome；请在私有配置中设置 browser.executablePath'
  };
}

/** Resolve the only permitted saved-session location: a JSON file under project/runtime. */
export function resolveDaiBrowserStoragePath({
  root = projectRoot,
  storagePath = DEFAULT_DAI_BROWSER_STORAGE_PATH
} = {}) {
  const resolvedRoot = resolve(root);
  const runtimeRoot = resolve(resolvedRoot, 'runtime');
  const requested = normalizeStoragePathInput(storagePath);
  if (isAbsolute(requested)) {
    throw new Error('browser.storagePath 必须是项目 runtime/ 目录内的相对路径');
  }
  const absolutePath = resolve(resolvedRoot, requested);
  const relativeToRuntime = relative(runtimeRoot, absolutePath);
  if (
    !relativeToRuntime
    || isAbsolute(relativeToRuntime)
    || relativeToRuntime === '..'
    || relativeToRuntime.startsWith(`..${sep}`)
    || !absolutePath.toLowerCase().endsWith('.json')
  ) {
    throw new Error('browser.storagePath 必须是 runtime/ 目录内的 .json 文件');
  }
  return {
    absolutePath,
    relativePath: relative(resolvedRoot, absolutePath).replaceAll('\\', '/')
  };
}

function normalizeReadSource(value, label, defaultUrl) {
  if (value === undefined || value === null) return null;
  if (typeof value !== 'object' || Array.isArray(value)) {
    throw new Error(`${label} 必须是对象`);
  }
  const selector = typeof value.selector === 'string' ? value.selector.trim() : '';
  if (!selector) throw new Error(`${label}.selector 必须是非空 CSS 选择器`);
  if (selector.length > 500) throw new Error(`${label}.selector 过长`);
  return {
    url: normalizeHttpUrl(value.url ?? defaultUrl, `${label}.url`),
    selector
  };
}

/**
 * Normalize browser configuration that is safe to keep in a local JSON file.
 * It deliberately has no user-profile option and no credential field.
 */
export function normalizeDaiBrowserConfig(input = {}, { baseUrl } = {}) {
  if (input === null || typeof input !== 'object' || Array.isArray(input)) {
    throw new Error('browser 配置必须是对象');
  }
  assertNoProfileReuse(input);
  const fallbackUrl = baseUrl === undefined ? undefined : normalizeHttpUrl(baseUrl, 'baseUrl');
  const loginUrl = normalizeHttpUrl(input.loginUrl ?? fallbackUrl, 'browser.loginUrl');
  const requestPageUrl = normalizeHttpUrl(input.requestPageUrl ?? loginUrl, 'browser.requestPageUrl');
  if (new URL(requestPageUrl).origin !== new URL(loginUrl).origin) {
    throw new Error('browser.requestPageUrl 必须与 browser.loginUrl 使用同一服务源');
  }
  return {
    loginUrl,
    requestPageUrl,
    executablePath: normalizeOptionalPath(input.executablePath, 'browser.executablePath'),
    storagePath: normalizeStoragePathInput(input.storagePath),
    read: {
      upstreams: normalizeReadSource(input.read?.upstreams, 'browser.read.upstreams', loginUrl),
      groups: normalizeReadSource(input.read?.groups, 'browser.read.groups', loginUrl)
    }
  };
}

/** Normalize safe browser-session options and resolve its controlled local storage path. */
export function normalizeDaiBrowserSessionConfig(input = {}, { root = projectRoot, baseUrl } = {}) {
  const config = normalizeDaiBrowserConfig(input, { baseUrl });
  const storage = resolveDaiBrowserStoragePath({ root, storagePath: config.storagePath });
  return {
    ...config,
    storagePath: storage.absolutePath,
    storagePathLabel: storage.relativePath
  };
}

function safeUrlForStatus(value) {
  if (!value) return null;
  try {
    const parsed = new URL(value);
    return `${parsed.origin}${parsed.pathname}`;
  } catch {
    return null;
  }
}

function safeSavedSessionMetadata(storagePath, storagePathLabel, existsSyncImpl, statSyncImpl) {
  if (!existsSyncImpl(storagePath)) {
    return { saved: false, storagePath: storagePathLabel, savedAt: null };
  }
  try {
    const metadata = statSyncImpl(storagePath);
    return {
      saved: metadata.isFile(),
      storagePath: storagePathLabel,
      savedAt: metadata.isFile() ? metadata.mtime.toISOString() : null
    };
  } catch {
    return { saved: false, storagePath: storagePathLabel, savedAt: null };
  }
}

function createSessionStorageCaptureScript(snapshotKey) {
  return `(() => {
    try {
      const entries = Object.fromEntries(Object.entries(sessionStorage));
      localStorage.setItem(${JSON.stringify(snapshotKey)}, JSON.stringify(entries));
    } catch {}
  })();`;
}

function createRequestHeaderCaptureScript(storageKey) {
  return `(() => {
    try {
      if (window.__daiSyncHeaderCaptureInstalled) return;
      window.__daiSyncHeaderCaptureInstalled = true;
      const originalFetch = window.fetch;
      window.fetch = async (input, init = {}) => {
        try {
          const request = new Request(input, init);
          const url = new URL(request.url, location.href);
          if (url.origin === location.origin && url.pathname.includes('/api/')) {
            const captured = {};
            for (const [name, value] of request.headers.entries()) {
              if (!/^(accept|content-type|cookie|host|content-length|origin|referer|user-agent)$/i.test(name)) {
                captured[name] = value;
              }
            }
            if (Object.keys(captured).length) localStorage.setItem(${JSON.stringify(storageKey)}, JSON.stringify(captured));
          }
        } catch {}
        return originalFetch(input, init);
      };
      const originalOpen = XMLHttpRequest.prototype.open;
      const originalSetRequestHeader = XMLHttpRequest.prototype.setRequestHeader;
      XMLHttpRequest.prototype.open = function(method, url, ...rest) {
        this.__daiSyncUrl = new URL(url, location.href).toString();
        return originalOpen.call(this, method, url, ...rest);
      };
      XMLHttpRequest.prototype.setRequestHeader = function(name, value) {
        try {
          const url = this.__daiSyncUrl ? new URL(this.__daiSyncUrl) : null;
          if (url && url.origin === location.origin && url.pathname.includes('/api/') && !/^(accept|content-type|cookie|host|content-length|origin|referer|user-agent)$/i.test(name)) {
            let captured = {};
            try { captured = JSON.parse(localStorage.getItem(${JSON.stringify(storageKey)}) || '{}'); } catch {}
            captured[name] = value;
            localStorage.setItem(${JSON.stringify(storageKey)}, JSON.stringify(captured));
          }
        } catch {}
        return originalSetRequestHeader.call(this, name, value);
      };
    } catch {}
  })();`;
}

function createSessionStorageRestoreScript(snapshotKey) {
  return `(() => {
    try {
      let snapshot;
      try {
        snapshot = JSON.parse(localStorage.getItem(${JSON.stringify(snapshotKey)}) || '{}');
      } catch {
        snapshot = {};
      }
      if (!snapshot || typeof snapshot !== 'object' || Array.isArray(snapshot)) return;
      for (const [key, value] of Object.entries(snapshot)) {
        if (typeof key === 'string' && typeof value === 'string') sessionStorage.setItem(key, value);
      }
      localStorage.removeItem(${JSON.stringify(snapshotKey)});
    } catch {}
  })();`;
}

async function loadPlaywrightCore() {
  try {
    return await import('playwright-core');
  } catch (error) {
    throw new Error(`无法加载 playwright-core。请先运行 npm install（不会下载 Chromium）：${errorMessage(error)}`);
  }
}

/**
 * Local, controlled manual-login session manager.
 *
 * It launches the system Chrome headfully and always creates a fresh Playwright
 * context. It never supplies a userDataDir, never reads storage-state contents,
 * and only reports metadata about the saved state file.
 */
export class DaiBrowserSession {
  constructor({
    root = projectRoot,
    playwright,
    playwrightLoader = loadPlaywrightCore,
    existsSyncImpl = existsSync,
    statSyncImpl = statSync,
    mkdirImpl = mkdir,
    chmodImpl = chmod,
    now = () => new Date()
  } = {}) {
    this.root = resolve(root);
    this.playwright = playwright;
    this.playwrightLoader = playwrightLoader;
    this.existsSyncImpl = existsSyncImpl;
    this.statSyncImpl = statSyncImpl;
    this.mkdirImpl = mkdirImpl;
    this.chmodImpl = chmodImpl;
    this.now = now;
    this.browser = null;
    this.browserHeadless = null;
    this.loginContext = null;
    this.loginPage = null;
    this.requestPageSession = null;
    this.requestPagePromise = null;
    this.requestPageUrl = null;
    this.options = null;
    this.lastMessage = '尚未打开登录窗口';
    this.lastSavedAt = null;
  }

  getStatus() {
    if (!this.options) {
      return {
        state: 'idle',
        manualLoginOpen: false,
        chrome: { available: null },
        loginUrl: null,
        session: { saved: false, storagePath: DEFAULT_DAI_BROWSER_STORAGE_PATH, savedAt: null },
        message: this.lastMessage
      };
    }
    const chrome = detectChromeExecutable({
      executablePath: this.options.executablePath,
      existsSyncImpl: this.existsSyncImpl
    });
    const savedSession = safeSavedSessionMetadata(
      this.options.storagePath,
      this.options.storagePathLabel,
      this.existsSyncImpl,
      this.statSyncImpl
    );
    return {
      state: this.loginContext ? 'login-open' : 'idle',
      manualLoginOpen: Boolean(this.loginContext),
      chrome: {
        available: chrome.available,
        ...(chrome.available ? { executablePath: chrome.executablePath, source: chrome.source } : { reason: chrome.reason })
      },
      loginUrl: safeUrlForStatus(this.options.loginUrl),
      requestPageUrl: safeUrlForStatus(this.options.requestPageUrl),
      session: {
        ...savedSession,
        ...(this.lastSavedAt ? { lastSavedAt: this.lastSavedAt } : {})
      },
      message: this.lastMessage
    };
  }

  configure(input = {}) {
    const merged = {
      ...(this.options?.loginUrl ? { loginUrl: this.options.loginUrl } : {}),
      ...(this.options?.requestPageUrl ? { requestPageUrl: this.options.requestPageUrl } : {}),
      ...(this.options?.executablePath ? { executablePath: this.options.executablePath } : {}),
      ...(this.options?.storagePathLabel ? { storagePath: this.options.storagePathLabel } : {}),
      ...input
    };
    this.options = normalizeDaiBrowserSessionConfig(merged, { root: this.root });
    return this.getStatus();
  }

  async getPlaywright() {
    const playwright = this.playwright ?? await this.playwrightLoader();
    if (!playwright?.chromium || typeof playwright.chromium.launch !== 'function') {
      throw new Error('playwright-core 未提供 chromium.launch，无法启动已安装的 Google Chrome');
    }
    return playwright;
  }

  async ensureBrowser({ headless = false } = {}) {
    if (headless ? this.browserHeadless : this.browser) return headless ? this.browserHeadless : this.browser;
    const chrome = detectChromeExecutable({
      executablePath: this.options.executablePath,
      existsSyncImpl: this.existsSyncImpl
    });
    if (!chrome.available) throw new Error(chrome.reason);
    const playwright = await this.getPlaywright();
    try {
      const browser = await playwright.chromium.launch({
        executablePath: chrome.executablePath,
        headless
      });
      if (headless) this.browserHeadless = browser;
      else this.browser = browser;
      return browser;
    } catch (error) {
      throw new Error(`无法启动本机 Google Chrome：${errorMessage(error)}`);
    }
  }

  /** Open a fresh, isolated, visible Chrome context for the user to log in manually. */
  async openLogin(input = {}) {
    this.configure(input);
    if (!this.options.loginUrl) {
      throw new Error('请先在私有配置中设置 browser.loginUrl，再打开 D-AI 登录窗口');
    }
    if (this.loginContext) {
      this.lastMessage = '登录窗口已打开；请在该窗口完成手动登录后点击“保存会话”';
      return this.getStatus();
    }
    const browser = await this.ensureBrowser();
    try {
      // Do not use launchPersistentContext or userDataDir: this is intentionally isolated.
      this.loginContext = await browser.newContext();
      if (typeof this.loginContext.addInitScript === 'function') {
        await this.loginContext.addInitScript({ content: createSessionStorageCaptureScript(SESSION_STORAGE_SNAPSHOT_KEY) });
        await this.loginContext.addInitScript({ content: createRequestHeaderCaptureScript(REQUEST_HEADERS_STORAGE_KEY) });
      }
      this.loginPage = await this.loginContext.newPage();
      await this.loginPage.goto(this.options.loginUrl, { waitUntil: 'domcontentloaded' });
      this.lastMessage = '已打开隔离的 Chrome 登录窗口；请自行完成登录，然后在本地控制台保存会话';
      return this.getStatus();
    } catch (error) {
      await this.closeLoginContext();
      throw new Error(`无法打开 D-AI 登录页面：${errorMessage(error)}`);
    }
  }

  /** Save Playwright state directly to disk without loading or returning its contents. */
  async saveSession() {
    if (!this.loginContext) {
      throw new Error('当前没有打开的登录窗口；请先打开 D-AI 登录并手动完成登录');
    }
    await this.mkdirImpl(dirname(this.options.storagePath), { recursive: true });
    try {
      await this.loginPage.evaluate(createSessionStorageCaptureScript(SESSION_STORAGE_SNAPSHOT_KEY));
      await this.loginContext.storageState({ path: this.options.storagePath });
      try {
        await this.chmodImpl(this.options.storagePath, 0o600);
      } catch {
        // Windows may not support POSIX modes; the runtime directory remains local and ignored.
      }
      this.lastSavedAt = this.now().toISOString();
      this.lastMessage = '会话已保存到受控 runtime 文件；控制台不会读取或显示会话内容';
      return this.getStatus();
    } catch (error) {
      throw new Error(`无法保存本地浏览器会话：${errorMessage(error)}`);
    }
  }

  /**
   * Create a new isolated context that asks Playwright to consume the saved state file.
   * The application never parses that file or exposes its content.
   */
  async openSavedPage({ url, ...input } = {}) {
    this.configure(input);
    const pageUrl = normalizeHttpUrl(url, '浏览器读取页面 URL');
    if (!this.existsSyncImpl(this.options.storagePath)) {
      throw new Error('未找到已保存的本地浏览器会话；请先完成手动登录并保存会话');
    }
    const browser = await this.ensureBrowser({ headless: true });
    let context;
    try {
      context = await browser.newContext({ storageState: this.options.storagePath });
      if (typeof context.addInitScript === 'function') {
        await context.addInitScript({ content: createSessionStorageRestoreScript(SESSION_STORAGE_SNAPSHOT_KEY) });
        await context.addInitScript({ content: createRequestHeaderCaptureScript(REQUEST_HEADERS_STORAGE_KEY) });
      }
      const page = await context.newPage();
      await page.goto(pageUrl, { waitUntil: 'domcontentloaded' });
      return {
        page,
        close: async () => {
          try { await context.close(); } catch { /* local cleanup only */ }
        }
      };
    } catch (error) {
      try { await context?.close(); } catch { /* local cleanup only */ }
      throw new Error(`无法使用已保存会话打开读取页面：${errorMessage(error)}`);
    }
  }

  /**
   * Execute one same-origin JSON request with the saved browser session.
   * Response bodies are returned, but cookies and storage state never leave the page context.
   */
  async request({ url, method = 'GET', headers = {}, body } = {}) {
    const requestUrl = normalizeHttpUrl(url, '浏览器请求 URL');
    const loginOrigin = new URL(this.options.loginUrl).origin;
    if (new URL(requestUrl).origin !== loginOrigin) {
      throw new Error('浏览器请求必须与 browser.loginUrl 使用同一服务源');
    }
    const sessionPage = await this.openRequestPage({ url: this.options.requestPageUrl });
    await sessionPage.page.evaluate(async (storageKey) => {
      const deadline = Date.now() + 3_000;
      while (Date.now() < deadline) {
        try {
          const captured = JSON.parse(localStorage.getItem(storageKey) || '{}');
          if (captured && typeof captured === 'object' && Object.keys(captured).length > 0) return true;
        } catch {}
        await new Promise((resolvePromise) => setTimeout(resolvePromise, 100));
      }
      return false;
    }, REQUEST_HEADERS_STORAGE_KEY);
    return sessionPage.page.evaluate(async ({ requestUrl: targetUrl, method: requestMethod, headers: requestHeaders, body: requestBody, headerStorageKey }) => {
      const outgoingHeaders = { ...requestHeaders };
      try {
        const captured = JSON.parse(localStorage.getItem(headerStorageKey) || '{}');
        for (const [name, value] of Object.entries(captured)) {
          if (typeof name === 'string' && typeof value === 'string' && !Object.keys(outgoingHeaders).some((existing) => existing.toLowerCase() === name.toLowerCase())) {
            outgoingHeaders[name] = value;
          }
        }
      } catch {}
      const hasAuthorization = Object.keys(outgoingHeaders).some((name) => name.toLowerCase() === 'authorization');
      if (!hasAuthorization) {
        const names = new Set(['token', 'access_token', 'access-token', 'accessToken', 'id_token', 'idToken', 'authorization', 'Authorization', 'user_token', 'userToken', 'jwt', 'admin_token']);
        const extract = (value) => {
          if (typeof value !== 'string' || !value.trim()) return null;
          const text = value.trim();
          try {
            const parsed = JSON.parse(text);
            if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
              for (const key of ['token', 'access_token', 'accessToken', 'id_token', 'idToken', 'authorization']) {
                const nested = extract(parsed[key]);
                if (nested) return nested;
              }
            }
          } catch {}
          return text;
        };
        const read = (storage) => {
          for (let index = 0; index < storage.length; index += 1) {
            const key = storage.key(index);
            if (!key || (!names.has(key) && !/(?:token|auth|jwt)/i.test(key))) continue;
            const value = extract(storage.getItem(key));
            if (value) return value;
          }
          return null;
        };
        const value = read(localStorage) || read(sessionStorage);
        if (value) outgoingHeaders.Authorization = /^Bearer\\s/i.test(value) ? value : `Bearer ${value}`;
      }
      const response = await fetch(targetUrl, {
        method: requestMethod,
        headers: outgoingHeaders,
        body: requestBody,
        credentials: 'include',
        redirect: 'error'
      });
      const text = await response.text();
      let parsed = null;
      if (text) {
        try { parsed = JSON.parse(text); } catch { parsed = null; }
      }
      return { ok: response.ok, status: response.status, body: parsed };
    }, {
      requestUrl,
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      headerStorageKey: REQUEST_HEADERS_STORAGE_KEY
    });
  }

  async openRequestPage({ url, ...input } = {}) {
    this.configure(input);
    const pageUrl = normalizeHttpUrl(url, '浏览器读取页面 URL');
    if (this.requestPageSession && this.requestPageUrl === pageUrl) return this.requestPageSession;
    await this.closeRequestPage();
    if (this.requestPagePromise) return this.requestPagePromise;
    this.requestPageUrl = pageUrl;
    this.requestPagePromise = this.openSavedPage({ url: pageUrl }).then((sessionPage) => {
      this.requestPageSession = sessionPage;
      return sessionPage;
    }).finally(() => {
      this.requestPagePromise = null;
    });
    try {
      return await this.requestPagePromise;
    } catch (error) {
      this.requestPageUrl = null;
      throw error;
    }
  }

  async closeRequestPage() {
    const sessionPage = this.requestPageSession;
    this.requestPageSession = null;
    this.requestPageUrl = null;
    if (sessionPage) await sessionPage.close();
  }

  async closeLoginContext() {
    const context = this.loginContext;
    this.loginContext = null;
    this.loginPage = null;
    if (context) {
      try { await context.close(); } catch { /* local cleanup only */ }
    }
  }

  async close() {
    await this.closeLoginContext();
    await this.closeRequestPage();
    const browser = this.browser;
    this.browser = null;
    if (browser) {
      try { await browser.close(); } catch { /* local cleanup only */ }
    }
    const headlessBrowser = this.browserHeadless;
    this.browserHeadless = null;
    if (headlessBrowser) {
      try { await headlessBrowser.close(); } catch { /* local cleanup only */ }
    }
    this.lastMessage = '本机受控浏览器窗口已关闭';
    return this.getStatus();
  }
}

export function createDaiBrowserSession(options) {
  return new DaiBrowserSession(options);
}
