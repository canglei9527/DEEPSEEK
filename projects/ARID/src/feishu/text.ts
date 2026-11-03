// 文本回复分段：不丢内容，优先按行边界切分。
// 从 feishu_bot.py split_reply_text 移植。
export function splitReplyText(text: string, maxChars: number): string[] {
  if (maxChars <= 0) throw new Error("maxChars must be positive");
  const remaining = String(text);
  if (!remaining) return [""];
  const parts: string[] = [];
  let rest = remaining;
  while (rest.length > maxChars) {
    const lineBreak = rest.lastIndexOf("\n", maxChars - 1);
    const cut = lineBreak > Math.floor(maxChars / 2) ? lineBreak + 1 : maxChars;
    parts.push(rest.slice(0, cut));
    rest = rest.slice(cut);
  }
  parts.push(rest);
  return parts;
}

// 去掉飞书消息里的 @提及 占位符（@_user_123 等），返回纯文本。
// 从 feishu_bot.py strip_mentions 移植。
export function stripMentions(text: string, mentions: string[]): string {
  let cleaned = String(text);
  for (const key of mentions) {
    if (!key) continue;
    const esc = key.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    cleaned = cleaned.replace(new RegExp(`@\\s*${esc}\\b`, "g"), " ");
  }
  // 兜底清理残余 @xxx 占位符（_all 群全体、未知 key 等）
  cleaned = cleaned.replace(/@[A-Za-z0-9_-]+/g, " ");
  return cleaned.replace(/\s+/g, " ").trim();
}
