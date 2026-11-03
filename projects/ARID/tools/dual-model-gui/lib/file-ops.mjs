// lib/file-ops.mjs
// 文件操作工具：原子写入、文件锁定、备份管理

import {
  readFile, writeFile, rename, rm, mkdir, readdir, stat, copyFile,
} from 'node:fs/promises';
import { randomBytes } from 'node:crypto';
import { resolve, sep } from 'node:path';

/**
 * 原子写入：先写临时文件，再 rename 覆盖目标。
 * 镜像 @deepseek-ai/dsh-atomic-write 的协议，与 DSH runtime 并发安全。
 */
export async function writeFileAtomic(filename, content) {
  const temp = `${filename}.${randomBytes(6).toString('hex')}.tmp`;
  try {
    await writeFile(temp, content, { mode: 0o600, flag: 'wx' });
    await rename(temp, filename);
  } catch (err) {
    await rm(temp, { force: true });
    throw err;
  }
}

/**
 * 文件锁定：与 @deepseek-ai/dsh-atomic-write 的 withFileLock 协议相同。
 * 指数退避重试，2s 超时后失败。
 */
export async function withFileLock(filename, operation) {
  const lockPath = `${filename}.lock`;
  const deadline = Date.now() + 2000;
  let delay = 20;
  for (;;) {
    try {
      await writeFile(lockPath, `${process.pid}\n`, { mode: 0o600, flag: 'wx' });
      break;
    } catch (err) {
      if (err?.code !== 'EEXIST') throw err;
    }
    if (Date.now() >= deadline) {
      throw new Error(`等待写入锁超时（2s）：${lockPath}`);
    }
    await new Promise((r) => setTimeout(r, delay));
    delay = Math.min(delay * 2, 200);
  }
  try {
    return await operation();
  } finally {
    await rm(lockPath, { force: true });
  }
}

/** 检查文件/目录是否存在 */
export async function pathExists(p) {
  try { await stat(p); return true; } catch { return false; }
}

/**
 * 路径安全检查：确保 candidate 解析后位于 dir 内部。
 * 防止路径穿梭漏洞。
 */
export function inDir(dir, candidate) {
  const resolved = resolve(candidate);
  const root = resolve(dir);
  if (resolved !== root && !resolved.startsWith(root + sep)) return null;
  return resolved;
}

/** 生成时间戳：yyyyMMdd-HHmmss */
export function nowStamp(d = new Date()) {
  const p = (n, l = 2) => String(n).padStart(l, '0');
  return `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}`;
}

/** 读取文本文件 */
export async function readTextFile(path) {
  return readFile(path, 'utf8');
}

/** 列出目录文件 */
export async function listDir(path) {
  return readdir(path);
}

/** 获取文件信息 */
export async function getFileStat(path) {
  return stat(path);
}

/** 复制文件 */
export async function copyFileAtomic(src, dst) {
  try {
    await copyFile(src, dst);
  } catch {
    // Some packaged files may be read-only; fall back to read+write.
    await writeFile(dst, await readFile(src));
  }
}

/** 删除文件 */
export async function removeFile(path) {
  return rm(path, { force: true });
}

/** 创建目录（递归） */
export async function ensureDir(path) {
  return mkdir(path, { recursive: true });
}
