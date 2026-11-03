/**
 * 角色常量 + 连接配置模型（纯逻辑，无 DSH 依赖）。
 *
 * 从 aider_model_profiles.py 移植的核心语义：
 * - 三角色：planner（规划/审查，高等级）/ executor（执行，低等级）/ decision（验收决策）。
 * - 多平台 provider + OpenAI 兼容网关（base_url + api_key）。
 * - 密钥纪律：任何输出/摘要/日志绝不包含明文 Key。
 */

export const ROLE_PLANNER = "planner" as const;
export const ROLE_EXECUTOR = "executor" as const;
export const ROLE_DECISION = "decision" as const;
export type Role = typeof ROLE_PLANNER | typeof ROLE_EXECUTOR | typeof ROLE_DECISION;

/** Provider 白名单（与 Python 版一致）。openai_compatible 是网关/中转。 */
export const PROVIDERS = [
  "openai_compatible",
  "deepseek",
  "anthropic",
  "openrouter",
  "openai",
  "gemini",
] as const;
export type Provider = (typeof PROVIDERS)[number];

/** 角色 -> 环境变量名（密钥经环境变量注入，绝不进命令行/日志）。 */
export const ENV_VAR_BY_ROLE: Record<Role, string> = {
  [ROLE_PLANNER]: "ARID_PLANNER_API_KEY",
  [ROLE_EXECUTOR]: "ARID_EXECUTOR_API_KEY",
  [ROLE_DECISION]: "ARID_DECISION_API_KEY",
};

/** 角色 -> 环境变量前缀（MODEL / BASE_URL / PROVIDER 等非密钥字段）。 */
export const ROLE_ENV_PREFIX: Record<Role, string> = {
  [ROLE_PLANNER]: "ARID_PLANNER",
  [ROLE_EXECUTOR]: "ARID_EXECUTOR",
  [ROLE_DECISION]: "ARID_DECISION",
};

/**
 * 从环境变量构建一个角色的连接（provider 默认 openai_compatible，与旧版飞书流程一致）。
 * 读取 `<前缀>_MODEL / <前缀>_BASE_URL / <前缀>_API_KEY / <前缀>_PROVIDER`。
 */
export function connectionFromEnv(
  role: Role,
  source: Record<string, string | undefined>,
): RoleConnection {
  const prefix = ROLE_ENV_PREFIX[role];
  return makeRoleConnection({
    role,
    provider: source[`${prefix}_PROVIDER`] || "openai_compatible",
    model: source[`${prefix}_MODEL`] || "",
    base_url: source[`${prefix}_BASE_URL`] || "",
    api_key: source[`${prefix}_API_KEY`] || "",
  });
}

/** 把一个角色的连接转成子进程环境变量（含 KEY/MODEL/BASE_URL/PROVIDER，密钥不落日志）。 */
export function roleSubprocessEnv(conn: RoleConnection): Record<string, string> {
  const prefix = ROLE_ENV_PREFIX[conn.role];
  const env: Record<string, string> = {};
  if (conn.api_key) env[`${prefix}_API_KEY`] = conn.api_key;
  if (conn.model) env[`${prefix}_MODEL`] = conn.model;
  if (conn.base_url) env[`${prefix}_BASE_URL`] = conn.base_url;
  if (conn.provider) env[`${prefix}_PROVIDER`] = conn.provider;
  return env;
}

/** 合并多个角色连接为子进程环境变量（只有配置了密钥的角色才导出 KEY）。 */
export function buildRoleEnvMap(
  planner: RoleConnection,
  executor: RoleConnection | null,
  decision: RoleConnection | null = null,
): Record<string, string> {
  const env: Record<string, string> = { ...roleSubprocessEnv(planner) };
  if (executor) Object.assign(env, roleSubprocessEnv(executor));
  if (decision) Object.assign(env, roleSubprocessEnv(decision));
  return env;
}

export interface RoleConnectionInput {
  role: Role;
  provider?: Provider | string;
  model?: string;
  base_url?: string;
  api_key?: string;
  /** 该模型是否接受图片输入（自定义网关模型需显式声明）。 */
  supports_vision?: boolean;
  /** 网关按 UA 分流上游时，伪装 curl/8.x 才走正常上游。 */
  user_agent?: string;
  /** 思考深度 low/medium/high；空=不传。 */
  reasoning_effort?: string;
}

export interface RoleConnection {
  role: Role;
  provider: string;
  model: string;
  base_url: string;
  api_key: string;
  supports_vision: boolean;
  user_agent: string;
  reasoning_effort: string;
  /** 标准化后的 model_id，如 openai/gpt-5.6-sol。不持久化。 */
  model_id: string;
}

export function normalizeModelId(provider: string, model: string): string {
  const m = (model || "").trim();
  if (!m) return "";
  const prefix: Record<string, string> = {
    openai_compatible: "openai",
    deepseek: "deepseek",
    anthropic: "anthropic",
    openrouter: "openrouter",
    openai: "openai",
    gemini: "gemini",
  };
  const p = prefix[provider] || "";
  if (m.includes("/")) {
    // 已带前缀：openrouter 保留嵌套前缀（openrouter/anthropic/claude-3）
    if (provider === "openrouter" && !m.startsWith("openrouter/")) {
      return `openrouter/${m}`;
    }
    return m;
  }
  return p ? `${p}/${m}` : m;
}

export function makeRoleConnection(input: RoleConnectionInput): RoleConnection {
  const provider = (input.provider || "").trim();
  const model = (input.model || "").trim();
  const base_url = (input.base_url || "").trim();
  const api_key = (input.api_key || "").trim();
  const supports_vision = Boolean(input.supports_vision);
  const user_agent = (input.user_agent || "").trim();
  let reasoning_effort = (input.reasoning_effort || "").trim().toLowerCase();
  if (!["low", "medium", "high", "max"].includes(reasoning_effort)) {
    reasoning_effort = "";
  }
  return {
    role: input.role,
    provider,
    model,
    base_url,
    api_key,
    supports_vision,
    user_agent,
    reasoning_effort,
    model_id: normalizeModelId(provider, model),
  };
}

export function fromRoleConnectionInput(
  role: Role,
  data: Partial<RoleConnectionInput> | undefined,
): RoleConnection {
  return makeRoleConnection({
    role,
    provider: (data || {}).provider,
    model: (data || {}).model,
    base_url: (data || {}).base_url,
    api_key: (data || {}).api_key,
    supports_vision: (data || {}).supports_vision,
    user_agent: (data || {}).user_agent,
    reasoning_effort: (data || {}).reasoning_effort,
  });
}

/** 角色是否配置完整：provider + model + (网关需 base_url) + api_key。 */
export function isConfigured(conn: RoleConnection): boolean {
  if (!conn.provider || !conn.model || !conn.api_key) return false;
  if (conn.provider === "openai_compatible" && !conn.base_url) return false;
  return true;
}

export function missingFields(conn: RoleConnection): string[] {
  const missing: string[] = [];
  if (!conn.provider) missing.push("provider");
  if (!conn.model) missing.push("model");
  if (conn.provider === "openai_compatible" && !conn.base_url) missing.push("base_url");
  if (!conn.api_key) missing.push("api_key");
  return missing;
}

/** 脱敏摘要：只显示 configured / missing 字段名，绝不显示 Key。 */
export function redactedSummary(conn: RoleConnection): string {
  if (isConfigured(conn)) return "configured";
  const missing = missingFields(conn);
  return missing.length ? `missing: ${missing.join(", ")}` : "not configured";
}

/**
 * 校验一对角色（planner 必填；executor 若填了 model 就必须完整）。
 * 返回错误列表（空 = 通过）。本地预检，不请求网络。
 */
export function validatePair(
  planner: RoleConnection,
  executor: RoleConnection | null,
): string[] {
  const errors: string[] = [];
  if (!isConfigured(planner)) {
    errors.push(`planner: ${redactedSummary(planner)}`);
  }
  if (executor && executor.model && !isConfigured(executor)) {
    errors.push(`executor: ${redactedSummary(executor)}`);
  }
  return errors;
}
