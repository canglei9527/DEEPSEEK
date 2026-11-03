#!/usr/bin/env node
/*
 * ARID 双模型模式 - 配置 GUI 服务器
 *
 * 零外部依赖：只用 Node 内置模块 + vendored yaml（node_modules/yaml 复制而来）。
 * 只监听 127.0.0.1 固定端口（默认 3456，可用环境变量 ARID_GUI_PORT 覆盖）。
 * 配置目录默认 ~/.dsh，可用 ARID_CONFIG_HOME 覆盖（测试用）。
 *
 * 密钥纪律：GET /api/config 绝不返回任何 API key 值；server 日志不打印 key。
 * 保存时只写新值/变更值，不删除其他键；写文件用原子写（临时文件 + rename）。
 */
import http from 'node:http';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
let YAML;
try {
  YAML = require('./vendor/yaml/dist/index.js');
} catch (error) {
  // 开发模式（仓库内直跑）：回退到 node_modules/yaml
  YAML = require('yaml');
}
const { parse: yamlParse, stringify: yamlStringify } = YAML;

/* ------------------------------------------------------------------ */
/* 路径与配置解析                                                         */
/* ------------------------------------------------------------------ */
// DSH_HOME（若设置）优先于 ARID_CONFIG_HOME（测试覆盖）与默认 ~/.dsh。
const DSH_HOME = process.env.DSH_HOME && process.env.DSH_HOME.trim()
  ? path.resolve(process.env.DSH_HOME)
  : undefined;
const CONFIG_HOME = DSH_HOME
  ? path.dirname(DSH_HOME)
  : process.env.ARID_CONFIG_HOME && process.env.ARID_CONFIG_HOME.trim()
    ? path.resolve(process.env.ARID_CONFIG_HOME)
    : os.homedir();
const DSH_DIR = DSH_HOME ?? path.join(CONFIG_HOME, '.dsh');
const SETTINGS_PATH = path.join(DSH_DIR, 'settings.yaml');
const CREDENTIALS_PATH = path.join(DSH_DIR, '.credentials.yaml');
const DEFAULTS_DIR = path.join(path.dirname(fileURLToPath(import.meta.url)), 'defaults');
const SETTINGS_DEFAULT_PATH = path.join(DEFAULTS_DIR, 'settings.default.yaml');
const CREDENTIALS_DEFAULT_PATH = path.join(DEFAULTS_DIR, 'credentials.default.yaml');
const PORT = (() => {
  const p = Number.parseInt(process.env.ARID_GUI_PORT || '', 10);
  return Number.isFinite(p) && p > 0 && p < 65536 ? String(p) : '3456';
})();
const HOST = '127.0.0.1';

/* ------------------------------------------------------------------ */
/* 常量                                                                  */
/* ------------------------------------------------------------------ */
const REASONING_LEVELS = ['off', 'low', 'medium', 'high', 'max'];
const GATEWAY_APIS = ['openai-completions']; // 常见 OpenAI 兼容网关；可扩展
// 路由名允许字符（避免 YAML 特殊字符）
const ROUTE_KEY_RE = /^[A-Za-z0-9._-]+$/;
const ENV_KEY_RE = /^[A-Za-z_][A-Za-z0-9_]*$/;

/* ------------------------------------------------------------------ */
/* 工具函数                                                               */
/* ------------------------------------------------------------------ */
function readYamlOr(filePath, fallback) {
  try {
    if (!fs.existsSync(filePath)) return fallback;
    const raw = fs.readFileSync(filePath, 'utf8');
    const trimmed = raw.trim();
    if (!trimmed) return fallback;
    const val = yamlParse(trimmed);
    return val === null || val === undefined ? fallback : val;
  } catch (error) {
    return fallback;
  }
}

function writeFileAtomic(filePath, content) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  const tmp = `${filePath}.tmp-${process.pid}-${Date.now()}`;
  fs.writeFileSync(tmp, content, 'utf8');
  try {
    fs.renameSync(tmp, filePath);
  } catch {
    try { fs.unlinkSync(filePath); } catch { /* 文件不存在也允许 */ }
    fs.renameSync(tmp, filePath);
  }
}

function copyIfExists(source, target) {
  if (fs.existsSync(source)) fs.copyFileSync(source, target);
}

// Establish a baseline once. In a source checkout, reuse the legacy GUI
// baseline when present so switching GUI implementations cannot lose it.
function ensureDefaults() {
  fs.mkdirSync(DEFAULTS_DIR, { recursive: true });
  if (!fs.existsSync(SETTINGS_DEFAULT_PATH)) {
    const legacy = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', 'tools', 'dual-model-gui', 'defaults', 'settings.default.yaml');
    copyIfExists(legacy, SETTINGS_DEFAULT_PATH);
    if (!fs.existsSync(SETTINGS_DEFAULT_PATH) && fs.existsSync(SETTINGS_PATH)) fs.copyFileSync(SETTINGS_PATH, SETTINGS_DEFAULT_PATH);
  }
  if (!fs.existsSync(CREDENTIALS_DEFAULT_PATH)) {
    const legacy = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', 'tools', 'dual-model-gui', 'defaults', 'credentials.default.yaml');
    copyIfExists(legacy, CREDENTIALS_DEFAULT_PATH);
    if (!fs.existsSync(CREDENTIALS_DEFAULT_PATH) && fs.existsSync(CREDENTIALS_PATH)) fs.copyFileSync(CREDENTIALS_PATH, CREDENTIALS_DEFAULT_PATH);
  }
}

function restoreDefaults() {
  ensureDefaults();
  if (!fs.existsSync(SETTINGS_DEFAULT_PATH) && !fs.existsSync(CREDENTIALS_DEFAULT_PATH)) {
    throw new Error('默认配置快照不存在');
  }
  const stamp = new Date().toISOString().replace(/[-:TZ.]/g, '').slice(0, 14);
  const settingsBackup = `${SETTINGS_PATH}.bak-restore-${stamp}`;
  const credentialsBackup = `${CREDENTIALS_PATH}.bak-restore-${stamp}`;
  if (fs.existsSync(SETTINGS_PATH)) fs.copyFileSync(SETTINGS_PATH, settingsBackup);
  if (fs.existsSync(CREDENTIALS_PATH)) fs.copyFileSync(CREDENTIALS_PATH, credentialsBackup);
  if (fs.existsSync(SETTINGS_DEFAULT_PATH)) fs.copyFileSync(SETTINGS_DEFAULT_PATH, SETTINGS_PATH);
  if (fs.existsSync(CREDENTIALS_DEFAULT_PATH)) fs.copyFileSync(CREDENTIALS_DEFAULT_PATH, CREDENTIALS_PATH);
  return { settingsBackup, credentialsBackup };
}

/** 读 credentials.yaml，返回 {ENV_NAME: value} 纯对象（值绝不出现在响应里）。 */
function readCredentials() {
  const doc = readYamlOr(CREDENTIALS_PATH, {});
  if (doc && typeof doc === 'object' && !Array.isArray(doc)) {
    const out = {};
    for (const [k, v] of Object.entries(doc)) {
      if (typeof v === 'string') out[k] = v;
    }
    return out;
  }
  return {};
}

function maskKey(key) {
  if (!key) return '';
  if (key.length <= 8) return '*'.repeat(key.length);
  return `${key.slice(0, 3)}***${key.slice(-3)}`;
}

/** 深拷贝 plain-JS 值，避免 yaml stringify 对共享引用发射锚点/别名。 */
function deepClone(value) {
  if (Array.isArray(value)) return value.map(deepClone);
  if (value && typeof value === 'object') {
    const out = {};
    for (const k of Object.keys(value)) out[k] = deepClone(value[k]);
    return out;
  }
  return value;
}

/** 为路由自动生成 apiKeyEnv 名（如 molifang -> MOLIFANG_API_KEY）。 */
function generateEnvName(route) {
  const base = route.toUpperCase().replace(/[^A-Z0-9]+/g, '_').replace(/^_+|_+$/g, '');
  return `${base || 'ROUTE'}_API_KEY`;
}

/** 规范化 doc，确保 llm-pi-ai.providers 存在且为对象，返回 provider 表。 */
function ensureProviders(doc) {
  if (typeof doc !== 'object' || doc === null || Array.isArray(doc)) doc = {};
  if (typeof doc['llm-pi-ai'] !== 'object' || doc['llm-pi-ai'] === null || Array.isArray(doc['llm-pi-ai'])) {
    doc['llm-pi-ai'] = {};
  }
  if (typeof doc['llm-pi-ai'].providers !== 'object' || doc['llm-pi-ai'].providers === null || Array.isArray(doc['llm-pi-ai'].providers)) {
    doc['llm-pi-ai'].providers = {};
  }
  return doc;
}

function routeInfo(routeName, profile, creds) {
  const models = (Array.isArray(profile.models) ? profile.models : [])
    .map((m) => (m && typeof m === 'object' ? { id: String(m.id || ''), contextWindow: m.contextWindow } : { id: '', contextWindow: undefined }))
    .filter((m) => m.id);
  const env = typeof profile.apiKeyEnv === 'string' ? profile.apiKeyEnv : '';
  return {
    route: routeName,
    displayName: typeof profile.displayName === 'string' ? profile.displayName : '',
    api: typeof profile.api === 'string' ? profile.api : '',
    baseURL: typeof profile.baseURL === 'string' ? profile.baseURL : '',
    models,
    reasoning: typeof profile.reasoning === 'string' ? profile.reasoning : '',
    apiKeyEnv: env,
    hasKey: Boolean(env && Object.prototype.hasOwnProperty.call(creds, env)),
  };
}

/** 生成脱敏的 GET 配置。绝不包含任何 key 值。 */
function buildConfigPayload() {
  const doc = readYamlOr(SETTINGS_PATH, {});
  const creds = readCredentials();
  const norm = typeof doc === 'object' && !Array.isArray(doc) ? ensureProviders(doc) : { 'llm-pi-ai': { providers: {} } };
  const providers = norm['llm-pi-ai'].providers;

  const routes = Object.entries(providers).map(([route, profile]) =>
    routeInfo(route, profile && typeof profile === 'object' ? profile : {}, creds),
  );

  // 飞书远程控制段（绝不含密钥值）
  const feishu = (norm['arid-feishu'] && typeof norm['arid-feishu'] === 'object') ? norm['arid-feishu'] : {};
  const fPlanner = (feishu.planner && typeof feishu.planner === 'object') ? feishu.planner : {};
  const fExec = (feishu.executor && typeof feishu.executor === 'object') ? feishu.executor : {};

  const adm = (norm['agent-default-model'] && typeof norm['agent-default-model'] === 'object') ? norm['agent-default-model'] : {};
  const exec = (norm['arid-dual-model'] && typeof norm['arid-dual-model'] === 'object' && norm['arid-dual-model'].executor && typeof norm['arid-dual-model'].executor === 'object')
    ? norm['arid-dual-model'].executor : {};
  const deepseek = (norm['llm-deepseek'] && typeof norm['llm-deepseek'] === 'object') ? norm['llm-deepseek'] : {};

  const plannerProvider = typeof adm.provider === 'string' ? adm.provider : '';
  const executorProvider = typeof exec.provider === 'string' ? exec.provider : '';
  const plannerEnv = providerEnv(providers, plannerProvider);
  const executorEnv = providerEnv(providers, executorProvider);
  // 执行模型的思考程度是 route 级配置（子代理不支持 reasoningEffort）。
  // 若执行 provider 是派生的 <base>-exec 路由，route 列表里已有它自己的 reasoning，直接读取。
  const executorProfile = executorProvider ? providers[executorProvider] : undefined;
  const executorReasoning = (executorProfile && typeof executorProfile === 'object' && typeof executorProfile.reasoning === 'string')
    ? executorProfile.reasoning : '';

  return {
    dokExists: fs.existsSync(SETTINGS_PATH),
    settingsPath: SETTINGS_PATH,
    credentialsPath: CREDENTIALS_PATH,
    configHome: CONFIG_HOME,
    routes,
    planner: {
      provider: plannerProvider,
      model: typeof adm.model === 'string' ? adm.model : '',
      reasoningEffort: typeof adm.reasoningEffort === 'string' ? adm.reasoningEffort : '',
      hasKey: Boolean(plannerProvider && plannerEnv && Object.prototype.hasOwnProperty.call(creds, plannerEnv)),
    },
    executor: {
      provider: executorProvider,
      model: typeof exec.model === 'string' ? exec.model : '',
      reasoningEffort: executorReasoning,
      hasKey: Boolean(executorProvider && executorEnv && Object.prototype.hasOwnProperty.call(creds, executorEnv)),
    },
    llmDeepseekEffort: typeof deepseek.reasoningEffort === 'string' ? deepseek.reasoningEffort : '',
    reasoningLevels: REASONING_LEVELS,
    gatewayApis: GATEWAY_APIS,
    feishu: {
      appId: typeof feishu.appId === 'string' ? feishu.appId : '',
      secretSet: Boolean(creds.FEISHU_APP_SECRET),
      encryptKeySet: Boolean(creds.FEISHU_ENCRYPT_KEY),
      allowedDirs: Array.isArray(feishu.allowedDirs) ? feishu.allowedDirs.filter((d) => typeof d === 'string') : [],
      botOpenId: typeof feishu.botOpenId === 'string' ? feishu.botOpenId : '',
      timeoutSeconds: typeof feishu.timeoutSeconds === 'number' ? feishu.timeoutSeconds : '',
      replyMaxChars: typeof feishu.replyMaxChars === 'number' ? feishu.replyMaxChars : '',
      outputMaxLines: typeof feishu.outputMaxLines === 'number' ? feishu.outputMaxLines : '',
      planner: {
        provider: typeof fPlanner.provider === 'string' ? fPlanner.provider : '',
        model: typeof fPlanner.model === 'string' ? fPlanner.model : '',
        baseUrl: typeof fPlanner.baseUrl === 'string' ? fPlanner.baseUrl : '',
        hasKey: Boolean(creds.ARID_PLANNER_API_KEY),
      },
      executor: {
        provider: typeof fExec.provider === 'string' ? fExec.provider : '',
        model: typeof fExec.model === 'string' ? fExec.model : '',
        baseUrl: typeof fExec.baseUrl === 'string' ? fExec.baseUrl : '',
        hasKey: Boolean(creds.ARID_EXECUTOR_API_KEY),
      },
      tavilyHasKey: Boolean(creds.TAVILY_API_KEY),
    },
  };
}

function providerEnv(providers, routeName) {
  if (!routeName) return '';
  const p = providers[routeName];
  if (p && typeof p === 'object') return typeof p.apiKeyEnv === 'string' ? p.apiKeyEnv : '';
  return '';
}

/** 校验 POST body，返回 {errors:[...]} 或 null。 */
function validatePost(payload) {
  const errors = [];
  if (!payload || typeof payload !== 'object') return ['请求体必须为 JSON 对象'];

  const planner = (payload.planner && typeof payload.planner === 'object') ? payload.planner : {};
  const executor = (payload.executor && typeof payload.executor === 'object') ? payload.executor : {};

  if (typeof planner.provider !== 'string' || !planner.provider.trim() || !ROUTE_KEY_RE.test(planner.provider)) {
    errors.push('规划模型的 provider（路由名）不能为空，且仅允许字母/数字/._-');
  }
  if (planner.provider === '__NEW__') {
    errors.push('规划模型的 provider 为「(新网关…)」占位符，请先填写网关路由名或选择已有路由');
  }
  if (typeof planner.model !== 'string' || !planner.model.trim()) errors.push('规划模型的模型名不能为空');
  if (executor.provider !== undefined && executor.provider !== null &&
      (typeof executor.provider !== 'string' || !executor.provider.trim() || !ROUTE_KEY_RE.test(executor.provider))) {
    errors.push('执行模型的 provider（路由名）不能为空，且仅允许字母/数字/._-');
  }
  if (executor.provider === '__NEW__') {
    errors.push('执行模型的 provider 为「(新网关…)」占位符，请先填写网关路由名或选择已有路由');
  }
  if (executor.model !== undefined && executor.model !== null && (typeof executor.model !== 'string' || !executor.model.trim())) {
    errors.push('执行模型的模型名不能为空');
  }

  for (const role of ['planner', 'executor']) {
    const r = role === 'planner' ? planner : executor;
    const reasonVal = r.reasoning !== undefined ? r.reasoning : r.reasoningEffort;
    if (reasonVal !== undefined && reasonVal !== null && reasonVal !== '' && !REASONING_LEVELS.includes(reasonVal)) {
      errors.push(`${role} 的思考程度只能是 ${REASONING_LEVELS.join('/')}`);
    }
    if (r.apiKey !== undefined && r.apiKey !== null && r.apiKey !== '') {
      const k = String(r.apiKey);
      if (k.length < 8) errors.push(`${role} 的 API key 长度至少 8 位`);
    }
  }

  const gateways = Array.isArray(payload.gateways) ? payload.gateways : [];
  for (const g of gateways) {
    if (!g || typeof g !== 'object') { errors.push('网关配置格式错误'); continue; }
    if (typeof g.route !== 'string' || !g.route.trim() || !ROUTE_KEY_RE.test(g.route)) {
      errors.push(`网关路由名非法：${(g && g.route) || ''}`);
    }
    if (typeof g.baseURL !== 'string' || !/^https?:\/\/.+/.test(g.baseURL.trim())) {
      errors.push(`网关 ${g.route || ''} 的 baseURL 必须以 http:// 或 https:// 开头`);
    }
    if (g.apiKey !== undefined && g.apiKey !== null && g.apiKey !== '' && String(g.apiKey).length < 8) {
      errors.push(`网关 ${g.route || ''} 的 API key 长度至少 8 位`);
    }
    if (g.models !== undefined && !Array.isArray(g.models)) errors.push(`网关 ${g.route || ''} 的 models 必须是数组`);
  }

  // 飞书远程控制段
  if (payload.feishu !== undefined && payload.feishu !== null) {
    if (typeof payload.feishu !== 'object' || Array.isArray(payload.feishu)) {
      errors.push('飞书配置格式错误：feishu 必须是对象');
    } else {
      const fs = payload.feishu;
      if (fs.appId !== undefined && fs.appId !== null && fs.appId !== '' && typeof fs.appId === 'string' && !/^cli_/.test(fs.appId.trim())) {
        errors.push('飞书 App ID 必须（非空时）以 cli_ 开头');
      }
      if (fs.appId !== undefined && fs.appId !== null && fs.appId !== '' && typeof fs.appId !== 'string') {
        errors.push('飞书 App ID 必须是字符串');
      }
      if (fs.allowedDirs !== undefined && fs.allowedDirs !== null && !Array.isArray(fs.allowedDirs)) {
        errors.push('飞书白名单目录必须是数组');
      } else if (Array.isArray(fs.allowedDirs)) {
        for (const d of fs.allowedDirs) {
          if (typeof d !== 'string' || !d.trim()) errors.push('飞书白名单目录中的每一项都必须是非空字符串');
        }
      }
      for (const field of ['timeoutSeconds', 'replyMaxChars', 'outputMaxLines']) {
        if (fs[field] !== undefined && fs[field] !== null && fs[field] !== '') {
          const n = Number(fs[field]);
          if (!Number.isInteger(n) || n < 0) errors.push(`飞书 ${field} 必须是非负整数`);
        }
      }
      const fPlanner = fs.planner && typeof fs.planner === 'object' ? fs.planner : {};
      const fExec = fs.executor && typeof fs.executor === 'object' ? fs.executor : {};
      for (const [label, r] of [['飞书高等级模型', fPlanner], ['飞书低等级模型', fExec]]) {
        if (r.model && typeof r.model === 'string' && r.model.trim() && (!r.provider || !String(r.provider).trim())) {
          errors.push(`${label}：填了模型名则 provider 必填`);
        }
        if (r.baseUrl !== undefined && r.baseUrl !== '' && r.baseUrl !== null && typeof r.baseUrl === 'string' && !/^https?:\/\/.+/.test(r.baseUrl.trim())) {
          errors.push(`${label} 的 baseUrl 必须以 http:// 或 https:// 开头`);
        }
      }
    }
  }

  return errors;
}

/* ------------------------------------------------------------------ */
/* 保存逻辑                                                               */
/* ------------------------------------------------------------------ */
function applySave(payload) {
  const doc = ensureProviders(readYamlOr(SETTINGS_PATH, {}));
  const providers = doc['llm-pi-ai'].providers;
  const creds = readCredentials();
  const credWrite = {};

  // 1) 删除被移除的自定义网关
  const removedRoutes = Array.isArray(payload.removedRoutes) ? payload.removedRoutes : [];
  for (const rm of removedRoutes) {
    if (typeof rm === 'string' && rm && Object.prototype.hasOwnProperty.call(providers, rm)) {
      delete providers[rm];
    }
  }

  // 2) 应用网关定义
  const gateways = Array.isArray(payload.gateways) ? payload.gateways : [];
  const gatewayRoutes = new Set();
  for (const g of gateways) {
    if (!g || typeof g.route !== 'string' || !g.route) continue;
    const route = g.route.trim();
    gatewayRoutes.add(route);
    const existing = providers[route] && typeof providers[route] === 'object' ? providers[route] : {};
    const profile = { ...existing };
    if (typeof g.displayName === 'string' && g.displayName) profile.displayName = g.displayName;
    if (typeof g.api === 'string' && g.api) profile.api = g.api;
    if (typeof g.baseURL === 'string' && g.baseURL) profile.baseURL = g.baseURL.trim();
    if (Array.isArray(g.models)) {
      profile.models = g.models
        .map((m) => {
          if (!m || typeof m !== 'object') return null;
          const entry = { id: typeof m.id === 'string' ? m.id.trim() : '' };
          if (!entry.id) return null;
          if (m.contextWindow !== undefined && m.contextWindow !== null && m.contextWindow !== '') {
            const n = Number(m.contextWindow);
            if (Number.isFinite(n) && n > 0) entry.contextWindow = Math.floor(n);
          }
          return entry;
        })
        .filter(Boolean);
    }
    // apiKeyEnv：保留已有；否则根据 key/存在性自动生成
    if (typeof profile.apiKeyEnv !== 'string' || !profile.apiKeyEnv) {
      const envName = generateEnvName(route);
      profile.apiKeyEnv = envName;
    }
    providers[route] = profile;
    // 网关自带 key
    if (g.apiKey && typeof g.apiKey === 'string' && String(g.apiKey).length >= 8) {
      credWrite[profile.apiKeyEnv] = String(g.apiKey);
    }
  }

  // 3) 角色配置
  const planner = (payload.planner && typeof payload.planner === 'object') ? payload.planner : {};
  const executor = (payload.executor && typeof payload.executor === 'object') ? payload.executor : {};

  const plannerProvider = planner.provider.trim();
  const plannerModel = planner.model.trim();
  const plannerReasonRaw = planner.reasoning !== undefined ? planner.reasoning : planner.reasoningEffort;
  const plannerReason = plannerReasonRaw === 'off' || !plannerReasonRaw ? '' : (REASONING_LEVELS.includes(plannerReasonRaw) ? plannerReasonRaw : '');

  const executorProviderUnmapped = executor.provider ? executor.provider.trim() : '';
  const executorModel = executor.model ? executor.model.trim() : '';
  const executorReasonRaw = executor.reasoning !== undefined ? executor.reasoning : executor.reasoningEffort;
  const executorReason = executorReasonRaw === 'off' || !executorReasonRaw ? '' : (REASONING_LEVELS.includes(executorReasonRaw) ? executorReasonRaw : '');

  // agent-default-model（规划模型）
  doc['agent-default-model'] = { provider: plannerProvider, model: plannerModel };
  if (plannerReason) doc['agent-default-model'].reasoningEffort = plannerReason;

  // 规划路由 reasoning：确保路由存在（缺失则建最小条目，便于 key/reasoning 落盘），再设置 reasoning
  if (plannerProvider) {
    if (!Object.prototype.hasOwnProperty.call(providers, plannerProvider) || typeof providers[plannerProvider] !== 'object' || providers[plannerProvider] === null) {
      const envName = generateEnvName(plannerProvider);
      providers[plannerProvider] = { apiKeyEnv: envName };
    }
    if (plannerReason) providers[plannerProvider].reasoning = plannerReason;
    else delete providers[plannerProvider].reasoning;
  }

  // 执行路由 reasoning 冲突处理：若执行路由与规划路由相同且思考程度不同 -> 派生 <route>-exec
  let derivedNotice = '';
  let effectiveExecProvider = executorProviderUnmapped;
  const needExecReasoning = Boolean(executorReason);

  if (plannerProvider && executorProviderUnmapped && executorProviderUnmapped === plannerProvider && needExecReasoning && plannerReason !== executorReason) {
    const derived = `${executorProviderUnmapped}-exec`;
    // 复制原路由并加 reasoning
    const base = providers[executorProviderUnmapped] && typeof providers[executorProviderUnmapped] === 'object'
      ? { ...providers[executorProviderUnmapped] }
      : {};
    providers[derived] = { ...deepClone(base), reasoning: executorReason };
    effectiveExecProvider = derived;
    derivedNotice = `已自动派生独立路由「${derived}」用于执行模型（与规划路由同源但思考程度不同，避免互相污染）。`;
  } else if (executorProviderUnmapped && needExecReasoning) {
    // 执行路由与规划不同，或思考程度相同：直接在执行路由上设置 reasoning
    if (Object.prototype.hasOwnProperty.call(providers, executorProviderUnmapped) && typeof providers[executorProviderUnmapped] === 'object') {
      providers[executorProviderUnmapped].reasoning = executorReason;
    } else {
      // 路由不存在（如 catalog 路由）-> 建最小条目
      const envName = generateEnvName(executorProviderUnmapped);
      providers[executorProviderUnmapped] = { apiKeyEnv: envName, reasoning: executorReason };
    }
  } else if (executorProviderUnmapped) {
    // 不需要执行 reasoning：确保路由上不残留旧 reasoning（若与规划路由不同则不触碰）
    // 仅在执行路由与规划路由不同时才清理，避免删掉规划要用的 reasoning
  }

  // 清理不再被引用的历史派生路由（<base>-exec，且带 reasoning 的才算自动派生的）。
  // 仅当它不再是当前执行 provider / 规划 provider，且其 base 路由仍存在时删除。
  const activeRoutes = new Set([effectiveExecProvider, plannerProvider].filter(Boolean));
  for (const r of Object.keys(providers)) {
    const m = /^(.+)-exec$/.exec(r);
    if (!m) continue;
    const base = m[1];
    if (!activeRoutes.has(r) && Object.prototype.hasOwnProperty.call(providers, base)
        && providers[r] && typeof providers[r] === 'object' && providers[r].reasoning) {
      delete providers[r];
    }
  }

  // arid-dual-model.executor
  doc['arid-dual-model'] = doc['arid-dual-model'] && typeof doc['arid-dual-model'] === 'object' ? doc['arid-dual-model'] : {};
  doc['arid-dual-model'].executor = { provider: effectiveExecProvider, model: executorModel };

  // 角色级 key：写入对应路由的 apiKeyEnv
  const plannerEnv = providerEnv(providers, plannerProvider);
  if (planner.apiKey && typeof planner.apiKey === 'string' && String(planner.apiKey).length >= 8) {
    if (plannerEnv) credWrite[plannerEnv] = String(planner.apiKey);
  }
  const execEnv = providerEnv(providers, effectiveExecProvider);
  if (executor.apiKey && typeof executor.apiKey === 'string' && String(executor.apiKey).length >= 8) {
    if (execEnv) credWrite[execEnv] = String(executor.apiKey);
  }

  // 3b) 飞书远程控制段
  let feishuTouched = false;
  if (payload.feishu !== undefined && payload.feishu !== null) {
    const fs = payload.feishu;
    feishuTouched = true;
    doc['arid-feishu'] = doc['arid-feishu'] && typeof doc['arid-feishu'] === 'object' ? doc['arid-feishu'] : {};

    // settings：空字符串字段不写（删除）；数字字段仅非空写入
    if (typeof fs.appId === 'string' && fs.appId.trim()) doc['arid-feishu'].appId = fs.appId.trim();
    else if (fs.appId !== undefined && fs.appId !== null && fs.appId === '') delete doc['arid-feishu'].appId;

    if (Array.isArray(fs.allowedDirs)) {
      const dirs = fs.allowedDirs.map((d) => String(d).trim()).filter(Boolean);
      if (dirs.length) doc['arid-feishu'].allowedDirs = dirs;
      else delete doc['arid-feishu'].allowedDirs;
    } else if (fs.allowedDirs !== undefined && fs.allowedDirs === null) {
      delete doc['arid-feishu'].allowedDirs;
    }

    if (typeof fs.botOpenId === 'string' && fs.botOpenId.trim()) doc['arid-feishu'].botOpenId = fs.botOpenId.trim();
    else if (fs.botOpenId !== undefined && fs.botOpenId === '') delete doc['arid-feishu'].botOpenId;

    for (const field of ['timeoutSeconds', 'replyMaxChars', 'outputMaxLines']) {
      const v = fs[field];
      if (v !== undefined && v !== null && v !== '' && Number.isInteger(Number(v)) && Number(v) >= 0) {
        doc['arid-feishu'][field] = Number(v);
      } else if (v === '' || v === null) {
        delete doc['arid-feishu'][field];
      }
    }

    // planner / executor 模型段
    if (fs.planner !== undefined && fs.planner !== null && typeof fs.planner === 'object') {
      doc['arid-feishu'].planner = doc['arid-feishu'].planner && typeof doc['arid-feishu'].planner === 'object' ? doc['arid-feishu'].planner : {};
      const p = fs.planner;
      if (p.provider !== undefined && p.provider !== null && String(p.provider).trim()) doc['arid-feishu'].planner.provider = String(p.provider).trim();
      else if (p.provider === '') delete doc['arid-feishu'].planner.provider;
      if (typeof p.model === 'string' && p.model.trim()) doc['arid-feishu'].planner.model = p.model.trim();
      else if (p.model === '') delete doc['arid-feishu'].planner.model;
      if (typeof p.baseUrl === 'string' && p.baseUrl.trim()) doc['arid-feishu'].planner.baseUrl = p.baseUrl.trim();
      else if (p.baseUrl === '') delete doc['arid-feishu'].planner.baseUrl;
      if (p.apiKey && typeof p.apiKey === 'string' && String(p.apiKey).length >= 8) {
        credWrite.ARID_PLANNER_API_KEY = String(p.apiKey);
      }
    }
    if (fs.executor !== undefined && fs.executor !== null && typeof fs.executor === 'object') {
      doc['arid-feishu'].executor = doc['arid-feishu'].executor && typeof doc['arid-feishu'].executor === 'object' ? doc['arid-feishu'].executor : {};
      const e = fs.executor;
      if (e.provider !== undefined && e.provider !== null && String(e.provider).trim()) doc['arid-feishu'].executor.provider = String(e.provider).trim();
      else if (e.provider === '') delete doc['arid-feishu'].executor.provider;
      if (typeof e.model === 'string' && e.model.trim()) doc['arid-feishu'].executor.model = e.model.trim();
      else if (e.model === '') delete doc['arid-feishu'].executor.model;
      if (typeof e.baseUrl === 'string' && e.baseUrl.trim()) doc['arid-feishu'].executor.baseUrl = e.baseUrl.trim();
      else if (e.baseUrl === '') delete doc['arid-feishu'].executor.baseUrl;
      if (e.apiKey && typeof e.apiKey === 'string' && String(e.apiKey).length >= 8) {
        credWrite.ARID_EXECUTOR_API_KEY = String(e.apiKey);
      }
    }

    // 飞书级凭证
    if (fs.appSecret && typeof fs.appSecret === 'string' && String(fs.appSecret).length >= 8) {
      credWrite.FEISHU_APP_SECRET = String(fs.appSecret);
    }
    if (fs.encryptKey && typeof fs.encryptKey === 'string' && String(fs.encryptKey).length >= 8) {
      credWrite.FEISHU_ENCRYPT_KEY = String(fs.encryptKey);
    }
    if (fs.tavilyApiKey && typeof fs.tavilyApiKey === 'string' && String(fs.tavilyApiKey).length >= 8) {
      credWrite.TAVILY_API_KEY = String(fs.tavilyApiKey);
    }
  }

  // 4) 写 settings.yaml（原子）
  writeFileAtomic(SETTINGS_PATH, yamlStringify(doc));

  // 5) 写 credentials.yaml（合并，只写新值/变更值）
  if (Object.keys(credWrite).length > 0) {
    for (const [k, v] of Object.entries(credWrite)) {
      creds[k] = v;
    }
    // 按 ENV 名排序输出，保持稳定
    const out = {};
    for (const k of Object.keys(creds).sort()) out[k] = creds[k];
    writeFileAtomic(CREDENTIALS_PATH, yamlStringify(out));
  }

  const feishuHint = feishuTouched ? ' 飞书配置已保存，运行 feishu-worker\\start-feishu.bat 启动长连接。' : '';
  return {
    ok: true,
    settingsPath: SETTINGS_PATH,
    credentialsWritten: Object.keys(credWrite).map((k) => ({ envName: k, masked: maskKey(credWrite[k]) })),
    derivedRoute: derivedNotice ? effectiveExecProvider : '',
    notice: derivedNotice,
    message: derivedNotice
      ? `已保存。${derivedNotice} 请重启 dsh web（start-arid.bat）或新建会话后生效。${feishuHint}`
      : `已保存。请重启 dsh web（start-arid.bat）或新建会话后生效。${feishuHint}`,
  };
}

/* ------------------------------------------------------------------ */
/* HTTP 服务器                                                           */
/* ------------------------------------------------------------------ */
function sendJson(res, code, obj) {
  const body = JSON.stringify(obj);
  res.writeHead(code, {
    'Content-Type': 'application/json; charset=utf-8',
    'Cache-Control': 'no-store',
    'X-Content-Type-Options': 'nosniff',
  });
  res.end(body);
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    req.on('data', (c) => chunks.push(c));
    req.on('end', () => resolve(Buffer.concat(chunks).toString('utf8')));
    req.on('error', reject);
  });
}

let indexHtmlCache = null;
function getIndexHtml() {
  if (indexHtmlCache) return indexHtmlCache;
  const here = path.dirname(fileURLToPath(import.meta.url));
  indexHtmlCache = fs.readFileSync(path.join(here, 'index.html'), 'utf8');
  return indexHtmlCache;
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, `http://${req.headers.host || '127.0.0.1'}`);
  const u = url.pathname;

  try {
    if (u === '/' || u === '/index.html') {
      const html = getIndexHtml();
      res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store' });
      res.end(html);
      return;
    }

    if (u === '/api/config') {
      if (req.method === 'GET') {
        sendJson(res, 200, buildConfigPayload());
        return;
      }
      if (req.method === 'POST') {
        let payload;
        try {
          const raw = await readBody(req);
          payload = raw ? JSON.parse(raw) : {};
        } catch {
          sendJson(res, 400, { error: '请求体不是合法的 JSON' });
          return;
        }
        const errors = validatePost(payload);
        if (errors.length) {
          sendJson(res, 400, { error: `保存失败：${errors.join('；')}` });
          return;
        }
        try {
          const result = applySave(payload);
          sendJson(res, 200, result);
        } catch (error) {
          sendJson(res, 500, { error: `保存失败：${String(error && error.message ? error.message : error)}` });
        }
        return;
      }
      sendJson(res, 405, { error: '仅支持 GET / POST' });
      return;
    }

    if (u === '/api/defaults' && req.method === 'GET') {
      ensureDefaults();
      sendJson(res, 200, {
        settingsExists: fs.existsSync(SETTINGS_DEFAULT_PATH),
        credentialsExists: fs.existsSync(CREDENTIALS_DEFAULT_PATH),
      });
      return;
    }

    if (u === '/api/defaults/restore' && req.method === 'POST') {
      const result = restoreDefaults();
      sendJson(res, 200, { ok: true, ...result, message: '已还原默认配置，请重启 dsh web 或新建会话后生效。' });
      return;
    }

    sendJson(res, 404, { error: '未找到页面' });
  } catch (error) {
    sendJson(res, 500, { error: `服务器错误：${String(error && error.message ? error.message : error)}` });
  }
});

server.listen(Number(PORT), HOST, () => {
  // 日志不打印任何 key
  console.log(`[arid-config-gui] 配置目录: ${CONFIG_HOME}\\.dsh`);
  console.log(`[arid-config-gui] settings: ${SETTINGS_PATH}`);
  console.log(`[arid-config-gui] 监听 http://${HOST}:${PORT}  (Ctrl+C 退出)`);
  try { ensureDefaults(); } catch { /* 快照可在还原时重试 */ }
});
