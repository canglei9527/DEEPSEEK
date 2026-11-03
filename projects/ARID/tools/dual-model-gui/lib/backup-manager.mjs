// lib/backup-manager.mjs
// 备份管理：创建、列表、恢复、清理

import { readdir, stat, readFile, writeFile, rm, mkdir } from 'node:fs/promises';
import { join } from 'node:path';
import { pathExists, nowStamp, withFileLock, writeFileAtomic } from './file-ops.mjs';

// 备份名称正则表达式
const BACKUP_NAME_RE = /^settings(-(initial|pregui|manual))?-\d{8}-\d{6}(-\d+)?\.yaml$/;

/**
 * 对备份文件名进行分类
 * 返回: 'initial' | 'pregui' | 'manual' | 'auto' | null
 */
export function classifyBackup(name) {
  if (!BACKUP_NAME_RE.test(name)) return null;
  if (name.startsWith('settings-initial-')) return 'initial';
  if (name.startsWith('settings-pregui-')) return 'pregui';
  if (name.startsWith('settings-manual-')) return 'manual';
  return 'auto';
}

/**
 * 生成备份文件名
 * kind: 'auto' | 'initial' | 'pregui' | 'manual'
 */
function backupName(kind) {
  const tag = kind === 'auto' ? '' : kind === 'initial' ? '-initial' : kind === 'pregui' ? '-pregui' : '-manual';
  return `settings${tag}-${nowStamp()}.yaml`;
}

/**
 * 列出备份文件
 * 返回: 按 mtime 降序排列的备份列表
 */
export async function listBackups(backupDir) {
  let files = [];
  try { files = await readdir(backupDir); } catch { return []; }
  const out = [];
  for (const name of files) {
    const kind = classifyBackup(name);
    if (!kind) continue;
    let size = 0, mtime = 0;
    try { const st = await stat(join(backupDir, name)); size = st.size; mtime = st.mtimeMs; } catch { continue; }
    out.push({ file: name, name, kind, size, mtime });
  }
  out.sort((a, b) => b.mtime - a.mtime);
  return out;
}

/**
 * 创建备份
 * 防止同秒重复创建时的文件覆盖
 */
export async function writeBackup(settingsPath, backupDir, kind) {
  await mkdir(backupDir, { recursive: true });
  // 同秒写入时防止覆盖：追加 -n 后缀
  let name = backupName(kind);
  let n = 1;
  while (await pathExists(join(backupDir, name))) {
    name = `${backupName(kind).replace(/\.yaml$/, '')}-${n}.yaml`;
    n += 1;
  }
  const dest = join(backupDir, name);
  const data = await readFile(settingsPath);
  await writeFile(dest, data);
  const st = await stat(dest);
  return { file: name, kind, size: st.size, mtime: st.mtimeMs };
}

/**
 * 清理自动备份
 * 保留最近 MAX_AUTO_KEEP 个自动和手动备份
 * initial 和 pregui 备份永不删除
 */
export async function pruneAuto(backupDir, maxAutoKeep = 30) {
  const pruneable = (await listBackups(backupDir))
    .filter((b) => b.kind !== 'initial' && b.kind !== 'pregui')
    .sort((a, b) => b.mtime - a.mtime);
  if (pruneable.length <= maxAutoKeep) return;
  for (const b of pruneable.slice(maxAutoKeep)) {
    try { await rm(join(backupDir, b.file), { force: true }); } catch { /* ignore */ }
  }
}

/**
 * 确保存在初始备份
 * 首次调用时自动创建（如果备份不存在且 settings.yaml 存在）
 */
export async function ensureInitialBackup(settingsPath, backupDir) {
  if ((await listBackups(backupDir)).length > 0) return null;
  if (!(await pathExists(settingsPath))) return null;
  return writeBackup(settingsPath, backupDir, 'initial');
}

/**
 * 确保存在 pregui 基线备份
 * 首次 GUI 修改时创建，之后永不重建
 * 必须在写入锁内调用
 */
export async function ensurePreguiBackup(settingsPath, backupDir) {
  if (!(await pathExists(settingsPath))) return null;
  const pregui = (await listBackups(backupDir)).find((b) => b.kind === 'pregui');
  if (pregui) return null;
  return writeBackup(settingsPath, backupDir, 'pregui');
}

/**
 * 生成设置快照签名
 * 用于 SSE 通道检测外部变化
 * 返回字符串，内容变化时字符串改变
 */
export async function settingsSnapshot(settingsPath, credentialsPath, backupDir) {
  const parts = [];
  try {
    const st = await stat(settingsPath);
    parts.push(`s:${st.size}:${Math.round(st.mtimeMs)}`);
  } catch {
    parts.push('s:absent');
  }
  try {
    const st = await stat(credentialsPath);
    parts.push(`c:${st.size}:${Math.round(st.mtimeMs)}`);
  } catch {
    parts.push('c:absent');
  }
  let names = [];
  try { names = (await readdir(backupDir)).sort(); } catch { names = []; }
  parts.push(`#${names.length}`);
  for (const name of names) {
    try {
      const st = await stat(join(backupDir, name));
      parts.push(`${name}=${st.size}:${Math.round(st.mtimeMs)}`);
    } catch {
      parts.push(`${name}=absent`);
    }
  }
  return parts.join('\n');
}

/**
 * 处理备份读取请求
 * 安全检查文件路径，防止路径穿梭攻击
 */
export async function resolveBackupFile(backupDir, name, inDirFn) {
  if (!name) return null;
  const { basename, join: joinPath } = await import('node:path');
  const base = basename(name);
  if (base.includes('..') || base.includes('\\') || base.includes('/')) return null;
  const resolved = inDirFn(backupDir, joinPath(backupDir, base));
  if (!resolved) return null;
  const kind = classifyBackup(base);
  if (!kind) return null;
  if (!(await pathExists(resolved))) return null;
  return { path: resolved, name: base, kind };
}

/**
 * 安全恢复：先备份当前文件，再替换为备份内容
 */
export async function restoreFromBackup(
  settingsPath,
  backupDir,
  backupFileName,
  resolveBackupFileFn,
  parseDocOrThrowFn,
) {
  return withFileLock(settingsPath, async () => {
    const b = await resolveBackupFileFn(backupDir, backupFileName);
    if (!b) { const e = new Error('备份文件未找到或路径非法。'); e.statusCode = 404; throw e; }
    if (!(await pathExists(settingsPath))) {
      const e = new Error('settings.yaml 不存在。'); e.statusCode = 404; throw e;
    }
    // 创建 pregui 基线：首次恢复时固化当前文件
    await ensurePreguiBackup(settingsPath, backupDir);
    // 自动备份当前文件
    const backup = await writeBackup(settingsPath, backupDir, 'auto');
    await pruneAuto(backupDir);
    const content = await readFile(b.path, 'utf8');
    // 验证文件格式
    parseDocOrThrowFn(content);
    // 替换为备份内容
    await writeFileAtomic(settingsPath, content);
    return { ok: true, restored: b.name, backup: backup.file };
  });
}
