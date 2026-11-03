import test from 'node:test';
import assert from 'node:assert/strict';
import { DaiBrowserSession } from '../src/dai-browser-session.js';

function createFakePlaywright({ savedPaths, onEvaluate = async (_callback, _argument) => ({ ok: true, status: 200, body: { items: [] } }) } = {}) {
  const launches = [];
  const navigations = [];
  const contexts = [];
  const initScripts = [];
  return {
    launches,
    navigations,
    contexts,
    initScripts,
    chromium: {
      async launch(options) {
        launches.push(options);
        return {
          async newContext(options) {
            const context = {
              options,
              closed: false,
              async newPage() {
                return {
                  async goto(url) { navigations.push(url); },
                  async evaluate(callback, argument) {
                    if (argument === undefined) return undefined;
                    return onEvaluate(callback, argument);
                  }
                };
              },
              async addInitScript(script) { initScripts.push(script); },
              async storageState({ path }) { context.savedPath = path; savedPaths.add(path); },
              async close() { context.closed = true; }
            };
            contexts.push(context);
            return context;
          },
          async close() {}
        };
      }
    }
  };
}

test('opens a visible manual login, saves isolated state, and uses headless saved-session requests', async () => {
  const savedPaths = new Set();
  let capturedAuthorization = null;
  const playwright = createFakePlaywright({
    savedPaths,
    onEvaluate: async (callback, argument) => {
      if (typeof callback !== 'function') return undefined;
      const previous = {
        localStorage: globalThis.localStorage,
        sessionStorage: globalThis.sessionStorage,
        fetch: globalThis.fetch
      };
      const makeStorage = (entries) => ({
        length: Object.keys(entries).length,
        key: (index) => Object.keys(entries)[index] ?? null,
        getItem: (key) => entries[key] ?? null
      });
      globalThis.localStorage = makeStorage({ access_token: 'test-only-browser-token', __dai_sync_request_headers__: JSON.stringify({ 'x-tenant-id': 'test-tenant' }) });
      globalThis.sessionStorage = makeStorage({});
      if (!argument?.requestUrl) return true;
      globalThis.fetch = async (_url, init) => {
        capturedAuthorization = init.headers.Authorization;
        return {
          ok: true,
          status: 200,
          async text() { return JSON.stringify({ requestUrl: argument.requestUrl }); }
        };
      };
      try {
        return await callback(argument);
      } finally {
        globalThis.localStorage = previous.localStorage;
        globalThis.sessionStorage = previous.sessionStorage;
        globalThis.fetch = previous.fetch;
      }
    }
  });
  const session = new DaiBrowserSession({
    root: '/sync-project',
    playwright,
    existsSyncImpl: (path) => path === '/chrome' || savedPaths.has(path),
    statSyncImpl: () => ({ isFile: () => true, mtime: new Date('2026-08-21T00:00:00.000Z') }),
    mkdirImpl: async () => {},
    chmodImpl: async () => {}
  });
  const browserConfig = {
    loginUrl: 'https://example.test/login',
    requestPageUrl: 'https://example.test/tenant/ai/models/upstreams',
    executablePath: '/chrome',
    storagePath: 'runtime/session.json'
  };

  const opened = await session.openLogin(browserConfig);
  assert.equal(opened.manualLoginOpen, true);
  assert.equal(playwright.launches[0].headless, false);
  assert.deepEqual(playwright.navigations, ['https://example.test/login']);

  const saved = await session.saveSession();
  assert.equal(saved.session.storagePath, 'runtime/session.json');
  assert.equal(session.getStatus().session.saved, true);

  const response = await session.request({
    url: 'https://example.test/api/v1/tenants/me/groups',
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: { default_user_multiplier: 0.22 }
  });
  assert.equal(response.ok, true);
  assert.equal(response.body.requestUrl, 'https://example.test/api/v1/tenants/me/groups');
  assert.equal(capturedAuthorization, 'Bearer test-only-browser-token');
  assert.equal(playwright.launches[1].headless, true);
  assert.equal(playwright.contexts[1].options.storageState.replaceAll('\\', '/').endsWith('/sync-project/runtime/session.json'), true);
  assert.equal(playwright.initScripts.length, 4);
  assert.equal(playwright.navigations.at(-1), 'https://example.test/tenant/ai/models/upstreams');
  assert.equal(playwright.initScripts.filter((script) => script.content.includes('sessionStorage')).length, 2);
  assert.equal(playwright.initScripts.filter((script) => script.content.includes('XMLHttpRequest')).length, 2);

  await session.close();
  assert.equal(session.getStatus().manualLoginOpen, false);
});

test('reuses one isolated request page until the session closes', async () => {
  const savedPaths = new Set(['/sync-project/runtime/session.json']);
  const playwright = createFakePlaywright({ savedPaths });
  const session = new DaiBrowserSession({
    root: '/sync-project',
    playwright,
    existsSyncImpl: (path) => path === '/chrome' || path.replaceAll('\\', '/').endsWith('/sync-project/runtime/session.json'),
    statSyncImpl: () => ({ isFile: () => true, mtime: new Date('2026-08-21T00:00:00.000Z') }),
    mkdirImpl: async () => {},
    chmodImpl: async () => {}
  });
  session.configure({
    loginUrl: 'https://example.test/login',
    requestPageUrl: 'https://example.test/tenant/ai/models/upstreams',
    executablePath: '/chrome',
    storagePath: 'runtime/session.json'
  });

  await session.request({ url: 'https://example.test/api/v1/tenants/me/groups' });
  await session.request({ url: 'https://example.test/api/v1/tenants/me/upstream-resources' });

  assert.equal(playwright.launches.length, 1);
  assert.equal(playwright.contexts.length, 1);
  assert.equal(playwright.navigations.length, 1);
  await session.close();
  assert.equal(playwright.contexts[0].closed, true);
});

test('rejects browser-session requests outside the configured service origin', async () => {
  const session = new DaiBrowserSession({
    root: '/sync-project',
    existsSyncImpl: () => false
  });
  session.configure({
    loginUrl: 'https://example.test/login',
    executablePath: '/chrome',
    storagePath: 'runtime/session.json'
  });

  await assert.rejects(
    () => session.request({ url: 'https://other.example.test/api', method: 'GET' }),
    /同一服务源/
  );
});
