#!/usr/bin/env node
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { resolve } from 'node:path';
import { createDaiRestAdapter, normalizeDaiSyncConfig } from '../src/dai-rest-adapter.js';
import { validateSyncInterval } from '../src/dai-sync-core.js';
import {
  formatDaiSyncEvent,
  formatDaiSyncSummary,
  runDaiLifecycleCycle,
  runDaiSyncCycle
} from '../src/dai-sync-runner.js';

const packagedRuntime = Boolean(process.pkg || process.isSea || process.argv[1]?.toLowerCase().endsWith('.exe'));

const HELP = `D-AI 同名模型分组倍率同步器

用法：
  node bin/dai-group-sync.mjs --config <文件> [--once | --watch] [--dry-run | --apply] [--interval-ms <毫秒>] [--adjustment <数值>]

安全默认值：
  - 默认 --once 和 --dry-run；不会调用任何更新 API。
  - 要写入远程系统，必须同时设置配置中的 allowApply: true 并传入 --apply。
  - --watch 按配置（默认 60000ms）持续轮询；每轮都重新获取上游和分组。

选项：
  --config <文件>       必填，JSON 配置文件（可从 dai-sync.config.example.json 复制）
  --once                只执行一轮（默认）
  --watch               持续轮询
  --dry-run             只显示差异，不更新（默认）
  --apply               显式允许更新；还需要配置 allowApply: true
  --interval-ms <毫秒>  覆盖配置轮询间隔，最小 1000ms
  --adjustment <数值>   覆盖配置调整值，范围 0 到 1
  --lifecycle           同步新增分组、上游关联和调度规则
  --disable-stale       在生命周期 apply 中停用失效分组（不停止规则）
  --enable-restored     在生命周期 apply 中恢复已识别规则/分组
  --help, -h            显示帮助
`;

function parseInteger(value, flag) {
  if (!/^\d+$/.test(String(value))) throw new Error(`${flag} 必须是整数`);
  return Number(value);
}

function parseAdjustment(value, flag) {
  const adjustment = Number(value);
  if (!Number.isFinite(adjustment) || adjustment < 0 || adjustment > 1) {
    throw new Error(`${flag} 必须是 0 到 1 之间的有限数字`);
  }
  return adjustment;
}

export function parseCliArguments(argv) {
  const options = { mode: 'once', apply: false, configPath: null, intervalMs: undefined, adjustment: undefined, lifecycle: false, disableStale: false, enableRestored: false, help: false };
  let explicitMode = null;
  let explicitWriteMode = null;
  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index];
    if (argument === '--help' || argument === '-h') {
      options.help = true;
    } else if (argument === '--config') {
      const value = argv[++index];
      if (!value) throw new Error('--config 需要文件路径');
      options.configPath = value;
    } else if (argument === '--once') {
      if (explicitMode === 'watch') throw new Error('--once 与 --watch 不能同时使用');
      options.mode = 'once';
      explicitMode = 'once';
    } else if (argument === '--watch') {
      if (explicitMode === 'once') throw new Error('--once 与 --watch 不能同时使用');
      options.mode = 'watch';
      explicitMode = 'watch';
    } else if (argument === '--apply') {
      if (explicitWriteMode === 'dry-run') throw new Error('--dry-run 与 --apply 不能同时使用');
      options.apply = true;
      explicitWriteMode = 'apply';
    } else if (argument === '--dry-run') {
      if (explicitWriteMode === 'apply') throw new Error('--dry-run 与 --apply 不能同时使用');
      options.apply = false;
      explicitWriteMode = 'dry-run';
    } else if (argument === '--interval-ms') {
      const value = argv[++index];
      if (!value) throw new Error('--interval-ms 需要毫秒值');
      options.intervalMs = validateSyncInterval(parseInteger(value, '--interval-ms'));
    } else if (argument === '--adjustment') {
      const value = argv[++index];
      if (value === undefined) throw new Error('--adjustment 需要数值');
      options.adjustment = parseAdjustment(value, '--adjustment');
    } else if (argument === '--lifecycle') {
      options.lifecycle = true;
    } else if (argument === '--disable-stale') {
      options.disableStale = true;
    } else if (argument === '--enable-restored') {
      options.enableRestored = true;
    } else {
      throw new Error(`未知选项：${argument}`);
    }
  }
  return options;
}

async function loadJsonConfig(configPath, readFileImpl) {
  let text;
  try {
    text = await readFileImpl(resolve(configPath), 'utf8');
  } catch (error) {
    throw new Error(`无法读取配置文件：${error instanceof Error ? error.message : String(error)}`);
  }
  try {
    return JSON.parse(text);
  } catch (error) {
    throw new Error(`配置不是有效 JSON：${error instanceof Error ? error.message : String(error)}`);
  }
}

function sleep(milliseconds) {
  return new Promise((resolvePromise) => setTimeout(resolvePromise, milliseconds));
}

/** Public entry point, exported so local mock verification can exercise the same CLI flow. */
export async function main(argv = process.argv.slice(2), {
  readFileImpl = readFile,
  fetchImpl = globalThis.fetch,
  environment = process.env,
  write = (line) => console.log(line),
  error = (line) => console.error(line),
  sleepImpl = sleep,
  config: injectedConfig
} = {}) {
  let options;
  try {
    options = parseCliArguments(argv);
  } catch (parseError) {
    error(`参数错误：${parseError.message}\n\n${HELP}`);
    return 2;
  }
  if (options.help) {
    write(HELP);
    return 0;
  }
  if (!options.configPath && !injectedConfig) {
    error(`参数错误：必须提供 --config <文件>\n\n${HELP}`);
    return 2;
  }

  let config;
  try {
    config = normalizeDaiSyncConfig(injectedConfig ?? await loadJsonConfig(options.configPath, readFileImpl));
  } catch (configError) {
    error(`配置错误：${configError.message}`);
    return 2;
  }
  if (options.apply && !config.allowApply) {
    error('安全拦截：--apply 还要求配置文件显式设置 "allowApply": true。未发送任何请求。');
    return 2;
  }
  if (options.apply && options.lifecycle && !config.allowLifecycleApply) {
    error('安全拦截：--lifecycle --apply 还要求配置文件显式设置 "allowLifecycleApply": true。未发送任何请求。');
    return 2;
  }
  if (options.disableStale && (!options.lifecycle || !options.apply)) {
    error('参数错误：--disable-stale 只能与 --lifecycle --apply 一起使用。未发送任何请求。');
    return 2;
  }
  if (options.disableStale && !config.allowDisableStale) {
    error('安全拦截：--disable-stale 还要求配置文件显式设置 "allowDisableStale": true。未发送任何请求。');
    return 2;
  }
  if (options.enableRestored && (!options.lifecycle || !options.apply)) {
    error('参数错误：--enable-restored 只能与 --lifecycle --apply 一起使用。未发送任何请求。');
    return 2;
  }
  if (options.enableRestored && !config.allowEnableRestored) {
    error('安全拦截：--enable-restored 还要求配置文件显式设置 "allowEnableRestored": true。未发送任何请求。');
    return 2;
  }
  if (options.intervalMs !== undefined) config.intervalMs = options.intervalMs;
  if (options.adjustment !== undefined) config.adjustment = options.adjustment;

  let adapter;
  try {
      adapter = createDaiRestAdapter(config, {
        fetchImpl,
        environment,
        browserRoot: process.cwd()
      });
  } catch (adapterError) {
    error(`适配器错误：${adapterError.message}`);
    return 2;
  }

  let cycle = 0;
  let exitCode = 0;
  try {
    do {
      cycle += 1;
      try {
        const result = options.lifecycle
          ? await runDaiLifecycleCycle(adapter, {
            apply: options.apply,
            disableStale: options.disableStale,
            enableRestored: options.enableRestored,
            adjustment: config.adjustment,
            decimals: config.decimals,
            tolerance: config.tolerance,
            onEvent: (event) => write(formatDaiSyncEvent(event))
          })
          : await runDaiSyncCycle(adapter, {
            apply: options.apply,
            adjustment: config.adjustment,
            decimals: config.decimals,
            tolerance: config.tolerance,
            onEvent: (event) => write(formatDaiSyncEvent(event))
          });
        write(`第 ${cycle} 轮 ${formatDaiSyncSummary(result)}`);
        if (result.failure) {
          exitCode = 1;
          if (options.mode === 'watch') {
            error(`第 ${cycle} 轮写入失败，已停止监视器，避免重复发送远程请求。`);
            break;
          }
        }
      } catch (cycleError) {
        error(`第 ${cycle} 轮失败：${cycleError instanceof Error ? cycleError.message : String(cycleError)}。本轮未调用更新。`);
        exitCode = 1;
        if (options.mode === 'watch') {
          error(`第 ${cycle} 轮读取或计划失败，已停止监视器，避免在异常状态下重复执行。`);
          break;
        }
      }
      if (options.mode === 'watch') await sleepImpl(config.intervalMs);
    } while (options.mode === 'watch');
  } finally {
    await adapter.close?.();
  }
  return exitCode;
}

const invokedAsCli = !packagedRuntime && process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url);
if (invokedAsCli) {
  main().then((code) => {
    process.exitCode = code;
  }).catch((error) => {
    console.error(`未处理错误：${error instanceof Error ? error.message : String(error)}`);
    process.exitCode = 1;
  });
}
