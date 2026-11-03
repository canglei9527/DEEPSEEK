/**
 * 飞书 worker 入口 + transport。
 *
 * 复用与 aider-gui 的 run_feishu_bot.py 相同的长连接语义：
 * - 从环境变量读 App ID/Secret（绝不写日志）。
 * - 白名单 ARID_FEISHU_ALLOWED_DIRS（未配置则禁用远程执行）。
 * - 单飞：同一时刻只跑一个任务。
 * - 复用 DSH 的 headless/会话能力跑任务（通过注入的 TaskExecutor）。
 *
 * 本模块只做 transport 与入口装配，业务路由在 bot.ts（纯逻辑可测）。
 * 长连接对接飞书官方 SDK（lark）是可替换实现，默认提供占位 transport，
 * 等你拿到 App ID/Secret 后接上。
 */

import { FeishuRemoteBot, type FeishuMessage, type FeishuTransport } from "./bot.js";

/** 从环境变量读白名单目录（多个用平台路径分隔符分隔）。 */
export function allowedDirsFromEnv(env: Record<string, string | undefined> = process.env): string[] {
  const raw = env.ARID_FEISHU_ALLOWED_DIRS || env.AIDER_FEISHU_ALLOWED_DIRS || "";
  if (!raw) return [];
  const sep = process.platform === "win32" ? ";" : ":";
  return raw.split(sep).map((s) => s.trim()).filter(Boolean);
}

/** 从环境变量读 bot open_id（可选，用于严格群聊 @ 匹配）。 */
export function botOpenIdFromEnv(env: Record<string, string | undefined> = process.env): string {
  return (env.ARID_FEISHU_BOT_OPEN_ID || env.AIDER_FEISHU_BOT_OPEN_ID || "").trim();
}

/**
 * 从飞书消息的 content 字段提取纯文本。text 消息的 content 是 JSON 字符串
 * （如 {"text":"..."}），富文本是 {"content": [[...]]}；解析失败则原样返回。
 */
export function extractText(content: unknown): string {
  const raw = String(content ?? "");
  if (!raw) return "";
  try {
    const parsed = JSON.parse(raw) as Record<string, unknown>;
    if (typeof parsed.text === "string") return parsed.text;
    if (typeof parsed.content === "string") return parsed.content;
    return raw;
  } catch {
    return raw;
  }
}

/**
 * 把飞书消息事件（SDK 事件对象）归一化为 FeishuMessage。
 * 按 lark SDK 长连接事件的字段做保守映射（data = event 对象，含 message + sender）。
 */
export function normalizeEvent(ev: Record<string, unknown>): FeishuMessage | null {
  const message = (ev.message as Record<string, unknown>) || {};
  const chatId = (message.chat_id as string) || (ev.chat_id as string) || "";
  const messageId = (message.message_id as string) || (ev.message_id as string) || "";
  if (!chatId || !messageId) return null;
  const chatType = (message.chat_type as string) === "group" ? "group" : "p2p";
  const mentions = Array.isArray(message.mentions) ? (message.mentions as Record<string, unknown>[]) : [];
  const sender = (ev.sender as Record<string, unknown>) || {};
  const senderOpenId =
    (sender.sender_id as Record<string, unknown> | undefined)?.open_id as string | undefined ||
    (sender.open_id as string) || "";
  return {
    message_id: messageId,
    chat_id: chatId,
    chat_type: chatType,
    text: extractText(message.content),
    mentionKeys: mentions.map((m) => String(m.key || "")).filter(Boolean),
    mentionOpenIds: mentions.map((m) => {
      const id = m.id;
      if (id && typeof id === "object") return String((id as Record<string, unknown>).open_id || "");
      return String(id || "");
    }).filter(Boolean),
    sender_open_id: senderOpenId,
  };
}

/** 组装飞书 remote bot（transport 由外部注入，便于测试与替换 SDK）。 */
export function buildBot(opts: {
  transport: FeishuTransport;
  taskExecutor?: import("./bot.js").TaskExecutor;
  env?: Record<string, string | undefined>;
}): FeishuRemoteBot {
  const env = opts.env || process.env;
  return new FeishuRemoteBot({
    transport: opts.transport,
    taskExecutor: opts.taskExecutor,
    allowedDirs: allowedDirsFromEnv(env),
    botOpenId: botOpenIdFromEnv(env),
    timeoutSeconds: Number(env.ARID_FEISHU_TIMEOUT || 1800),
    replyMaxChars: Number(env.ARID_FEISHU_REPLY_MAX_CHARS || 3500),
    outputMaxLines: Number(env.ARID_FEISHU_OUTPUT_MAX_LINES || 0),
  });
}
