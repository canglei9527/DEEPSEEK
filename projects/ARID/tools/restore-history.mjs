#!/usr/bin/env node
/**
 * ARID 一键恢复历史聊天记录
 *
 * 把 aider / AiderGui 时代（8/12 - 8/14）的聊天记录：
 *   1) 归档到 E:\聊天记录归档\（原始文件副本 + 合并可读版）
 *   2) 转成 dsh 会话（session.jsonl.zstd），写入 DSH 存储（默认同时写
 *      C:\Users\<user>\.dsh 与 E:\.dsh，保证当前实例与重启后都可见）
 *   3) 注册进 workspace.json 的会话列表
 *
 * 用法: node tools/restore-history.mjs
 * 环境变量: RESTORE_HOMES="C:\...;E:\..." 可覆盖默认双 home
 */
import z from 'node:zlib';
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import os from 'node:os';

const WORKSPACE_PATH = 'E:\\DEEPSEEK ai\\ARID';
const SLUG = '--E-DEEPSEEK~0020ai-ARID--';
const ARCHIVE_DIR = 'E:\\聊天记录归档';

const HOMES = (process.env.RESTORE_HOMES || `C:\\Users\\${os.userInfo().username}\\.dsh;E:\\.dsh`)
  .split(';').map((s) => s.trim()).filter(Boolean);

/* ------------------------------------------------------------------ */
/* 解析 aider 聊天历史                                                  */
/* ------------------------------------------------------------------ */
function parseAider(text) {
  const sessions = [];
  let cur = null;
  let userBuf = null;   // consecutive "#### " lines -> one user message
  let asstBuf = [];
  const flushTurn = () => {
    if (!cur) return;
    if (userBuf !== null) {
      const user = userBuf.join('\n').replace(/\s+$/, '');
      const assistant = asstBuf.join('\n').replace(/\s+$/, '');
      cur.turns.push({ user, assistant });
    }
    userBuf = null;
    asstBuf = [];
  };
  const flushSession = () => {
    if (cur && cur.turns.length) sessions.push(cur);
    cur = null;
    userBuf = null;
    asstBuf = [];
  };
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.replace(/\s+$/, '');
    if (!line.trim()) continue;
    if (line.startsWith('# aider chat started at ')) {
      flushSession();
      cur = { startedAt: new Date(line.slice('# aider chat started at '.length).trim() + '+08:00'), turns: [] };
      continue;
    }
    if (!cur) continue;
    if (line.startsWith('#### ')) {
      flushTurn();
      userBuf = [line.slice(5).trim()];
      continue;
    }
    if (line.startsWith('###### ') || line.startsWith('> ') || line.startsWith('>')) continue;
    if (userBuf !== null) asstBuf.push(line);
  }
  flushTurn();
  flushSession();
  return sessions;
}

/* ------------------------------------------------------------------ */
/* AiderGui 会话汇总                                                    */
/* ------------------------------------------------------------------ */
function buildAiderGuiSummary(jsonPath) {
  const j = JSON.parse(fs.readFileSync(jsonPath, 'utf8'));
  const list = [...j].sort((a, b) => a.started_at - b.started_at);
  const turns = [];
  for (const s of list) {
    const when = new Date(s.started_at * 1000).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false });
    const task = String(s.task_text || '').replace(/\s+$/, '').trim();
    const user = `【任务 ${s.id.replace('session-', '')} · ${when} · ${s.lifecycle_stage}】` + (task ? `\n${task}` : '');
    const outputs = (s.output_records || [])
      .filter((r) => r.kind === 'model_output' && r.text && String(r.text).trim())
      .map((r) => String(r.text).trim())
      .slice(0, 6);
    const assistant = outputs.length
      ? outputs.map((t) => t.slice(0, 4000)).join('\n\n---\n\n')
      : '（此轮无有效模型输出）';
    turns.push({ user, assistant });
  }
  return { startedAt: new Date(list[0].started_at * 1000), turns };
}

/* ------------------------------------------------------------------ */
/* 生成 dsh 会话文件                                                    */
/* ------------------------------------------------------------------ */
function buildSessionFile({ id, createdAt, title, turns }) {
  const lines = [];
  const push = (obj) => lines.push(JSON.stringify(obj));
  let seq = 0;
  const t = () => createdAt + seq * 250;
  push({ type: 'session', version: 0, id, createdAt, cwd: WORKSPACE_PATH, delegationDepth: 0, agentPreset: 'cordis' });
  push({ type: 'permission/preset', seq: seq++, time: t(), data: { preset: 'workspace-write' } });
  push({ type: 'sandbox/mode', seq: seq++, time: t(), data: { mode: 'workspace-write' } });
  push({ type: 'approval/policy', seq: seq++, time: t(), data: { policy: 'ask' } });
  push({ type: 'session/title', seq: seq++, time: t(), data: { title, messageSeqs: [], source: { kind: 'imported' } } });
  let turn = 1;
  for (const tr of turns) {
    if (!tr.user || !tr.user.trim()) continue;
    push({ type: 'turn/start', seq: seq++, time: t(), data: { turn } });
    push({ type: 'step/start', seq: seq++, time: t(), data: { turn, step: 1 } });
    const userSeq = seq;
    push({
      type: 'user/message', seq: userSeq, time: t(),
      data: {
        content: [{ type: 'text', text: tr.user }],
        source: { kind: 'user', rpcId: crypto.randomUUID(), clientTimeZone: 'Asia/Shanghai' },
        role: 'user',
        id: crypto.randomUUID(),
      },
      surfaceOp: 'append',
    });
    seq++;
    if (tr.assistant && tr.assistant.trim()) {
      push({
        type: 'assistant/message', seq: seq++, time: t(),
        data: {
          turn, step: 1,
          message: {
            role: 'assistant',
            content: [{ type: 'text', text: tr.assistant }],
            source: { kind: 'model', provider: 'opencode-go', model: 'deepseek-v4-flash' },
            id: crypto.randomUUID(),
          },
        },
        sourceEventSeqs: [userSeq],
        surfaceOp: 'append',
      });
    }
    push({ type: 'step/end', seq: seq++, time: t(), data: { turn, step: 1 } });
    push({ type: 'turn/end', seq: seq++, time: t(), data: { turn, reason: { kind: 'completed' } } });
    turn++;
  }
  // 每行一个完整 zstd 帧（与 dsh 持久化格式一致）
  return Buffer.concat(lines.map((l) => z.zstdCompressSync(Buffer.from(l + '\n', 'utf8'), {
    params: { [z.constants.ZSTD_c_checksumFlag]: 1 },
  })));
}

/* ------------------------------------------------------------------ */
/* 主流程                                                              */
/* ------------------------------------------------------------------ */
const aiderguiJson = path.join(os.homedir(), '.aider_ui_sessions.json');
const sources = [
  { name: 'ARID-8月12日-飞船任务', file: path.join(WORKSPACE_PATH, '.aider.chat.history.md'), label: 'E:\\DEEPSEEK ai\\ARID\\.aider.chat.history.md' },
  { name: 'HOME-8月13日-杂项', file: path.join(os.homedir(), '.aider.chat.history.md'), label: path.join(os.homedir(), '.aider.chat.history.md') },
  { name: 'GLM5.2-8月14日-飞船任务重试', file: 'E:\\DEEPSEEK ai\\GLM5.2\\.aider.chat.history.md', label: 'E:\\DEEPSEEK ai\\GLM5.2\\.aider.chat.history.md' },
];

const imported = []; // {id,title,createdAt,turns}

// 1) aider 历史 -> 会话
for (const src of sources) {
  const parsed = parseAider(fs.readFileSync(src.file, 'utf8'));
  parsed.forEach((s, i) => {
    const label = src.name + (parsed.length > 1 ? `·第${i + 1}轮` : '');
    imported.push({
      id: 'session-' + crypto.randomUUID(),
      title: `${s.startedAt.getFullYear()}/${s.startedAt.getMonth() + 1}/${s.startedAt.getDate()} ${label}`,
      createdAt: s.startedAt.getTime(),
      turns: s.turns,
    });
  });
}

// 2) AiderGui 汇总
if (fs.existsSync(aiderguiJson)) {
  const g = buildAiderGuiSummary(aiderguiJson);
  imported.push({
    id: 'session-' + crypto.randomUUID(),
    title: '8/13-8/14 AiderGui 飞船任务重试(11次)',
    createdAt: g.startedAt.getTime(),
    turns: g.turns,
  });
}

// 3) 写归档
fs.mkdirSync(ARCHIVE_DIR, { recursive: true });
const parts = [];
const safeName = (s) => s.replace(/[\\/:*?"<>|]/g, '-');
for (const src of [...sources, { name: 'AiderGui会话', file: aiderguiJson, label: path.join(os.homedir(), '.aider_ui_sessions.json') }]) {
  if (fs.existsSync(src.file)) {
    const dst = path.join(ARCHIVE_DIR, `${safeName(src.name)}-${path.basename(src.file)}`);
    fs.copyFileSync(src.file, dst);
    parts.push(`- ${src.name}: 已复制到 ${dst}（源: ${src.label}）`);
  }
}
let md = `# ARID 全部聊天记录（8/12 - 8/14 合并版）\n\n生成时间: ${new Date().toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false })}\n\n`;
for (const imp of imported) {
  md += `\n## ${imp.title}\n`;
  for (const tr of imp.turns) {
    if (tr.user) md += `\n### 用户\n${tr.user}\n`;
    if (tr.assistant) md += `\n### 助手\n${tr.assistant}\n`;
  }
}
fs.writeFileSync(path.join(ARCHIVE_DIR, '全部聊天记录-合并.md'), md, 'utf8');
fs.writeFileSync(path.join(ARCHIVE_DIR, 'README.txt'),
  'ARID 历史聊天记录归档\n' + '='.repeat(30) + '\n\n' +
  '来源文件:\n' + parts.join('\n') + '\n\n' +
  '说明:\n' +
  '- 全部聊天记录-合并.md 为可读合并版（8/12-8/14）。\n' +
  '- 8/15 起的 dsh web 会话在 E:\\.dsh\\sessions（已同步）。\n' +
  '- 恢复脚本: E:\\DEEPSEEK ai\\ARID\\tools\\restore-history.mjs（可重跑）。\n',
  'utf8');

// 4) 写 dsh 存储（双 home），按标题幂等：已存在的会话跳过创建、复用 id
const MAGIC = 0xFD2FB528;
function existingTitles(home) {
  const root = path.join(home, 'sessions', SLUG);
  const out = new Map();
  if (!fs.existsSync(root)) return out;
  for (const dir of fs.readdirSync(root)) {
    if (!dir.startsWith('session-')) continue;
    const p = path.join(root, dir, 'session.jsonl.zstd');
    if (!fs.existsSync(p)) continue;
    const buf = fs.readFileSync(p);
    let off = 0;
    while (off + 4 <= buf.length) {
      if (buf.readUInt32LE(off) !== MAGIC) { off++; continue; }
      let dec;
      try { dec = z.zstdDecompressSync(buf.subarray(off)).toString('utf8'); } catch { off++; continue; }
      const m = dec.match(/"type":"session\/title"[^}]*"title":"([^"]+)"/);
      if (m) { out.set(m[1], dir); break; }
      off += 4;
    }
  }
  return out;
}
const primary = HOMES[0];
const known = existingTitles(primary);
for (const home of HOMES) {
  const sessRoot = path.join(home, 'sessions', SLUG);
  fs.mkdirSync(sessRoot, { recursive: true });
  for (const imp of imported) {
    if (known.has(imp.title)) { imp.id = known.get(imp.title); continue; }
    const dir = path.join(sessRoot, imp.id);
    fs.mkdirSync(dir, { recursive: true });
    fs.writeFileSync(path.join(dir, 'session.jsonl.zstd'), buildSessionFile(imp));
  }
  // 注册进 workspace.json
  const wsPath = path.join(home, 'storages', 'workspace.json');
  if (fs.existsSync(wsPath)) {
    const ws = JSON.parse(fs.readFileSync(wsPath, 'utf8'));
    for (const rec of Object.values(ws.tables?.workspaces || {})) {
      if (rec.path === WORKSPACE_PATH) {
        for (const imp of imported) {
          if (!rec.sessionIds.includes(imp.id)) rec.sessionIds.push(imp.id);
        }
        rec.updatedAt = new Date().toISOString();
      }
    }
    fs.writeFileSync(wsPath, JSON.stringify(ws, null, 2), 'utf8');
  }
}

console.log(`导入完成: ${imported.length} 个历史会话，写入 ${HOMES.length} 个存储`);
for (const imp of imported) {
  console.log(`  - ${imp.title} (${imp.turns.length} 轮) ${imp.id}`);
}
console.log(`归档目录: ${ARCHIVE_DIR}`);
