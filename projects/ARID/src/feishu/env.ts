/**
 * 极简 .env 加载器（纯函数，无外部依赖）。
 *
 * 语义对齐 dotenv：
 * - 错误/文件不存在 -> 返回 {}，不抛异常。
 * - 每行 `KEY=VALUE`：跳过空行与 # 注释；值可带可选两侧双引号（去除）；
 *   值内含 `=` 时按第一个 `=` 分割；键只允许 /^[A-Za-z_][A-Za-z0-9_]*$/，非法键跳过。
 * - 不覆盖已存在的环境变量（只有 process.env 中不存在该键时才写入）。
 *
 * 返回值 = 本次**实际写入**的 {key: value}（仅新增项），便于测试与日志。
 *
 * 密钥纪律：调用方绝不打印返回值里的 value（可能含 App Secret / API Key）。
 */
import fs from "node:fs";

const ENV_KEY_RE = /^[A-Za-z_][A-Za-z0-9_]*$/;

/** 把一行 `KEY=VALUE` 解析为 {key, value}；非法行返回 null。 */
export function parseEnvLine(line: string): { key: string; value: string } | null {
  const eq = line.indexOf("=");
  if (eq < 1) return null; // 无 '=' 或键为空
  const key = line.slice(0, eq).trim();
  if (!ENV_KEY_RE.test(key)) return null; // 非法键
  let value = line.slice(eq + 1).trim();
  // 可选两侧双引号（两边都必须是引号才剥去）
  if (value.length >= 2 && value.startsWith('"') && value.endsWith('"')) {
    value = value.slice(1, -1);
  }
  return { key, value };
}

/**
 * 读取并应用 .env 文件（UTF-8）。不覆盖已存在的环境变量。
 * 返回本次实际写入的 {key: value}（仅新增项）。
 */
export function loadEnvFile(filePath: string): Record<string, string> {
  let raw: string;
  try {
    raw = fs.readFileSync(filePath, "utf8");
  } catch {
    return {}; // 文件不存在 / 读失败
  }
  const written: Record<string, string> = {};
  for (const rawLine of raw.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith("#")) continue; // 空行或注释
    const parsed = parseEnvLine(line);
    if (!parsed) continue; // 非法行跳过
    const { key, value } = parsed;
    if (process.env[key] === undefined) {
      process.env[key] = value;
      written[key] = value;
    }
    // 已存在则不覆盖，也不计入 written
  }
  return written;
}
