/**
 * Tavily 搜索集成（纯逻辑模块，无 DSH 依赖）。
 *
 * 职责：把 @tavily/core SDK 的响应归一化成 ARID 统一结构
 *（TavilySearchResult），并通过依赖注入（TavilyClient 接口）保证
 * 测试不需要真实联网。密钥纪律：任何错误 message / 输出绝不包含明文 key。
 *
 * 依赖注入理由：@tavily/core 底层用 axios 走真实网络，单测无法跑；
 * 测试注入 fake client，只断言传给 SDK 的参数与归一化结果。
 */

import { tavily as tavilyFromSdk } from "@tavily/core";

/** 可选搜索参数（映射到 SDK 的 TavilySearchOptions 子集）。 */
export type TavilySearchOptions = {
  searchDepth?: "basic" | "advanced";
  maxResults?: number;
  includeAnswer?: boolean;
  includeRawContent?: boolean;
};

/** 归一化后的一条来源（snippet = SDK content，publishedAt = SDK publishedDate）。 */
export interface TavilySource {
  url: string;
  title?: string;
  snippet?: string;
  score?: number;
  publishedAt?: string;
}

/** 归一化后的搜索结果。 */
export interface TavilySearchResult {
  query: string;
  answer?: string;
  sources: TavilySource[];
}

/** 归一化失败的错误码。 */
export type TavilyErrorCode = "CREDENTIAL_MISSING" | "API_ERROR" | "ABORTED" | "INVALID_RESPONSE";

/** 归一化层抛出的错误：code 携带原因，message 绝不包含 key。 */
export class TavilyError extends Error {
  readonly code: TavilyErrorCode;
  constructor(code: TavilyErrorCode, message: string) {
    super(message);
    this.name = "TavilyError";
    this.code = code;
  }
}

/** SDK 的 search 能力抽象（供依赖注入；真实实现由 @tavily/core 提供）。 */
export interface TavilyClient {
  search(query: string, options: Record<string, unknown>): Promise<unknown>;
}

/** 依赖注入：用 apiKey 创建一个 client。 */
export type TavilyClientFactory = (apiKey: string) => TavilyClient;

/** 依赖注入参数。 */
export interface TavilySearchDeps {
  createClient?: TavilyClientFactory;
}

/** 默认参数。 */
const DEFAULT_OPTIONS: Required<TavilySearchOptions> = {
  searchDepth: "advanced",
  maxResults: 5,
  includeAnswer: true,
  includeRawContent: false,
};

/** 归一化参数：缺省字段填默认值。 */
function normalizeOptions(options?: TavilySearchOptions): Required<TavilySearchOptions> {
  return { ...DEFAULT_OPTIONS, ...(options ?? {}) };
}

/** 真值判断：原样透传布尔（false 保持 false）。 */
function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

/**
 * 把一条 SDK result 归一化成 TavilySource。
 * SDK 真实字段名为 content / publishedDate（不是 spec 假设的 published_date），
 * 归一化为 snippet / publishedAt；为兼容老 SDK 也容错 published_date。
 */
function normalizeSource(item: Record<string, unknown>): TavilySource {
  const url = typeof item.url === "string" ? item.url : "";
  const source: TavilySource = { url };
  if (typeof item.title === "string" && item.title) source.title = item.title;
  // content → snippet（SDK 主字段）；缺 content 才退回 raw_content 兜底
  const content = typeof item.content === "string" ? item.content : "";
  if (content) {
    source.snippet = content;
  } else if (typeof item.raw_content === "string") {
    source.snippet = item.raw_content;
  }
  if (typeof item.score === "number") source.score = item.score;
  // publishedAt：SDK 用 publishedDate；容错 spec 里的 published_date
  const published =
    typeof item.publishedDate === "string"
      ? item.publishedDate
      : typeof item.published_date === "string"
        ? item.published_date
        : undefined;
  if (published) source.publishedAt = published;
  return source;
}

/**
 * 归一化 SDK 响应：results 必须为数组，否则 INVALID_RESPONSE。
 * answer 原样透传，results[] 逐条映射成 sources[]。
 */
function normalizeResponse(raw: unknown, defaultQuery: string): TavilySearchResult {
  if (!isRecord(raw)) throw new TavilyError("INVALID_RESPONSE", "Tavily response is not an object");
  const results = raw.results;
  if (!Array.isArray(results)) {
    throw new TavilyError("INVALID_RESPONSE", "Tavily response has no results array");
  }
  const sources: TavilySource[] = [];
  for (const item of results) {
    if (!isRecord(item)) continue; // 越过非法条目而非整体报错
    sources.push(normalizeSource(item));
  }
  const result: TavilySearchResult = {
    query: typeof raw.query === "string" && raw.query ? raw.query : defaultQuery,
    sources,
  };
  if (typeof raw.answer === "string" && raw.answer) result.answer = raw.answer;
  return result;
}

/**
 * 搜索：解析 TAVILY_API_KEY，用注入/默认 factory 创建 client，调 search，
 * 再归一化成 TavilySearchResult。
 *
 * @param query 搜索词
 * @param options 可选搜索参数（缺省用默认值）
 * @param deps 依赖注入；不传则用 @tavily/core 的 tavily({ apiKey }) 真实 client
 * @returns 归一化结果
 * @throws {TavilyError} key 缺失 CREDENTIAL_MISSING / 结构非法 INVALID_RESPONSE / 请求失败 API_ERROR
 */
export async function tavilySearch(
  query: string,
  options?: TavilySearchOptions,
  deps?: TavilySearchDeps,
): Promise<TavilySearchResult> {
  const factory = deps?.createClient ?? defaultTavilyClient;
  const opts = normalizeOptions(options);

  // 解析 key：env 优先，空则 CREDENTIAL_MISSING（message 不含 key）
  const apiKey = tavilyKeyFromEnv();
  if (!apiKey) throw new TavilyError("CREDENTIAL_MISSING", "Tavily API key is missing");

  const client = factory(apiKey);
  const sdkOptions: Record<string, unknown> = {
    searchDepth: opts.searchDepth,
    maxResults: opts.maxResults,
    includeAnswer: opts.includeAnswer,
    includeRawContent: opts.includeRawContent,
  };

  let raw: unknown;
  try {
    raw = await client.search(query, sdkOptions);
  } catch (error) {
    // 注入的 fake 或真实 SDK 抛错都归为 API_ERROR；message 不泄露 key
    throw new TavilyError("API_ERROR", `Tavily search request failed: ${describeError(error)}`);
  }
  return normalizeResponse(raw, query);
}

/** 把未知错误转成不含 key 的安全描述。 */
function describeError(error: unknown): string {
  if (!error) return "unknown error";
  // 只取 message 字段，剥离可能携带 key 的完整堆栈
  if (typeof error === "object") {
    const ctor = (error as { constructor?: { name?: string } }).constructor;
    const name = ctor && ctor.name ? ctor.name : "Error";
    const message = (error as { message?: unknown }).message;
    return typeof message === "string" ? `${name}: ${message}` : name;
  }
  return String(error);
}

/** 真实默认 client：用 @tavily/core 的 tavily({ apiKey }) 创建。 */
function defaultTavilyClient(apiKey: string): TavilyClient {
  // @tavily/core 的 TavilyClient.search 已是 (query, options) => Promise<TavilySearchResponse>
  return tavilyFromSdk({ apiKey });
}

/**
 * 从环境读取 TAVILY_API_KEY（trim 后非空才返回）。
 * env 缺省用 process.env。
 */
export function tavilyKeyFromEnv(
  env: Record<string, string | undefined> = process.env,
): string | undefined {
  const value = env["TAVILY_API_KEY"];
  const trimmed = value ? value.trim() : "";
  return trimmed ? trimmed : undefined;
}

/** 脱敏 key：头 4 字符 + '****' + 尾 4 字符；长度 ≤ 8 时整体 '****'。 */
export function redactKey(key: string): string {
  const k = String(key);
  if (k.length <= 8) return "****";
  return `${k.slice(0, 4)}****${k.slice(-4)}`;
}
