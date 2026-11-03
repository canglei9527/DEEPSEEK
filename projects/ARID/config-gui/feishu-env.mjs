#!/usr/bin/env node
/*
 * ARID Feishu .env generator
 *
 * 从配置 GUI 的 settings.yaml（arid-feishu 段）+ ~/.dsh/.credentials.yaml 生成
 * feishu-worker\.env（供飞书长连接 worker 读取）。零外部依赖 + vendored yaml，
 * 与 server.mjs 同一目录、同一风格。
 *
 * 用法（从 feishu-worker 的 start-feishu.bat 调用）：
 *   node config-gui\feishu-env.mjs          # 生成/覆盖 feishu-worker\.env
 *   node config-gui\feishu-env.mjs --print  # 只打印预览到 stdout（key 值被掩码）
 *
 * 环境变量覆盖：
 *   ARID_CONFIG_HOME    覆盖配置目录（默认用户主目录，对齐 server.mjs）
 *   ARID_FEISHU_ENV_OUT 覆盖 .env 输出路径（默认 <仓库根>\feishu-worker\.env）
 *
 * 密钥纪律：默认写文件模式绝不打印 key 值；--print 模式把 key 行掩码为 sk-***；
 * 日志只打印 .env 输出路径，绝不打印任何真实密钥。
 */
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
  YAML = require('yaml');
}
const { parse: yamlParse, stringify: yamlStringify } = YAML;

/* ------------------------------------------------------------------ */
/* 路径                                                                  */
/* ------------------------------------------------------------------ */
const HERE = path.dirname(fileURLToPath(import.meta.url));
const PACKAGE_ROOT = path.resolve(HERE, '..');
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
const OUT_ENV_PATH = process.env.ARID_FEISHU_ENV_OUT && process.env.ARID_FEISHU_ENV_OUT.trim()
  ? path.resolve(process.env.ARID_FEISHU_ENV_OUT)
  : path.join(PACKAGE_ROOT, 'feishu-worker', '.env');

/* ------------------------------------------------------------------ */
/* 工具                                                                  */
/* ------------------------------------------------------------------ */
function readYamlOr(filePath, fallback) {
  try {
    if (!fs.existsSync(filePath)) return fallback;
    const raw = fs.readFileSync(filePath, 'utf8');
    const trimmed = raw.trim();
    if (!trimmed) return fallback;
    const val = yamlParse(trimmed);
    return val === null || val === undefined ? fallback : val;
  } catch {
    return fallback;
  }
}

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

/** 值中含特殊字符（空白、引号、#、=、分号分隔的路径里含 ; 可直接写）时用双引号包裹。 */
function quoteIfNeeded(value) {
  const s = String(value);
  if (s && /[\s="#]/.test(s)) {
    return '"' + s.replace(/"/g, '\\"') + '"';
  }
  return s;
}

function maskKey(key) {
  if (!key) return '';
  if (key.length <= 8) return '*'.repeat(key.length);
  return `${key.slice(0, 3)}***${key.slice(-3)}`;
}

/** 空字符串或 null/undefined 视为"未填写"，不写入 .env。 */
function present(v) {
  return v !== undefined && v !== null && String(v).trim() !== '';
}

/* ------------------------------------------------------------------ */
/* 生成 .env 条目（从 settings + credentials）                              */
/* ------------------------------------------------------------------ */
function buildEnvLines(doc, creds) {
  const feishu = doc && typeof doc === 'object' && doc['arid-feishu'] && typeof doc['arid-feishu'] === 'object'
    ? doc['arid-feishu'] : {};
  const allowedDirs = Array.isArray(feishu.allowedDirs) ? feishu.allowedDirs : [];
  const planner = feishu.planner && typeof feishu.planner === 'object' ? feishu.planner : {};
  const executor = feishu.executor && typeof feishu.executor === 'object' ? feishu.executor : {};

  const lines = [];
  const add = (key, val) => {
    if (!present(val)) return;
    lines.push(`${key}=${quoteIfNeeded(val)}`);
  };

  // 飞书凭证：App ID/Secret 来自 settings / credentials
  add('FEISHU_APP_ID', feishu.appId);
  add('FEISHU_APP_SECRET', creds.FEISHU_APP_SECRET);
  add('FEISHU_ENCRYPT_KEY', creds.FEISHU_ENCRYPT_KEY);

  // 白名单（数组 -> Windows 分号分隔）
  if (allowedDirs.length) {
    add('ARID_FEISHU_ALLOWED_DIRS', allowedDirs.join(';'));
  }

  // 可选参数
  add('ARID_FEISHU_BOT_OPEN_ID', feishu.botOpenId);
  add('ARID_FEISHU_TIMEOUT', feishu.timeoutSeconds);
  add('ARID_FEISHU_REPLY_MAX_CHARS', feishu.replyMaxChars);
  add('ARID_FEISHU_OUTPUT_MAX_LINES', feishu.outputMaxLines);

  // 高等级模型
  add('ARID_PLANNER_PROVIDER', planner.provider);
  add('ARID_PLANNER_MODEL', planner.model);
  add('ARID_PLANNER_BASE_URL', planner.baseUrl);
  add('ARID_PLANNER_API_KEY', creds.ARID_PLANNER_API_KEY);

  // 低等级模型（可选）
  add('ARID_EXECUTOR_PROVIDER', executor.provider);
  add('ARID_EXECUTOR_MODEL', executor.model);
  add('ARID_EXECUTOR_BASE_URL', executor.baseUrl);
  add('ARID_EXECUTOR_API_KEY', creds.ARID_EXECUTOR_API_KEY);

  // Tavily（可选）
  add('TAVILY_API_KEY', creds.TAVILY_API_KEY);

  return lines;
}

/* ------------------------------------------------------------------ */
/* 主流程                                                                  */
/* ------------------------------------------------------------------ */
function main() {
  const doc = readYamlOr(SETTINGS_PATH, {});
  const creds = readCredentials();
  const lines = buildEnvLines(doc, creds);

  const isPrint = process.argv.includes('--print');
  if (isPrint) {
    // 预览模式：key 行掩码，其它行原样。绝不打印真实密钥。
    for (const line of lines) {
      const m = /^([A-Z_]+)=(.*)$/.exec(line);
      if (m) {
        const k = m[1];
        if (k === 'FEISHU_APP_SECRET' || k === 'FEISHU_ENCRYPT_KEY' ||
            k === 'ARID_PLANNER_API_KEY' || k === 'ARID_EXECUTOR_API_KEY' ||
            k === 'TAVILY_API_KEY') {
          const raw = creds[k];
          process.stdout.write(`${k}=${maskKey(raw)}\n`);
          continue;
        }
      }
      process.stdout.write(line + '\n');
    }
    process.stdout.write('# OUTPUT would be: ' + OUT_ENV_PATH + '\n');
    return;
  }

  // 写文件模式：原子写
  fs.mkdirSync(path.dirname(OUT_ENV_PATH), { recursive: true });
  const content = lines.join('\n') + (lines.length ? '\n' : '');
  const tmp = `${OUT_ENV_PATH}.tmp-${process.pid}-${Date.now()}`;
  fs.writeFileSync(tmp, content, 'utf8');
  try {
    fs.renameSync(tmp, OUT_ENV_PATH);
  } catch {
    try { fs.unlinkSync(OUT_ENV_PATH); } catch { /* 文件不存在也允许 */ }
    fs.renameSync(tmp, OUT_ENV_PATH);
  }
  // 只打印路径，不打印键值
  process.stderr.write(`[arid-feishu-env] wrote ${OUT_ENV_PATH}\n`);
  process.stdout.write(`[arid-feishu-env] .env generated: ${OUT_ENV_PATH}\n`);
}

try {
  main();
} catch (error) {
  process.stderr.write(`[arid-feishu-env] ERROR: ${String(error && error.message ? error.message : error)}\n`);
  process.exit(1);
}
