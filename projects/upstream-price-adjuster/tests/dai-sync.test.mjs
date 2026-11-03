import test from 'node:test';
import assert from 'node:assert/strict';
import { main, parseCliArguments } from '../bin/dai-group-sync.mjs';
import { createDaiRestAdapter } from '../src/dai-rest-adapter.js';
import { createDaiSyncPlan } from '../src/dai-sync-core.js';
import { createDaiLifecyclePlan } from '../src/dai-lifecycle-core.js';
import { runDaiLifecycleCycle, runDaiSyncCycle } from '../src/dai-sync-runner.js';

function makeAdapter({ upstreams = [], groups = [], updateGroup = async () => {} } = {}) {
  return {
    upstreamSchema: { idField: 'id', nameField: 'name', multiplierField: 'tenant_multiplier' },
    groupSchema: { idField: 'id', nameField: 'name', multiplierField: 'default_user_multiplier' },
    async listUpstreams() { return upstreams; },
    async listGroups() { return groups; },
    updateGroup
  };
}

test('plans an exact-name mapping at upstream multiplier plus 0.02', () => {
  const plan = createDaiSyncPlan({
    upstreams: [{ id: 'u-1', name: 'gpt-5', tenant_multiplier: 0.2 }],
    groups: [{ id: 'g-1', name: 'gpt-5', default_user_multiplier: 0.1 }]
  });

  assert.equal(plan.adjustment, 0.02);
  assert.equal(plan.updates.length, 1);
  assert.equal(plan.updates[0].desiredMultiplier, 0.22);
  assert.equal(plan.updates[0].groupId, 'g-1');
});

test('supports a custom adjustment while preserving the default', () => {
  const plan = createDaiSyncPlan({
    upstreams: [{ id: 'u-1', name: 'gpt-5', tenant_multiplier: 0.2 }],
    groups: [{ id: 'g-1', name: 'gpt-5', default_user_multiplier: 0.1 }]
  }, { adjustment: 0.005 });

  assert.equal(plan.adjustment, 0.005);
  assert.equal(plan.updates[0].desiredMultiplier, 0.205);
});

test('rejects an adjustment outside the supported range', () => {
  assert.throws(() => createDaiSyncPlan({ upstreams: [], groups: [] }, { adjustment: 1.01 }), /同步调整值/);
  assert.throws(() => createDaiSyncPlan({ upstreams: [], groups: [] }, { adjustment: -0.001 }), /同步调整值/);
});

test('parses and validates the CLI adjustment override', () => {
  assert.equal(parseCliArguments(['--adjustment', '0.005']).adjustment, 0.005);
  assert.throws(() => parseCliArguments(['--adjustment', '1.1']), /--adjustment/);
});

test('builds lifecycle actions in group, target, then rule order and includes stale manual groups', () => {
  const plan = createDaiLifecyclePlan({
    upstreams: [{
      id: 'u-1',
      name: 'vendor-a',
      tenant_multiplier: 0.2,
      models: [{ model_code: 'model-a', api_format: 'openai_responses', capability_type: 'chat', availability: 'available' }]
    }],
    groups: [{ id: 'g-old', name: 'manual-group', description: '手工分组', retail_price_book_id: 'book-1', status: 'active' }],
    targetsByGroup: { 'g-old': [] },
    rulesByGroup: { 'g-old': [] }
  });

  assert.deepEqual(plan.actions.map((action) => action.type), ['create-group', 'associate-upstream', 'create-rule']);
  assert.equal(plan.actions[2].modelCode, 'model-a');
  assert.equal(plan.actions[2].model.api_format, 'openai_responses');
  assert.deepEqual(plan.stale.map((action) => action.type), ['disable-group']);
  assert.equal(plan.stale[0].groupId, 'g-old');
});

test('lifecycle dry-run never calls lifecycle writes and skips existing records', async () => {
  const calls = [];
  const adapter = {
    async listUpstreams() { return [{ id: 'u-1', name: 'vendor-a', models: [{ model_code: 'model-a', api_format: 'openai_responses', capability_type: 'chat', availability: 'available' }] }]; },
    async listGroups() { return [{ id: 'g-1', name: 'vendor-a', status: 'active' }]; },
    async listTargets() { return [{ account_id: 'u-1', status: 'active' }]; },
    async listRules() { return [{ id: 'r-1', client_surface: 'openai_responses', match_type: 'exact', match_value: 'model-a', target_model_code: 'model-a', status: 'active' }]; },
    async createGroup() { calls.push('create-group'); },
    async associateUpstream() { calls.push('associate'); },
    async createRule() { calls.push('rule'); },
    async disableGroup() { calls.push('disable-group'); },
    async disableRule() { calls.push('disable-rule'); }
  };
  const result = await runDaiLifecycleCycle(adapter, { apply: false, disableStale: true });
  assert.equal(result.plan.actions.length, 0);
  assert.equal(calls.length, 0);
});

test('lifecycle never fills missing targets or rules in an existing group', () => {
  const plan = createDaiLifecyclePlan({
    upstreams: [{
      id: 'u-1',
      name: 'vendor-a',
      tenant_multiplier: 0.2,
      models: [{ model_code: 'new-model', api_format: 'openai_responses', capability_type: 'chat', availability: 'available' }]
    }],
    groups: [{ id: 'g-1', name: 'vendor-a', description: '[dai-lifecycle] vendor-a', retail_price_book_id: 'book-1', status: 'active' }],
    targetsByGroup: { 'g-1': [] },
    rulesByGroup: { 'g-1': [] }
  });
  assert.deepEqual(plan.actions, []);
});

test('existing lifecycle groups still plan live multiplier updates without filling targets or rules', () => {
  const plan = createDaiLifecyclePlan({
    upstreams: [{
      id: 'u-1',
      name: 'vendor-a',
      tenant_multiplier: 0.2,
      models: [{ model_code: 'new-model', api_format: 'openai_responses', capability_type: 'chat', availability: 'available' }]
    }],
    groups: [{ id: 'g-1', name: 'vendor-a', description: '[dai-lifecycle] vendor-a', default_user_multiplier: 0.1, status: 'active' }],
    targetsByGroup: { 'g-1': [] },
    rulesByGroup: { 'g-1': [] }
  });
  assert.equal(plan.updates.length, 1);
  assert.equal(plan.updates[0].desiredMultiplier, 0.22);
  assert.deepEqual(plan.actions, []);
});

test('restores inactive manual groups and rules when upstream models return', () => {
  const plan = createDaiLifecyclePlan({
    upstreams: [{
      id: 'u-1',
      name: 'vendor-a',
      tenant_multiplier: 0.2,
      models: [{ model_code: 'restored', api_format: 'openai_responses', capability_type: 'chat', availability: 'available' }]
    }],
    groups: [{ id: 'g-1', name: 'vendor-a', description: '手工分组', default_user_multiplier: 0.22, status: 'disabled' }],
    targetsByGroup: { 'g-1': [{ account_id: 'u-1', status: 'inactive' }] },
    rulesByGroup: { 'g-1': [{ id: 'r-1', client_surface: 'openai_responses', match_type: 'exact', match_value: 'restored', target_model_code: 'restored', status: 'inactive' }] }
  });
  assert.deepEqual(plan.restore.map((action) => action.type), ['enable-group', 'enable-rule']);
  assert.equal(plan.stale.length, 0);
});

test('does not repeat stale disables for inactive manual resources', () => {
  const plan = createDaiLifecyclePlan({
    upstreams: [],
    groups: [{ id: 'g-manual', name: 'manual', description: 'manual', status: 'disabled' }],
    targetsByGroup: { 'g-manual': [] },
    rulesByGroup: { 'g-manual': [{ id: 'r-1', client_surface: 'openai_responses', match_type: 'exact', match_value: 'gone', target_model_code: 'gone', status: 'inactive' }] }
  });
  assert.equal(plan.restore.length, 0);
  assert.equal(plan.stale.length, 0);
});
test('lifecycle runner uses the selected adjustment for new group multipliers', async () => {
  const adapter = {
    async listUpstreams() {
      return [{
        id: 'u-1',
        name: 'vendor-a',
        tenant_multiplier: 0.2,
        models: [{ model_code: 'model-a', api_format: 'openai_responses', capability_type: 'chat', availability: 'available' }]
      }];
    },
    async listGroups() {
      return [{ id: 'g-template', name: 'template', retail_price_book_id: 'book-1', status: 'active' }];
    },
    async listTargets() { return []; },
    async listRules() { return []; }
  };
  const result = await runDaiLifecycleCycle(adapter, { adjustment: 0.005, decimals: 4 });
  assert.equal(result.plan.actions[0].type, 'create-group');
  assert.equal(result.plan.actions[0].desiredMultiplier, 0.205);
});

test('does not plan stale rule disables even for precisely matched manual groups', () => {
  const input = {
    upstreams: [{
      id: 'u-1',
      name: 'vendor-a',
      tenant_multiplier: 0.2,
      models: [{ model_code: 'available', api_format: 'openai_responses', capability_type: 'chat', availability: 'available' }]
    }],
    groups: [{ id: 'g-1', name: 'vendor-a', description: 'manual group', status: 'active' }],
    targetsByGroup: { 'g-1': [{ account_id: 'u-1', status: 'active' }] },
    rulesByGroup: { 'g-1': [{ id: 'r-stale', client_surface: 'openai_responses', match_type: 'exact', match_value: 'gone', target_model_code: 'gone', status: 'active' }] }
  };

  const plan = createDaiLifecyclePlan(input);
  assert.equal(plan.stale.length, 0);
  assert.equal('disableRules' in plan.summary, false);
});

test('lifecycle apply updates existing multipliers before restore actions and keeps lifecycle writes separate', async () => {
  const calls = [];
  const adapter = {
    async listUpstreams() { return [{ id: 'u-1', name: 'vendor-a', tenant_multiplier: 0.2, models: [{ model_code: 'restored', api_format: 'openai_responses', capability_type: 'chat', availability: 'available' }] }]; },
    async listGroups() { return [{ id: 'g-1', name: 'vendor-a', description: '[dai-lifecycle] vendor-a', default_user_multiplier: 0.1, status: 'disabled' }]; },
    async listTargets() { return [{ account_id: 'u-1', status: 'inactive' }]; },
    async listRules() { return [{ id: 'r-1', client_surface: 'openai_responses', match_type: 'exact', match_value: 'restored', target_model_code: 'restored', status: 'inactive' }]; },
    async updateGroup(input) { calls.push(['update', input.groupId, input.desiredMultiplier]); },
    async enableGroup(input) { calls.push(['enable-group', input.groupId]); },
    async enableRule(input) { calls.push(['enable-rule', input.groupId, input.ruleId]); }
  };
  const result = await runDaiLifecycleCycle(adapter, { apply: true, enableRestored: true });
  assert.equal(result.failure, null);
  assert.deepEqual(calls, [['update', 'g-1', 0.22], ['enable-group', 'g-1'], ['enable-rule', 'g-1', 'r-1']]);
});
test('lifecycle apply uses the created group id and stops after its first write failure', async () => {
  const calls = [];
  const adapter = {
    async listUpstreams() {
      return [{
        id: 'u-1',
        name: 'vendor-a',
        tenant_multiplier: 0.2,
        models: [
          { model_code: 'model-a', api_format: 'openai_responses', capability_type: 'chat', availability: 'available' },
          { model_code: 'model-b', api_format: 'openai_responses', capability_type: 'chat', availability: 'available' }
        ]
      }];
    },
    async listGroups() { return [{ id: 'g-template', name: 'template', retail_price_book_id: 'book-1', status: 'active' }]; },
    async listTargets() { return []; },
    async listRules() { return []; },
    async createGroup(input) { calls.push(['create-group', input.name]); return { id: 'g-new' }; },
    async associateUpstream(input) { calls.push(['associate', input.groupId, input.upstreamId]); },
    async createRule(input) {
      calls.push(['rule', input.groupId, input.modelCode]);
      if (input.modelCode === 'model-a') throw new Error('rule rejected');
    },
    async disableGroup() { calls.push(['disable-group']); },
    async disableRule() { calls.push(['disable-rule']); }
  };

  const result = await runDaiLifecycleCycle(adapter, { apply: true, disableStale: true });
  assert.equal(result.failure.action.type, 'create-rule');
  assert.deepEqual(calls, [
    ['create-group', 'vendor-a'],
    ['associate', 'g-new', 'u-1'],
    ['rule', 'g-new', 'model-a']
  ]);
});

test('lifecycle requires a usable price book before creating a missing group', () => {
  const plan = createDaiLifecyclePlan({
    upstreams: [{ id: 'u-1', name: 'vendor-a', tenant_multiplier: 0.2, models: [] }],
    groups: []
  });
  assert.equal(plan.actions.length, 0);
  assert.ok(plan.issues.some((issue) => issue.code === 'missing-retail-price-book'));
});
test('skips duplicate, missing, and non-exact names without proposing writes', () => {
  const plan = createDaiSyncPlan({
    upstreams: [
      { id: 'u-1', name: 'same', tenant_multiplier: 0.2 },
      { id: 'u-2', name: 'same', tenant_multiplier: 0.3 },
      { id: 'u-3', name: 'only-upstream', tenant_multiplier: 0.4 }
    ],
    groups: [
      { id: 'g-1', name: 'same', default_user_multiplier: 0.22 },
      { id: 'g-2', name: 'only-group', default_user_multiplier: 0.22 },
      { id: 'g-3', name: 'only-upstream ', default_user_multiplier: 0.42 }
    ]
  });

  assert.equal(plan.updates.length, 0);
  assert.deepEqual(plan.issues.map((issue) => issue.code).sort(), [
    'duplicate-upstream-name',
    'missing-group',
    'missing-upstream',
    'missing-upstream'
  ]);
  assert.ok(plan.issues.some((issue) => issue.name === 'only-upstream ' && issue.code === 'missing-upstream'));
});


test('does not propose an update when the target is already within tolerance', () => {
  const plan = createDaiSyncPlan({
    upstreams: [{ id: 'u-1', name: 'matched', tenant_multiplier: 0.2 }],
    groups: [{ id: 'g-1', name: 'matched', default_user_multiplier: 0.2200000001 }]
  });

  assert.equal(plan.updates.length, 0);
  assert.equal(plan.unchanged.length, 1);
});

test('dry-run does not call the update adapter', async () => {
  let updateCalls = 0;
  const result = await runDaiSyncCycle(makeAdapter({
    upstreams: [{ id: 'u-1', name: 'a', tenant_multiplier: 0.2 }],
    groups: [{ id: 'g-1', name: 'a', default_user_multiplier: 0.1 }],
    updateGroup: async () => { updateCalls += 1; }
  }));

  assert.equal(result.apply, false);
  assert.equal(result.summary.pending, 1);
  assert.equal(updateCalls, 0);
});

test('apply calls the adapter only for planned valid updates', async () => {
  const updates = [];
  const result = await runDaiSyncCycle(makeAdapter({
    upstreams: [
      { id: 'u-1', name: 'a', tenant_multiplier: 0.2 },
      { id: 'u-2', name: 'bad', tenant_multiplier: 'not-a-number' }
    ],
    groups: [
      { id: 'g-1', name: 'a', default_user_multiplier: 0.1 },
      { id: 'g-2', name: 'bad', default_user_multiplier: 0.1 }
    ],
    updateGroup: async (update) => { updates.push(update); }
  }), { apply: true });

  assert.equal(result.updated, 1);
  assert.equal(result.failure, null);
  assert.equal(updates.length, 1);
  assert.equal(updates[0].name, 'a');
  assert.equal(result.plan.issues[0].code, 'invalid-upstream-multiplier');
});

test('invalid target values safely fail planning and never reach updateGroup', async () => {
  let updateCalls = 0;
  const result = await runDaiSyncCycle(makeAdapter({
    upstreams: [{ id: 'u-1', name: 'a', tenant_multiplier: 0.2 }],
    groups: [{ id: 'g-1', name: 'a', default_user_multiplier: -1 }],
    updateGroup: async () => { updateCalls += 1; }
  }), { apply: true });

  assert.equal(result.summary.pending, 0);
  assert.equal(result.updated, 0);
  assert.equal(updateCalls, 0);
  assert.ok(result.plan.issues.some((issue) => issue.code === 'invalid-group-multiplier'));
});

test('REST adapter uses mock fetch, configured paths, and the full group body template', async () => {
  const calls = [];
  const fetchImpl = async (url, init) => {
    calls.push({ url: String(url), init });
    const path = new URL(url).pathname;
    const bodyByPath = {
      '/api/v1/tenants/me/upstream-resources': {
        items: [{ id: 'u-1', name: 'a', tenant_multiplier: 0.2 }]
      },
      '/api/v1/tenants/me/groups': {
        items: [{
          id: 'g-1', name: 'a', description: 'keep me', retail_price_book_id: 'book-1',
          default_user_multiplier: 0.1, user_default_visible: true,
          allow_protocol_conversion: false, sort_order: 100, status: 'active'
        }]
      },
      '/api/v1/tenants/me/groups/g-1': { id: 'g-1' }
    };
    return {
      ok: true,
      status: 200,
      async json() { return bodyByPath[path]; }
    };
  };
  const adapter = createDaiRestAdapter({
    baseUrl: 'http://127.0.0.1:19001',
    allowApply: true,
    headers: { Authorization: { env: 'TEST_SYNC_AUTH' } }
  }, {
    fetchImpl,
    environment: { TEST_SYNC_AUTH: 'test-only-value' }
  });

  const result = await runDaiSyncCycle(adapter, { apply: true });
  assert.equal(result.updated, 1);
  assert.equal(calls.length, 3);
  assert.equal(calls[0].init.method, 'GET');
  assert.equal(calls[2].init.method, 'PATCH');
  assert.equal(calls[2].init.redirect, 'error');
  assert.equal(calls[2].init.headers.Authorization, 'test-only-value');
  assert.deepEqual(JSON.parse(calls[2].init.body), {
    name: 'a',
    description: 'keep me',
    retail_price_book_id: 'book-1',
    default_user_multiplier: 0.22,
    user_default_visible: true,
    allow_protocol_conversion: false,
    sort_order: 100,
    status: 'active'
  });
});

test('REST adapter independently gates group status writes and uses D-AI status endpoints', async () => {
  const calls = [];
  const fetchImpl = async (url, init) => {
    calls.push({ url: String(url), init });
    return { ok: true, status: 200, async json() { return { id: 'ok' }; } };
  };
  const blocked = createDaiRestAdapter({ baseUrl: 'http://127.0.0.1:19001', allowEnableRestored: false, headers: {} }, { fetchImpl });
  await assert.rejects(() => blocked.enableGroup({ groupId: 'g-1' }), /allowEnableRestored/);
  assert.equal(calls.length, 0);
  const adapter = createDaiRestAdapter({ baseUrl: 'http://127.0.0.1:19001', allowEnableRestored: true, allowDisableStale: true, headers: {} }, { fetchImpl });
  await adapter.enableGroup({ groupId: 'g/1' });
  await adapter.disableGroup({ groupId: 'g/2' });
  assert.equal(calls.length, 2);
  assert.equal(new URL(calls[0].url).pathname, '/api/v1/tenants/me/groups/g%2F1/status');
  assert.deepEqual(JSON.parse(calls[0].init.body), { status: 'active' });
  assert.equal(new URL(calls[1].url).pathname, '/api/v1/tenants/me/groups/g%2F2/status');
  assert.deepEqual(JSON.parse(calls[1].init.body), { status: 'disabled' });
});

test('REST adapter exposes sanitized string and structured validation details', async () => {
  const adapter = createDaiRestAdapter({ baseUrl: 'http://127.0.0.1:19001', allowDisableStale: true, headers: {} }, {
    fetchImpl: async () => ({
      ok: false,
      status: 422,
      async json() { return { detail: 'status must be disabled' }; }
    })
  });
  await assert.rejects(
    () => adapter.disableGroup({
      groupId: 'g-1',
      group: { name: 'group', retail_price_book_id: 'book-1' },
      desiredMultiplier: 0.22
    }),
    /HTTP 422.*status must be disabled/
  );
  const structuredAdapter = createDaiRestAdapter({ baseUrl: 'http://127.0.0.1:19001', allowDisableStale: true, headers: {} }, {
    fetchImpl: async () => ({
      ok: false,
      status: 422,
      async json() { return { detail: [{ msg: 'Input should be disabled' }] }; }
    })
  });
  await assert.rejects(
    () => structuredAdapter.disableGroup({
      groupId: 'g-1',
      group: { name: 'group', retail_price_book_id: 'book-1' },
      desiredMultiplier: 0.22
    }),
    /HTTP 422.*Input should be disabled/
  );
});
test('REST adapter has its own allowApply gate even when called outside the CLI', async () => {
  let fetchCalls = 0;
  const adapter = createDaiRestAdapter({
    baseUrl: 'http://127.0.0.1:19001',
    allowApply: false,
    headers: {}
  }, {
    fetchImpl: async () => { fetchCalls += 1; throw new Error('should not fetch'); }
  });

  await assert.rejects(() => adapter.updateGroup({ groupId: 'g-1' }), /allowApply/);
  assert.equal(fetchCalls, 0);
});

test('REST adapter rejects URL credentials, remote plain HTTP, and query-bearing paths', () => {
  assert.throws(() => createDaiRestAdapter({
    baseUrl: 'https://token@example.test',
    headers: {}
  }), /不含路径、查询、片段或凭据/);
  assert.throws(() => createDaiRestAdapter({
    baseUrl: 'http://example.test',
    headers: {}
  }), /必须使用 https/);
  assert.throws(() => createDaiRestAdapter({
    baseUrl: 'http://127.0.0.1:19001',
    headers: {},
    endpoints: { groups: { path: '/groups?token=not-allowed' } }
  }), /不允许查询参数或片段/);
  assert.throws(() => createDaiRestAdapter({
    baseUrl: 'http://127.0.0.1:19001',
    headers: { Authorization: 'raw-token-is-not-allowed' }
  }), /必须引用环境变量/);
});

test('CLI requires an explicit restored-resource safety gate', async () => {
  const output = [];
  const code = await main(['--lifecycle', '--apply', '--enable-restored'], {
    config: { baseUrl: 'http://127.0.0.1:19001', allowApply: true, allowLifecycleApply: true, allowEnableRestored: false, headers: {} },
    fetchImpl: async () => { throw new Error('should not fetch'); },
    write: (line) => output.push(line),
    error: (line) => output.push(line)
  });
  assert.equal(code, 2);
  assert.match(output.join('\n'), /allowEnableRestored/);
});
test('CLI requires both apply safeguards before constructing a request cycle', async () => {
  let fetchCalls = 0;
  const output = [];
  const code = await main(['--apply'], {
    config: { baseUrl: 'http://127.0.0.1:19001', allowApply: false, headers: {} },
    fetchImpl: async () => { fetchCalls += 1; throw new Error('should not fetch'); },
    write: (line) => output.push(line),
    error: (line) => output.push(line)
  });

  assert.equal(code, 2);
  assert.equal(fetchCalls, 0);
  assert.match(output.join('\n'), /未发送任何请求/);
});

test('CLI rejects conflicting explicit write modes in either order', () => {
  assert.throws(() => parseCliArguments(['--apply', '--dry-run']), /不能同时使用/);
  assert.throws(() => parseCliArguments(['--dry-run', '--apply']), /不能同时使用/);
});

test('a fetch failure aborts before any update is attempted', async () => {
  let updateCalls = 0;
  const adapter = {
    upstreamSchema: { idField: 'id', nameField: 'name', multiplierField: 'tenant_multiplier' },
    groupSchema: { idField: 'id', nameField: 'name', multiplierField: 'default_user_multiplier' },
    async listUpstreams() { throw new Error('offline'); },
    async listGroups() { return []; },
    async updateGroup() { updateCalls += 1; }
  };

  await assert.rejects(() => runDaiSyncCycle(adapter), /offline/);
  assert.equal(updateCalls, 0);
});

test('browser session mode uses the saved-session request adapter without environment credentials', async () => {
  const calls = [];
  const browserSession = {
    async request(request) {
      calls.push(request);
      const path = new URL(request.url).pathname;
      if (path.endsWith('/upstream-resources')) {
        return { ok: true, status: 200, body: { items: [{ id: 'u-1', name: 'a', tenant_multiplier: 0.2 }] } };
      }
      if (path.endsWith('/groups') && request.method === 'GET') {
        return { ok: true, status: 200, body: { items: [{ id: 'g-1', name: 'a', default_user_multiplier: 0.1, name: 'a' }] } };
      }
      return { ok: true, status: 200, body: { id: 'g-1' } };
    }
  };
  const adapter = createDaiRestAdapter({
    baseUrl: 'https://example.test',
    allowApply: true,
    headers: {},
    browser: { loginUrl: 'https://example.test', storagePath: 'runtime/test-session.json' }
  }, { browserSession });

  const result = await runDaiSyncCycle(adapter, { apply: true });
  assert.equal(result.updated, 1);
  assert.equal(calls.length, 3);
  assert.equal(calls[2].method, 'PATCH');
  assert.equal(calls[2].body.default_user_multiplier, 0.22);
});

test('browser session rejects requests outside the configured login origin', () => {
  assert.throws(() => createDaiRestAdapter({
    baseUrl: 'https://api.example.test',
    headers: {},
    browser: { loginUrl: 'https://login.example.test', storagePath: 'runtime/test-session.json' }
  }, { browserSession: { async request() {} } }), /同一服务源/);
});
