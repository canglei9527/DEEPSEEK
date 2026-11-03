import {
  DEFAULT_GROUP_SCHEMA,
  DEFAULT_SYNC_DECIMALS,
  DEFAULT_SYNC_INTERVAL_MS,
  DEFAULT_SYNC_TOLERANCE,
  DEFAULT_UPSTREAM_SCHEMA,
  validateSyncInterval
} from './dai-sync-core.js';
import { createDaiBrowserSession, normalizeDaiBrowserConfig } from './dai-browser-session.js';

const SAFE_LITERAL_HEADERS = new Set(['accept', 'content-type', 'user-agent']);
const FORBIDDEN_HEADERS = new Set(['cookie', 'host', 'content-length', 'proxy-authorization']);
const ENV_NAME = /^[A-Za-z_][A-Za-z0-9_]*$/;
const WHOLE_TEMPLATE = /^\{\{([A-Za-z][A-Za-z0-9_.]*)\}\}$/;

export const DAI_STATIC_ENDPOINTS = Object.freeze({
  upstreams: Object.freeze({
    method: 'GET',
    path: '/api/v1/tenants/me/upstream-resources',
    itemsPath: 'items',
    fields: DEFAULT_UPSTREAM_SCHEMA
  }),
  groups: Object.freeze({
    method: 'GET',
    path: '/api/v1/tenants/me/groups',
    itemsPath: 'items',
    fields: DEFAULT_GROUP_SCHEMA
  }),
  updateGroup: Object.freeze({
    method: 'PATCH',
    path: '/api/v1/tenants/me/groups/{id}',
    bodyTemplate: Object.freeze({
      name: '{{group.name}}',
      description: '{{group.description}}',
      retail_price_book_id: '{{group.retail_price_book_id}}',
      default_user_multiplier: '{{desiredMultiplier}}',
      user_default_visible: '{{group.user_default_visible}}',
      allow_protocol_conversion: '{{group.allow_protocol_conversion}}',
      sort_order: '{{group.sort_order}}',
      status: '{{group.status}}'
    })
  }),
  targets: Object.freeze({
    method: 'GET',
    path: '/api/v1/tenants/me/groups/{id}/targets',
    itemsPath: 'items'
  }),
  rules: Object.freeze({
    method: 'GET',
    path: '/api/v1/tenants/me/groups/{id}/dispatch-rules',
    itemsPath: 'items'
  }),
  createGroup: Object.freeze({
    method: 'POST',
    path: '/api/v1/tenants/me/groups',
    bodyTemplate: Object.freeze({
      name: '{{name}}',
      description: '{{managedDescription}}',
      retail_price_book_id: '{{retailPriceBookId}}',
      default_user_multiplier: '{{desiredMultiplier}}',
      user_default_visible: '{{userDefaultVisible}}',
      allow_protocol_conversion: '{{allowProtocolConversion}}',
      sort_order: '{{sortOrder}}',
      status: 'active'
    })
  }),
  associateUpstream: Object.freeze({
    method: 'POST',
    path: '/api/v1/tenants/me/groups/{id}/targets',
    bodyTemplate: Object.freeze({
      account_id: '{{upstreamId}}',
      priority: '{{priority}}',
      status: 'active'
    })
  }),
  createRule: Object.freeze({
    method: 'POST',
    path: '/api/v1/tenants/me/groups/{id}/dispatch-rules',
    bodyTemplate: Object.freeze({
      client_surface: '{{clientSurface}}',
      match_type: 'exact',
      match_value: '{{modelCode}}',
      target_model_code: '{{modelCode}}',
      priority: '{{priority}}',
      status: 'active',
      required_capability: '{{requiredCapability}}'
    })
  }),
  disableGroup: Object.freeze({
    method: 'PATCH',
    path: '/api/v1/tenants/me/groups/{id}/status',
    bodyTemplate: Object.freeze({ status: 'disabled' })
  }),
  disableRule: Object.freeze({
    method: 'PATCH',
    path: '/api/v1/tenants/me/groups/{groupId}/dispatch-rules/{ruleId}/status',
    bodyTemplate: Object.freeze({ status: 'inactive' })
  }),
  enableGroup: Object.freeze({
    method: 'PATCH',
    path: '/api/v1/tenants/me/groups/{id}/status',
    bodyTemplate: Object.freeze({ status: 'active' })
  }),
  enableRule: Object.freeze({
    method: 'PATCH',
    path: '/api/v1/tenants/me/groups/{groupId}/dispatch-rules/{ruleId}/status',
    bodyTemplate: Object.freeze({ status: 'active' })
  })
});

const OMIT = Symbol('omit');

function isPlainObject(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function cloneEndpoint(defaultValue, providedValue) {
  return {
    ...defaultValue,
    ...(providedValue ?? {}),
    fields: {
      ...(defaultValue.fields ?? {}),
      ...(providedValue?.fields ?? {})
    }
  };
}

function assertString(value, label) {
  if (typeof value !== 'string' || !value.trim()) {
    throw new Error(`${label} 必须是非空字符串`);
  }
  return value;
}

function assertEndpoint(endpoint, label, { requiresItems = false, requiresTemplate = false } = {}) {
  const method = assertString(endpoint.method, `${label}.method`).toUpperCase();
  const path = assertString(endpoint.path, `${label}.path`);
  if (!path.startsWith('/')) {
    throw new Error(`${label}.path 必须以 / 开头`);
  }
  if (path.includes('?') || path.includes('#')) {
    throw new Error(`${label}.path 不允许查询参数或片段；凭据只能使用环境变量请求头`);
  }
  if (requiresItems) assertString(endpoint.itemsPath, `${label}.itemsPath`);
  if (requiresTemplate && !isPlainObject(endpoint.bodyTemplate)) {
    throw new Error(`${label}.bodyTemplate 必须是对象`);
  }
  return { ...endpoint, method, path };
}

function assertFieldMap(fields, label) {
  const value = { ...fields };
  for (const field of ['idField', 'nameField', 'multiplierField']) {
    assertString(value[field], `${label}.${field}`);
  }
  return value;
}

function normalizeBaseUrl(value) {
  const baseUrl = assertString(value, 'baseUrl');
  let parsed;
  try {
    parsed = new URL(baseUrl);
  } catch {
    throw new Error('baseUrl 必须是有效的 http(s) URL');
  }
  if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
    throw new Error('baseUrl 只支持 http 或 https');
  }
  const loopbackHosts = new Set(['127.0.0.1', 'localhost', '[::1]']);
  if (parsed.protocol === 'http:' && !loopbackHosts.has(parsed.hostname)) {
    throw new Error('非本机 baseUrl 必须使用 https，避免通过明文 HTTP 发送认证请求头');
  }
  if (parsed.username || parsed.password || parsed.search || parsed.hash || parsed.pathname !== '/') {
    throw new Error('baseUrl 必须是不含路径、查询、片段或凭据的服务源地址');
  }
  return parsed.toString();
}

/** Validate and normalize the external JSON configuration without resolving secrets. */
export function normalizeDaiSyncConfig(input) {
  if (!isPlainObject(input)) {
    throw new Error('同步配置必须是 JSON 对象');
  }

  const endpointsInput = input.endpoints ?? {};
  if (!isPlainObject(endpointsInput)) {
    throw new Error('endpoints 必须是对象');
  }
  const upstreams = assertEndpoint(
    cloneEndpoint(DAI_STATIC_ENDPOINTS.upstreams, endpointsInput.upstreams),
    'endpoints.upstreams',
    { requiresItems: true }
  );
  const groups = assertEndpoint(
    cloneEndpoint(DAI_STATIC_ENDPOINTS.groups, endpointsInput.groups),
    'endpoints.groups',
    { requiresItems: true }
  );
  const updateGroup = assertEndpoint(
    cloneEndpoint(DAI_STATIC_ENDPOINTS.updateGroup, endpointsInput.updateGroup),
    'endpoints.updateGroup',
    { requiresTemplate: true }
  );
  const targets = assertEndpoint(
    cloneEndpoint(DAI_STATIC_ENDPOINTS.targets, endpointsInput.targets),
    'endpoints.targets',
    { requiresItems: true }
  );
  const rules = assertEndpoint(
    cloneEndpoint(DAI_STATIC_ENDPOINTS.rules, endpointsInput.rules),
    'endpoints.rules',
    { requiresItems: true }
  );
  const createGroup = assertEndpoint(
    cloneEndpoint(DAI_STATIC_ENDPOINTS.createGroup, endpointsInput.createGroup),
    'endpoints.createGroup',
    { requiresTemplate: true }
  );
  const associateUpstream = assertEndpoint(
    cloneEndpoint(DAI_STATIC_ENDPOINTS.associateUpstream, endpointsInput.associateUpstream),
    'endpoints.associateUpstream',
    { requiresTemplate: true }
  );
  const createRule = assertEndpoint(
    cloneEndpoint(DAI_STATIC_ENDPOINTS.createRule, endpointsInput.createRule),
    'endpoints.createRule',
    { requiresTemplate: true }
  );
  const disableGroup = assertEndpoint(
    cloneEndpoint(DAI_STATIC_ENDPOINTS.disableGroup, endpointsInput.disableGroup),
    'endpoints.disableGroup',
    { requiresTemplate: true }
  );
  const disableRule = assertEndpoint(
    cloneEndpoint(DAI_STATIC_ENDPOINTS.disableRule, endpointsInput.disableRule),
    'endpoints.disableRule',
    { requiresTemplate: true }
  );
  const enableGroup = assertEndpoint(
    cloneEndpoint(DAI_STATIC_ENDPOINTS.enableGroup, endpointsInput.enableGroup),
    'endpoints.enableGroup',
    { requiresTemplate: true }
  );
  const enableRule = assertEndpoint(
    cloneEndpoint(DAI_STATIC_ENDPOINTS.enableRule, endpointsInput.enableRule),
    'endpoints.enableRule',
    { requiresTemplate: true }
  );
  for (const [label, endpoint, placeholders] of [
    ['endpoints.updateGroup', updateGroup, ['{id}']],
    ['endpoints.targets', targets, ['{id}']],
    ['endpoints.rules', rules, ['{id}']],
    ['endpoints.associateUpstream', associateUpstream, ['{id}']],
    ['endpoints.createRule', createRule, ['{id}']],
    ['endpoints.disableGroup', disableGroup, ['{id}']],
    ['endpoints.disableRule', disableRule, ['{groupId}', '{ruleId}']],
    ['endpoints.enableGroup', enableGroup, ['{id}']],
    ['endpoints.enableRule', enableRule, ['{groupId}', '{ruleId}']]
  ]) {
    for (const placeholder of placeholders) {
      if (!endpoint.path.includes(placeholder)) throw new Error(`${label}.path 必须包含 ${placeholder} 占位符`);
    }
  }

  const decimals = input.decimals ?? DEFAULT_SYNC_DECIMALS;
  const adjustment = input.adjustment ?? 0.02;
  const tolerance = input.tolerance ?? DEFAULT_SYNC_TOLERANCE;
  if (!Number.isFinite(adjustment) || adjustment < 0 || adjustment > 1) {
    throw new Error('adjustment 必须是 0 到 1 之间的有限数字');
  }
  const intervalMs = input.intervalMs ?? DEFAULT_SYNC_INTERVAL_MS;
  if (!Number.isInteger(decimals) || decimals < 0 || decimals > 12) {
    throw new Error('decimals 必须是 0 到 12 之间的整数');
  }
  if (!Number.isFinite(tolerance) || tolerance < 0) {
    throw new Error('tolerance 必须是大于等于 0 的有限数字');
  }
  validateHeaderConfiguration(input.headers ?? {});
  const baseUrl = normalizeBaseUrl(input.baseUrl);
  const browser = input.browser === undefined || input.browser === null
    ? null
    : normalizeDaiBrowserConfig(input.browser, { baseUrl });
  if (browser && new URL(browser.loginUrl).origin !== new URL(baseUrl).origin) {
    throw new Error('browser.loginUrl 必须与 baseUrl 使用同一服务源');
  }

  const allowApply = input.allowApply === true;
  const allowLifecycleApply = input.allowLifecycleApply === true;
  const allowDisableStale = input.allowDisableStale === true;
  const allowEnableRestored = input.allowEnableRestored === true;
  return {
    baseUrl,
    allowApply,
    allowLifecycleApply,
    allowDisableStale,
    allowEnableRestored,
    intervalMs: validateSyncInterval(intervalMs),
    decimals,
    adjustment,
    tolerance,
    headers: input.headers ?? {},
    browser,
    endpoints: {
      upstreams: {
        ...upstreams,
        fields: assertFieldMap(upstreams.fields, 'endpoints.upstreams.fields')
      },
      groups: {
        ...groups,
        fields: assertFieldMap(groups.fields, 'endpoints.groups.fields')
      },
      updateGroup,
      targets,
      rules,
      createGroup,
      associateUpstream,
      createRule,
      disableGroup,
      disableRule,
      enableGroup,
      enableRule
    }
  };
}

function getByPath(value, path, label) {
  const keys = assertString(path, label).split('.');
  let current = value;
  for (const key of keys) {
    if (!isPlainObject(current) || !Object.hasOwn(current, key)) {
      throw new Error(`${label} 在响应中不存在`);
    }
    current = current[key];
  }
  if (!Array.isArray(current)) {
    throw new Error(`${label} 必须指向数组`);
  }
  return current;
}

function getTemplateValue(context, path) {
  const keys = path.split('.');
  let current = context;
  for (const key of keys) {
    if (!isPlainObject(current) || !Object.hasOwn(current, key)) return undefined;
    current = current[key];
  }
  return current;
}

function renderTemplate(value, context) {
  if (typeof value === 'string') {
    const match = value.match(WHOLE_TEMPLATE);
    if (match) return getTemplateValue(context, match[1]) ?? OMIT;
    if (value.includes('{{') || value.includes('}}')) {
      throw new Error('请求模板变量必须独占一个字符串值，例如 {{desiredMultiplier}}');
    }
    return value;
  }
  if (Array.isArray(value)) {
    return value.map((item) => {
      const rendered = renderTemplate(item, context);
      return rendered === OMIT ? null : rendered;
    });
  }
  if (isPlainObject(value)) {
    const result = {};
    for (const [key, child] of Object.entries(value)) {
      const rendered = renderTemplate(child, context);
      if (rendered !== OMIT) result[key] = rendered;
    }
    return result;
  }
  if (value === undefined) return OMIT;
  return value;
}

function resolveEnvironmentReference(value, environment, headerName) {
  let envName;
  if (typeof value === 'string') {
    const match = value.match(/^env:([A-Za-z_][A-Za-z0-9_]*)$/);
    if (match) envName = match[1];
  } else if (isPlainObject(value) && typeof value.env === 'string') {
    envName = value.env;
  }

  if (!envName || !ENV_NAME.test(envName)) {
    throw new Error(`请求头 ${headerName} 必须引用环境变量，例如 "env:DAI_SYNC_AUTHORIZATION"`);
  }
  const resolved = environment[envName];
  if (typeof resolved !== 'string' || !resolved) {
    throw new Error(`请求头 ${headerName} 引用的环境变量 ${envName} 未设置`);
  }
  return resolved;
}

function validateHeaderConfiguration(configHeaders) {
  if (!isPlainObject(configHeaders)) {
    throw new Error('headers 必须是对象');
  }
  for (const [name, value] of Object.entries(configHeaders)) {
    const normalizedName = name.toLowerCase();
    if (FORBIDDEN_HEADERS.has(normalizedName)) {
      throw new Error(`不允许配置请求头 ${name}`);
    }
    if (SAFE_LITERAL_HEADERS.has(normalizedName) && typeof value === 'string' && !value.startsWith('env:')) {
      continue;
    } else {
      let envName;
      if (typeof value === 'string') {
        envName = value.match(/^env:([A-Za-z_][A-Za-z0-9_]*)$/)?.[1];
      } else if (isPlainObject(value) && typeof value.env === 'string') {
        envName = value.env;
      }
      if (!envName || !ENV_NAME.test(envName)) {
        throw new Error(`请求头 ${name} 必须引用环境变量，例如 "env:DAI_SYNC_AUTHORIZATION"`);
      }
    }
  }
}

function resolveHeaders(configHeaders, environment) {
  validateHeaderConfiguration(configHeaders);
  const headers = { Accept: 'application/json' };
  for (const [name, value] of Object.entries(configHeaders)) {
    const normalizedName = name.toLowerCase();
    if (SAFE_LITERAL_HEADERS.has(normalizedName) && typeof value === 'string' && !value.startsWith('env:')) {
      headers[name] = value;
    } else {
      headers[name] = resolveEnvironmentReference(value, environment, name);
    }
  }
  return headers;
}

function sanitizeRemoteErrorText(value) {
  if (typeof value !== 'string') return null;
  const detail = value.replace(/[\r\n]+/g, ' ').trim().slice(0, 300);
  if (!detail || /(?:bearer|token|authorization|cookie|password)/i.test(detail)) return null;
  return detail;
}

function safeRemoteErrorDetail(body) {
  if (!isPlainObject(body)) return null;
  const detail = body.detail ?? body.message ?? body.error;
  const text = sanitizeRemoteErrorText(detail);
  if (text) return text;
  if (!Array.isArray(detail)) return null;
  const messages = detail
    .map((item) => isPlainObject(item) ? sanitizeRemoteErrorText(item.msg ?? item.message) : null)
    .filter(Boolean)
    .slice(0, 3);
  return messages.length ? messages.join('; ') : null;
}

function formatRemoteError(response, operation) {
  const detail = safeRemoteErrorDetail(response?.body);
  return new Error(`${operation} 请求失败（HTTP ${response.status}）${detail ? `：${detail}` : ''}`);
}

/**
 * Create a REST adapter. It does not issue any request until one of its methods is called.
 * Credentials are resolved only from environment references when a request is about to run.
 */
export function createDaiRestAdapter(config, {
  fetchImpl = globalThis.fetch,
  environment = process.env,
  browserSession,
  browserRoot = process.cwd()
} = {}) {
  const normalized = normalizeDaiSyncConfig(config);
  let activeBrowserSession = browserSession ?? null;
  if (normalized.browser && !activeBrowserSession) {
    activeBrowserSession = createDaiBrowserSession({ root: browserRoot });
    activeBrowserSession.configure(normalized.browser);
  }
  if (!normalized.browser && typeof fetchImpl !== 'function') {
    throw new Error('当前 Node.js 运行环境没有可用的 fetch');
  }
  if (normalized.browser && !activeBrowserSession) {
    throw new Error('浏览器会话适配器未初始化');
  }

  async function request(endpoint, operation, { id, ids = {}, body } = {}) {
    const path = Object.entries({ id, ...ids }).reduce((current, [key, value]) => {
      if (value === undefined) return current;
      return current.replaceAll(`{${key}}`, encodeURIComponent(String(value)));
    }, endpoint.path);
    if (path.includes('{') || path.includes('}')) {
      throw new Error(`${operation} 的路径含有未解析占位符`);
    }
    const url = new URL(path, normalized.baseUrl);
    const hasBody = body !== undefined;
    const headers = normalized.browser
      ? { Accept: 'application/json', ...(hasBody ? { 'Content-Type': 'application/json' } : {}) }
      : resolveHeaders(normalized.headers, environment);
    if (!normalized.browser && hasBody && !Object.keys(headers).some((name) => name.toLowerCase() === 'content-type')) {
      headers['Content-Type'] = 'application/json';
    }

    let response;
    try {
      if (normalized.browser) {
        response = await activeBrowserSession.request({
          url: url.toString(),
          method: endpoint.method,
          headers,
          body
        });
      } else {
        const init = { method: endpoint.method, headers, redirect: 'error' };
        if (hasBody) init.body = JSON.stringify(body);
        response = await fetchImpl(url, init);
      }
    } catch (error) {
      throw new Error(`${operation} 网络请求失败：${error instanceof Error ? error.message : String(error)}`);
    }
    if (!response?.ok) {
      let errorResponse = response ?? { status: '未知' };
      if (!normalized.browser && response) {
        try {
          errorResponse = { ...response, body: await response.json() };
        } catch {
          // Keep the status-only error when the provider returns no JSON body.
        }
      }
      throw formatRemoteError(errorResponse, operation);
    }
    if (response.status === 204) return null;
    try {
      return normalized.browser ? response.body : await response.json();
    } catch {
      throw new Error(`${operation} 返回的不是有效 JSON`);
    }
  }

  return {
    config: normalized,
    upstreamSchema: normalized.endpoints.upstreams.fields,
    groupSchema: normalized.endpoints.groups.fields,
    async listUpstreams() {
      const body = await request(normalized.endpoints.upstreams, '获取上游资源');
      return getByPath(body, normalized.endpoints.upstreams.itemsPath, 'endpoints.upstreams.itemsPath');
    },
    async listGroups() {
      const body = await request(normalized.endpoints.groups, '获取模型分组');
      return getByPath(body, normalized.endpoints.groups.itemsPath, 'endpoints.groups.itemsPath');
    },
    async listTargets(groupId) {
      const body = await request(normalized.endpoints.targets, '获取分组上游目标', { id: groupId });
      return getByPath(body, normalized.endpoints.targets.itemsPath, 'endpoints.targets.itemsPath');
    },
    async listRules(groupId) {
      const body = await request(normalized.endpoints.rules, '获取分组调度规则', { id: groupId });
      return getByPath(body, normalized.endpoints.rules.itemsPath, 'endpoints.rules.itemsPath');
    },
    async updateGroup(update) {
      if (!normalized.allowApply) {
        throw new Error('安全拦截：配置必须显式设置 allowApply: true 才能更新模型分组');
      }
      if (!update || update.groupId === undefined) {
        throw new Error('更新分组缺少 groupId');
      }
      const body = renderTemplate(normalized.endpoints.updateGroup.bodyTemplate, {
        group: update.group,
        upstream: update.upstream,
        desiredMultiplier: update.desiredMultiplier
      });
      if (!isPlainObject(body) || Object.keys(body).length === 0) {
        throw new Error('更新分组请求体不能为空');
      }
      return request(normalized.endpoints.updateGroup, '更新模型分组', {
        id: update.groupId,
        body
      });
    },
    async createGroup(input) {
      if (!normalized.allowLifecycleApply) throw new Error('安全拦截：配置必须显式设置 allowLifecycleApply: true 才能创建模型分组');
      const body = renderTemplate(normalized.endpoints.createGroup.bodyTemplate, input ?? {});
      return request(normalized.endpoints.createGroup, '创建模型分组', { body });
    },
    async associateUpstream(input) {
      if (!normalized.allowLifecycleApply) throw new Error('安全拦截：配置必须显式设置 allowLifecycleApply: true 才能关联上游目标');
      const body = renderTemplate(normalized.endpoints.associateUpstream.bodyTemplate, input ?? {});
      return request(normalized.endpoints.associateUpstream, '关联上游目标', { id: input?.groupId, body });
    },
    async createRule(input) {
      if (!normalized.allowLifecycleApply) throw new Error('安全拦截：配置必须显式设置 allowLifecycleApply: true 才能创建调度规则');
      const body = renderTemplate(normalized.endpoints.createRule.bodyTemplate, input ?? {});
      return request(normalized.endpoints.createRule, '创建调度规则', { id: input?.groupId, body });
    },
    async disableGroup(input) {
      if (!normalized.allowDisableStale) throw new Error('安全拦截：配置必须显式设置 allowDisableStale: true 才能停用失效模型分组');
      if (input?.groupId === undefined) throw new Error('停用模型分组缺少 groupId');
      const body = renderTemplate(normalized.endpoints.disableGroup.bodyTemplate, input ?? {});
      return request(normalized.endpoints.disableGroup, '停用模型分组', { id: input.groupId, body });
    },
    async disableRule(input) {
      if (!normalized.allowDisableStale) throw new Error('安全拦截：配置必须显式设置 allowDisableStale: true 才能停用失效调度规则');
      const body = renderTemplate(normalized.endpoints.disableRule.bodyTemplate, input ?? {});
      return request(normalized.endpoints.disableRule, '停用调度规则', {
        ids: { groupId: input?.groupId, ruleId: input?.ruleId },
        body
      });
    },
    async enableGroup(input) {
      if (!normalized.allowEnableRestored) throw new Error('安全拦截：配置必须显式设置 allowEnableRestored: true 才能恢复模型分组');
      if (input?.groupId === undefined) throw new Error('恢复模型分组缺少 groupId');
      const body = renderTemplate(normalized.endpoints.enableGroup.bodyTemplate, input ?? {});
      return request(normalized.endpoints.enableGroup, '恢复模型分组', { id: input.groupId, body });
    },
    async enableRule(input) {
      if (!normalized.allowEnableRestored) throw new Error('安全拦截：配置必须显式设置 allowEnableRestored: true 才能恢复调度规则');
      const body = renderTemplate(normalized.endpoints.enableRule.bodyTemplate, input ?? {});
      return request(normalized.endpoints.enableRule, '恢复调度规则', {
        ids: { groupId: input?.groupId, ruleId: input?.ruleId },
        body
      });
    },
    async close() {
      if (normalized.browser && typeof activeBrowserSession?.close === 'function') {
        await activeBrowserSession.close();
      }
    }
  };
}
