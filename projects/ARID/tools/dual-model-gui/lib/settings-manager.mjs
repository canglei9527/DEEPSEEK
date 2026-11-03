// lib/settings-manager.mjs
// Settings.yaml 读写管理：YAML 解析、验证、角色配置补丁

import { readFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { withFileLock, writeFileAtomic } from './file-ops.mjs';

let yaml;
try {
  yaml = await import('yaml');
} catch {
  const entry = join(dirname(new URL(import.meta.url).pathname), '..', 'node_modules', 'yaml', 'dist', 'index.js');
  const url = 'file:///' + encodeURI(entry).replace(/#/g, '%23').replace(/\?/g, '%3F');
  yaml = await import(url);
}

if (typeof yaml.parseDocument !== 'function') {
  throw new Error('yaml package is unavailable; install or vendor it first.');
}

const { parseDocument: parseDocumentLib, Document: DocumentLib } = yaml;

// 导出 YAML 工具函数供 buildApp 使用
export const parseDocument = parseDocumentLib;
export const Document = DocumentLib;

// 角色定义：planner, executor, vision
export const ROLE_DEFS = {
  planner: {
    route: 'arid-planner',
    displayName: 'ARID 规划模型',
    keyEnv: 'ARID_PLANNER_API_KEY',
    section: 'agent-default-model',
  },
  executor: {
    route: 'arid-executor',
    displayName: 'ARID 执行模型',
    keyEnv: 'ARID_EXECUTOR_API_KEY',
    section: 'arid-dual-model',
    sectionKey: 'executor',
  },
  vision: {
    route: 'arid-vision',
    displayName: 'ARID 视觉模型',
    keyEnv: 'ARID_VISION_API_KEY',
    section: 'arid-dual-model',
    sectionKey: 'vision',
  },
};

export const ROLE_NAMES = Object.keys(ROLE_DEFS);

// 支持的 API 格式
export const API_FORMATS = [
  'openai-completions',
  'anthropic-messages',
  'google-generative-ai',
  'openai-responses',
  'mistral-conversations',
  'bedrock-converse-stream',
  'google-vertex',
  'azure-openai-responses',
  'openai-codex-responses',
  'pi-messages',
];

// executor 支持的思考等级
export const SUPPORTED_EXECUTOR_EFFORTS = new Set(['off', 'high', 'max']);

/**
 * 读取 settings.yaml 文本
 */
export async function readSettingsText(settingsPath) {
  return readFile(settingsPath, 'utf8');
}

/**
 * 解析 YAML 文档，抛出友好错误
 */
export function parseDocOrThrow(text) {
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

/**
 * 验证思考等级值
 */
export function validateReasoningEffort(provider, model, effort) {
  const value = typeof effort === 'string' ? effort.trim() : '';
  if (!value) return;
  if ((provider === 'opencode-go' || provider.startsWith('arid-')) && !SUPPORTED_EXECUTOR_EFFORTS.has(value)) {
    const e = new Error(`当前执行路由 ${provider}/${model} 只支持 reasoningEffort: off / high / max；留空使用模型默认。`);
    e.statusCode = 400;
    throw e;
  }
}

/**
 * 补丁 executor 角色配置
 * provider, model, reasoningEffort (可选)
 */
export function patchExecutor(doc, provider, model, reasoningEffort) {
  const root = doc.toJS() ?? {};
  const seg = root?.['arid-dual-model']?.['executor'];
  const curProv = typeof seg?.provider === 'string' ? seg.provider : '';
  const curModel = typeof seg?.model === 'string' ? seg.model : '';
  const hasEffort = typeof seg?.reasoningEffort === 'string';
  const curEffort = hasEffort ? seg.reasoningEffort : '';
  const newEffort = typeof reasoningEffort === 'string' ? reasoningEffort.trim() : '';
  const changed =
    curProv !== provider ||
    curModel !== model ||
    (hasEffort ? curEffort !== newEffort : newEffort !== '');
  if (!changed) return { changed: false };
  const hasSection = root['arid-dual-model'] !== undefined;
  if (!hasSection || root['arid-dual-model']['executor'] === undefined) {
    const obj = { provider, model };
    if (newEffort) obj.reasoningEffort = newEffort;
    doc.setIn(['arid-dual-model', 'executor'], obj);
  } else {
    doc.setIn(['arid-dual-model', 'executor', 'provider'], provider);
    doc.setIn(['arid-dual-model', 'executor', 'model'], model);
    if (newEffort) doc.setIn(['arid-dual-model', 'executor', 'reasoningEffort'], newEffort);
    else doc.deleteIn(['arid-dual-model', 'executor', 'reasoningEffort']);
  }
  return { changed: true };
}

/**
 * 补丁 planner 角色配置
 * provider, model, reasoningEffort (可选)
 */
export function patchPlanner(doc, provider, model, reasoningEffort) {
  const root = doc.toJS() ?? {};
  const cur = root?.['agent-default-model'];
  const curProv = typeof cur?.provider === 'string' ? cur.provider : '';
  const curModel = typeof cur?.model === 'string' ? cur.model : '';
  const hasEffort = typeof cur?.reasoningEffort === 'string';
  const curEffort = hasEffort ? cur.reasoningEffort : '';
  const newEffort = typeof reasoningEffort === 'string' ? reasoningEffort.trim() : '';
  const changed =
    curProv !== provider ||
    curModel !== model ||
    (hasEffort ? curEffort !== newEffort : newEffort !== '');
  if (!changed) return { changed: false };
  if (!root['agent-default-model'] || typeof root['agent-default-model'] !== 'object') {
    const obj = { provider, model };
    if (newEffort) obj.reasoningEffort = newEffort;
    doc.setIn(['agent-default-model'], obj);
  } else {
    doc.setIn(['agent-default-model', 'provider'], provider);
    doc.setIn(['agent-default-model', 'model'], model);
    if (newEffort) doc.setIn(['agent-default-model', 'reasoningEffort'], newEffort);
    else doc.deleteIn(['agent-default-model', 'reasoningEffort']);
  }
  return { changed: true };
}

/**
 * 补丁 vision 角色配置
 */
export function patchVision(doc, provider, model) {
  const root = doc.toJS() ?? {};
  const seg = root?.['arid-dual-model']?.['vision'];
  const curProv = typeof seg?.provider === 'string' ? seg.provider : '';
  const curModel = typeof seg?.model === 'string' ? seg.model : '';
  const changed = curProv !== provider || curModel !== model;
  if (!changed) return { changed: false };
  const hasSection = root['arid-dual-model'] !== undefined;
  if (!hasSection || root['arid-dual-model']['vision'] === undefined) {
    doc.setIn(['arid-dual-model', 'vision'], { provider, model });
  } else {
    doc.setIn(['arid-dual-model', 'vision', 'provider'], provider);
    doc.setIn(['arid-dual-model', 'vision', 'model'], model);
  }
  return { changed: true };
}

/**
 * 读取 executor 配置
 */
export function readExecutor(root) {
  const e = root?.['arid-dual-model']?.['executor'];
  if (!e || typeof e !== 'object') return null;
  const out = {};
  if (typeof e.provider === 'string') out.provider = e.provider;
  if (typeof e.model === 'string') out.model = e.model;
  if (typeof e.reasoningEffort === 'string') out.reasoningEffort = e.reasoningEffort;
  return Object.keys(out).length ? out : null;
}

/**
 * 读取 planner 配置
 */
export function readPlanner(root) {
  const d = root?.['agent-default-model'];
  if (!d || typeof d !== 'object') return null;
  const out = {};
  if (typeof d.provider === 'string') out.provider = d.provider;
  if (typeof d.model === 'string') out.model = d.model;
  if (typeof d.reasoningEffort === 'string') out.reasoningEffort = d.reasoningEffort;
  return Object.keys(out).length ? out : null;
}

/**
 * 读取 vision 配置
 */
export function readVision(root) {
  const v = root?.['arid-dual-model']?.['vision'];
  if (!v || typeof v !== 'object') return null;
  const out = {};
  if (typeof v.provider === 'string') out.provider = v.provider;
  if (typeof v.model === 'string') out.model = v.model;
  return Object.keys(out).length ? out : null;
}

/**
 * 安全地修改和保存 settings.yaml
 * operation: 接收 doc 文档对象，返回 { changed: boolean }
 */
export async function updateSettingsWithLock(settingsPath, operation) {
  return withFileLock(settingsPath, async () => {
    let text = '';
    try {
      text = await readSettingsText(settingsPath);
    } catch {
      text = '';
    }
    let doc;
    if (text.trim()) {
      doc = parseDocOrThrow(text);
    } else {
      doc = parseDocument('llm-pi-ai:\n  providers: {}\n', { prettyErrors: true });
    }
    const result = await operation(doc);
    if (result.changed) {
      await writeFileAtomic(settingsPath, doc.toString());
    }
    return result;
  });
}
