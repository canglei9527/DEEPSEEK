// lib/harness-manager.mjs
// Harness 生命周期管理：DSH 安装、预设导入、状态检查

import { spawn, spawnSync } from 'node:child_process';
import { readdir, copyFile, readFile } from 'node:fs/promises';
import { join, dirname } from 'node:path';
import {
  pathExists, ensureDir, copyFileAtomic,
} from './file-ops.mjs';
import {
  parseDocument, parseDocOrThrow, updateSettingsWithLock,
} from './settings-manager.mjs';

// 所需的预设文件列表
export const REQUIRED_PRESET_FILES = [
  'preset.yml',
  'agent.cordis.yml',
  'arid-dualmodel-ext6.cjs',
  'arid-tavily.cjs',
  'kbtool.cjs',
];

// 可选的预设文件（作为后备方案）
export const OPTIONAL_PRESET_FILES = [
  'arid-dualmodel-vision.cjs',  // Node < 22 时的视觉后备方案
];

/**
 * 检查命令是否可用
 */
export function commandExists(cmd) {
  try {
    const probe = process.platform === 'win32' ? 'where' : 'which';
    const r = spawnSync(probe, [cmd], { stdio: 'ignore', windowsHide: true });
    return r.status === 0;
  } catch { return false; }
}

/**
 * 检查 Node 版本是否 ≥22
 */
export function checkNodeVersion() {
  const version = process.version; // 如 "v22.0.0"
  const major = parseInt(version.slice(1).split('.')[0]);
  return {
    version,
    major,
    supportsVisionRouter: major >= 22,
  };
}

/**
 * 检查 dsh-vision-router 是否已安装
 */
export async function checkVisionRouterInstalled() {
  // 方法1: 检查 dsh plugin list 输出
  try {
    const r = await runProcess('dsh', ['plugin', 'list', '--profile', 'web']);
    if (r.ok && r.output.includes('dsh-vision-router')) {
      return { installed: true, method: 'plugin' };
    }
  } catch {
    // dsh 命令不存在或执行失败
  }
  
  // 方法2: 检查 node_modules（如果在当前项目安装）
  const nodeModulesPath = join(process.cwd(), 'node_modules', 'dsh-vision-router');
  if (await pathExists(nodeModulesPath)) {
    return { installed: true, method: 'local' };
  }
  
  return { installed: false };
}

/**
 * 运行外部进程
 * 返回 { ok, code, output, stderr }
 */
export function runProcess(cmd, args, opts = {}) {
  return new Promise((resolve) => {
    let child;
    try {
      child = spawn(cmd, args, { windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'], ...opts });
    } catch (err) {
      resolve({ ok: false, code: -1, output: '', stderr: err.message });
      return;
    }
    let out = '', errText = '';
    child.stdout?.on('data', (d) => { out += d.toString(); });
    child.stderr?.on('data', (d) => { errText += d.toString(); });
    child.on('error', (err) => resolve({ ok: false, code: -1, output: out, stderr: err.message }));
    child.on('close', (code) => resolve({ ok: code === 0, code, output: out, stderr: errText }));
  });
}

/**
 * 导入双模式预设
 * 复制预设文件并可选地将其设置为默认值
 */
export async function importDualModelPreset(
  settingsPath,
  aridDir,
  kbRoot,
  activate = false,
) {
  const src = join(aridDir, 'preset');
  const dst = join(dirname(settingsPath), '.agent-presets', 'arid-dualmodel');

  if (!(await pathExists(src))) {
    const e = new Error(`找不到 preset 目录：${src}`);
    e.statusCode = 404;
    throw e;
  }

  await ensureDir(dst);
  const files = await readdir(src);
  for (const name of files) {
    const from = join(src, name);
    const to = join(dst, name);
    await copyFileAtomic(from, to);
  }

  // 更新 settings.yaml：可选激活，设置知识库根目录
  await updateSettingsWithLock(settingsPath, async (doc) => {
    const current = doc.toJS()?.['agent-presets']?.default;
    const currentRoot = doc.toJS()?.['arid-dual-model']?.knowledgeBase?.root;
    let changed = false;

    if (activate && current !== 'arid-dualmodel') {
      doc.setIn(['agent-presets', 'default'], 'arid-dualmodel');
      changed = true;
    }

    if (currentRoot !== kbRoot) {
      doc.setIn(['arid-dual-model', 'knowledgeBase', 'root'], kbRoot);
      changed = true;
    }

    return { changed };
  });

  // 验证所有必需文件都已复制
  const presetFiles = {};
  for (const file of REQUIRED_PRESET_FILES) {
    presetFiles[file] = await pathExists(join(dst, file));
  }

  if (!REQUIRED_PRESET_FILES.every(file => presetFiles[file])) {
    const missing = REQUIRED_PRESET_FILES.filter(file => !presetFiles[file]);
    const e = new Error(`双模式预设不完整，缺少：${missing.join('、')}`);
    e.statusCode = 500;
    throw e;
  }

  return { ok: true, presetDir: dst, files: files.length, presetFiles };
}

/**
 * 检查知识库目录状态
 */
export async function checkKbStatus(root, configuredKbRoot, defaultKbRoot) {
  const path = configuredKbRoot || defaultKbRoot;
  try {
    const info = await import('node:fs/promises').then(fs => fs.stat(path));
    return { root: path, ready: info.isDirectory() };
  } catch {
    return { root: path, ready: false };
  }
}

/**
 * 检查预设和插件安装状态
 */
export async function checkPresetStatus(
  settingsPath,
  requiredFiles = REQUIRED_PRESET_FILES,
) {
  const presetDirBase = join(dirname(settingsPath), '.agent-presets', 'arid-dualmodel');

  let defaultVal = null;
  let root = {};
  try {
    const text = await readFile(settingsPath, 'utf8');
    const doc = parseDocOrThrow(text);
    root = doc.toJS() ?? {};
    const v = root?.['agent-presets']?.['default'];
    defaultVal = typeof v === 'string' ? v : null;
  } catch {
    // settings.yaml 不存在或解析失败
  }

  const presetFiles = {};
  for (const file of requiredFiles) {
    presetFiles[file] = await pathExists(join(presetDirBase, file));
  }

  // 检查视觉方案状态
  const nodeCheck = checkNodeVersion();
  const visionRouterCheck = await checkVisionRouterInstalled();
  const legacyVisionExists = await pathExists(join(presetDirBase, 'arid-dualmodel-vision.cjs'));

  const kb = await checkKbStatus(root, root?.['arid-dual-model']?.knowledgeBase?.root, '');

  return {
    presetDefault: defaultVal,
    presetInstalled: requiredFiles.every(file => presetFiles[file]),
    executorPluginInstalled: presetFiles['arid-dualmodel-ext6.cjs'],
    
    // 视觉插件状态（新增详细信息）
    visionPluginInstalled: visionRouterCheck.installed || legacyVisionExists,
    visionRouterInstalled: visionRouterCheck.installed,
    visionRouterSupported: nodeCheck.supportsVisionRouter,
    visionMethod: visionRouterCheck.installed ? 'dsh-vision-router' : (legacyVisionExists ? 'arid-dualmodel-vision' : 'none'),
    nodeVersion: nodeCheck.version,
    
    kbPluginInstalled: presetFiles['kbtool.cjs'],
    presetFiles,
    kb,
  };
}

/**
 * Harness 安装管理类
 */
export class HarnessInstallManager {
  constructor() {
    this.state = { installing: false, message: '', lastAt: 0 };
  }

  isInstalling() {
    return this.state.installing;
  }

  setInstalling(message) {
    this.state = { installing: true, message, lastAt: Date.now() };
  }

  setCompleted(message) {
    this.state = { installing: false, message, lastAt: Date.now() };
  }

  getState() {
    return this.state;
  }

  /**
   * 执行安装流程
   */
  async install(settingsPath, aridDir, kbRoot, dshInstalled) {
    try {
      this.setInstalling('正在启动安装…');

      if (!commandExists('npm')) {
        this.setCompleted('未找到 npm，请先安装 Node.js。');
        return;
      }

      if (!(await dshInstalled())) {
        this.setInstalling('正在通过 npm 安装 DeepSeek Harness（dsh）…');
        const r = await runProcess('npm', ['install', '-g', '@deepseek-ai/dsh']);
        if (!r.ok) {
          this.setCompleted(`安装 dsh 失败：${r.stderr || r.output || r.code}`);
          return;
        }
      }

      // 检查 Node 版本并安装对应的视觉方案
      const nodeCheck = checkNodeVersion();
      
      if (nodeCheck.supportsVisionRouter) {
        this.setInstalling('正在安装 dsh-vision-router（11 个视觉工具）…');
        
        const visionCheck = await checkVisionRouterInstalled();
        if (!visionCheck.installed) {
          const r = await runProcess('dsh', [
            'plugin', '--profile', 'web', 'add', 'dsh-vision-router'
          ]);
          
          if (!r.ok) {
            this.setInstalling(`安装 dsh-vision-router 失败（${r.stderr || r.output}），将使用旧版视觉方案。`);
            // 不视为致命错误，继续安装流程
          } else {
            this.setInstalling('dsh-vision-router 安装成功！');
          }
        } else {
          this.setInstalling('dsh-vision-router 已安装。');
        }
      } else {
        this.setInstalling(`Node ${nodeCheck.version} < 22，将使用旧版视觉方案（arid-dualmodel-vision.cjs）。建议升级 Node 以获得 11 个高级视觉工具。`);
      }

      await importDualModelPreset(settingsPath, aridDir, kbRoot, true);
      this.setCompleted('安装完成：dsh 已就绪，双模式预设已导入。');
    } catch (err) {
      this.setCompleted(`安装失败：${err?.message || String(err)}`);
    }
  }
}
