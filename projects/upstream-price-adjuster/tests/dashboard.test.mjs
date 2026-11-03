import test from 'node:test';
import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { createDashboardServer } from '../server.mjs';
import { DaiSyncController } from '../src/dai-sync-controller.js';
import { chooseDashboardPort, launchDashboard } from '../scripts/launch-dashboard.mjs';

const config = {
  baseUrl: 'http://127.0.0.1:19001',
  allowApply: false,
  intervalMs: 60_000,
  headers: {}
};

class FakeStream extends EventEmitter {}

function createFakeChild() {
  const child = new EventEmitter();
  child.stdout = new FakeStream();
  child.stderr = new FakeStream();
  child.killSignals = [];
  child.kill = (signal) => {
    child.killSignals.push(signal);
    queueMicrotask(() => child.emit('close', signal === 'SIGKILL' ? null : 0, signal));
    return true;
  };
  return child;
}

function makeController(overrides = {}) {
  const children = [];
  const controller = new DaiSyncController({
    root: 'C:\\sync-project',
    environment: {},
    readFileImpl: async () => JSON.stringify(config),
    spawnImpl: (...args) => {
      const child = createFakeChild();
      children.push({ child, args });
      return child;
    },
    ...overrides
  });
  return { controller, children };
}

test('controller starts one dry-run child, parses local output, and rejects duplicate starts', async () => {
  const { controller, children } = makeController();
  const started = await controller.start({ configPath: 'dai-sync.config.json', apply: false, intervalMs: 1_000 });
  assert.equal(started.state, 'running');
  assert.equal(children.length, 1);
  assert.equal(children[0].args[1][0].endsWith('bin\\dai-group-sync.mjs'), true);
  assert.deepEqual(children[0].args[1].slice(1), [
    '--config',
    'C:\\sync-project\\dai-sync.config.json',
    '--watch',
    '--dry-run',
    '--interval-ms',
    '1000',
    '--adjustment',
    '0.02'
  ]);
  assert.equal(children[0].args[2].shell, false);

  await assert.rejects(
    () => controller.start({ configPath: 'dai-sync.config.json', apply: false }),
    /已经在运行/
  );

  children[0].child.stdout.emit('data', '[预览] "model-a" ×0.1 -> ×0.12\n[跳过] missing-group："model-b"\n第 1 轮 [dry-run] 上游=2 分组=2 匹配=1 一致=0 待更新=1 跳过=1\n');
  assert.equal(controller.getStatus().mismatches[0].name, 'model-a');
  assert.equal(controller.getStatus().skipped[0].code, 'missing-group');
  assert.equal(controller.getStatus().lastCycle.pending, 1);

  await controller.stop();
  assert.equal(controller.getStatus().state, 'stopped');
  assert.deepEqual(children[0].child.killSignals, ['SIGTERM']);
});

test('controller passes a selected adjustment to the sync child', async () => {
  const { controller, children } = makeController();
  await controller.start({ configPath: 'dai-sync.config.json', apply: false, adjustment: 0.005 });
  assert.equal(controller.getStatus().config.adjustment, 0.005);
  assert.deepEqual(children[0].args[1].slice(-2), ['--adjustment', '0.005']);
  await controller.stop();
});
test('apply requires allowApply and then starts without a UI confirmation field', async () => {
  const { controller, children } = makeController();
  await assert.rejects(
    () => controller.start({ configPath: 'dai-sync.config.json', apply: true }),
    /allowApply/
  );
  assert.equal(children.length, 0);

  const { controller: allowed, children: allowedChildren } = makeController({
    readFileImpl: async () => JSON.stringify({ ...config, allowApply: true })
  });
  const status = await allowed.start({ configPath: 'dai-sync.config.json', apply: true });
  assert.equal(status.mode, 'apply');
  assert.equal(allowedChildren[0].args[1].includes('--apply'), true);
  await allowed.stop();
});


test('controller passes lifecycle flags and reports lifecycle summaries separately', async () => {
  const { controller, children } = makeController({
    readFileImpl: async () => JSON.stringify({ ...config, allowApply: true, allowLifecycleApply: true })
  });
  const started = await controller.start({
    configPath: 'dai-sync.config.json',
    apply: true,
    lifecycle: true,
    disableStale: false
  });
  assert.equal(started.lifecycle, true);
  assert.equal(started.disableStale, false);
  assert.equal(children[0].args[1].at(-1), '--lifecycle');
  assert.equal(children[0].args[1].includes('--disable-stale'), false);
  children[0].child.stdout.emit('data', '[生命周期预览] 创建规则 "vendor-a" 模型="model-a"\n第 1 轮 [dry-run] 创建分组=1 关联上游=1 创建规则=2 更新倍率=0 待恢复规则=0 待恢复分组=0 待停用分组=0 跳过=0\n');
  assert.equal(controller.getStatus().lastCycle.type, 'lifecycle');
  assert.equal(controller.getStatus().lastCycle.createRules, 2);
  assert.equal(controller.getStatus().lifecycleActions[0].name, 'vendor-a');
  await controller.stop();
});

test('controller passes restored lifecycle flag and parses expanded lifecycle summaries', async () => {
  const { controller, children } = makeController({
    readFileImpl: async () => JSON.stringify({ ...config, allowApply: true, allowLifecycleApply: true, allowEnableRestored: true })
  });
  const started = await controller.start({ configPath: 'dai-sync.config.json', apply: true, lifecycle: true, enableRestored: true });
  assert.equal(started.enableRestored, true);
  assert.equal(children[0].args[1].at(-1), '--enable-restored');
  children[0].child.stdout.emit('data', '第 1 轮 [apply] 创建分组=0 关联上游=0 创建规则=0 更新倍率=1 待恢复规则=1 待恢复分组=1 待停用分组=0 跳过=0 已完成=3 尝试=3\n');
  assert.equal(controller.getStatus().lastCycle.enableRules, 1);
  assert.equal(controller.getStatus().lastCycle.updates, 1);
  await controller.stop();
});
test('browser mode refuses to start before a saved local session exists', async () => {
  const { controller, children } = makeController({
    readFileImpl: async () => JSON.stringify({
      ...config,
      allowApply: true,
      browser: { loginUrl: 'http://127.0.0.1:19001', storagePath: 'runtime/missing-session.json' }
    })
  });

  await assert.rejects(
    () => controller.start({ configPath: 'dai-sync.config.json', apply: true }),
    /登录会话尚未保存/
  );
  assert.equal(children.length, 0);
});

test('controller defaults to dry-run and never exposes an environment credential in status or logs', async () => {
  const token = 'test-only-token-value';
  const { controller, children } = makeController({
    environment: { DASHBOARD_TEST_TOKEN: token },
    readFileImpl: async () => JSON.stringify({
      ...config,
      headers: { Authorization: { env: 'DASHBOARD_TEST_TOKEN' } }
    })
  });
  const status = await controller.start({ configPath: 'dai-sync.config.json' });
  assert.equal(status.mode, 'dry-run');
  assert.equal(children[0].args[1].includes('--dry-run'), true);
  children[0].child.stderr.emit('data', `Authorization: ${token}\n`);
  assert.doesNotMatch(JSON.stringify(controller.getStatus()), new RegExp(token));
  await controller.stop();
});

test('dashboard serves the console and loopback API without starting a child', async (t) => {
  let startRequest;
  const fakeController = {
    getStatus: () => ({ state: 'stopped', logs: [] }),
    validateConfig: async () => ({ config: { configPath: 'dai-sync.config.json' }, status: { state: 'stopped', logs: [] } }),
    start: async (request) => {
      startRequest = request;
      return { state: 'running', logs: [] };
    },
    preview: async () => ({ state: 'running', logs: [] }),
    stop: async () => ({ state: 'stopped', logs: [] })
  };
  const server = createDashboardServer({ root: process.cwd(), controller: fakeController });
  t.after(() => server.close());
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const { port } = server.address();
  const page = await fetch(`http://127.0.0.1:${port}/`);
  assert.equal(page.status, 200);
  const pageHtml = await page.text();
  assert.match(pageHtml, /同名模型分组同步/);
  assert.doesNotMatch(pageHtml, /id="apply-confirmation"/);
  assert.match(pageHtml, /apply 仍会实时更新已有分组倍率/);
  const start = await fetch(`http://127.0.0.1:${port}/api/sync/start`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ apply: true })
  });
  assert.equal(start.status, 200);
  assert.deepEqual(startRequest, { apply: true });
  const status = await fetch(`http://127.0.0.1:${port}/api/sync/status`);
  assert.equal(status.status, 200);
  assert.deepEqual((await status.json()).state, 'stopped');
  const externalFile = await fetch(`http://127.0.0.1:${port}/dai-sync.config.example.json`);
  assert.equal(externalFile.status, 404);
});

test('desktop launcher skips an occupied legacy port and opens the selected port', async () => {
  const probes = [];
  const selected = await chooseDashboardPort({
    startPort: 4173,
    probePort: async (port) => {
      probes.push(port);
      return port === 4173 ? 'occupied' : 'free';
    }
  });
  assert.deepEqual(selected, { port: 4174, reused: false });
  assert.deepEqual(probes, [4173, 4174]);

  const opened = [];
  let spawned = 0;
  const child = { exitCode: null, kill() {} };
  const launched = await launchDashboard({
    startPort: 4173,
    choosePort: async () => ({ port: 4174, reused: false }),
    probePort: async () => 'dashboard',
    spawnServer: () => { spawned += 1; return child; },
    openApp: (url) => opened.push(url),
    log: () => {}
  });
  assert.equal(spawned, 1);
  assert.equal(launched.url, 'http://127.0.0.1:4174/');
  assert.deepEqual(opened, ['http://127.0.0.1:4174/']);

  const reusedOpened = [];
  const reused = await launchDashboard({
    choosePort: async () => ({ port: 4173, reused: true }),
    openApp: (url) => reusedOpened.push(url),
    log: () => {}
  });
  assert.equal(reused.child, null);
  assert.deepEqual(reusedOpened, ['http://127.0.0.1:4173/']);
});
