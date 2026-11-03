import { test } from "node:test";
import assert from "node:assert/strict";
import {
  makeRoleConnection,
  normalizeModelId,
  isConfigured,
  missingFields,
  redactedSummary,
  validatePair,
  connectionFromEnv,
  roleSubprocessEnv,
  buildRoleEnvMap,
  ROLE_PLANNER,
  ROLE_EXECUTOR,
  ROLE_DECISION,
} from "../src/roles.js";

test("normalizeModelId: 网关模型加 openai/ 前缀", () => {
  assert.equal(normalizeModelId("openai_compatible", "gpt-5.6-sol"), "openai/gpt-5.6-sol");
  assert.equal(normalizeModelId("deepseek", "deepseek-chat"), "deepseek/deepseek-chat");
  assert.equal(normalizeModelId("openrouter", "anthropic/claude-3"), "openrouter/anthropic/claude-3");
});

test("normalizeModelId: 已带前缀保持不变 / 空模型为空", () => {
  assert.equal(normalizeModelId("openai_compatible", "openai/gpt-5.6-sol"), "openai/gpt-5.6-sol");
  assert.equal(normalizeModelId("openai", ""), "");
});

test("normalizeModelId: openrouter 嵌套前缀保留", () => {
  assert.equal(
    normalizeModelId("openrouter", "openrouter/anthropic/claude-3"),
    "openrouter/anthropic/claude-3",
  );
});

test("isConfigured: 网关缺 base_url 不算配置完整", () => {
  const conn = makeRoleConnection({
    role: ROLE_PLANNER,
    provider: "openai_compatible",
    model: "gpt-5.6-sol",
    api_key: "sk-xxx",
    base_url: "",
  });
  assert.equal(isConfigured(conn), false);
  assert.deepEqual(missingFields(conn), ["base_url"]);
});

test("isConfigured: 原生 provider 无需 base_url", () => {
  const conn = makeRoleConnection({
    role: ROLE_EXECUTOR,
    provider: "deepseek",
    model: "deepseek-chat",
    api_key: "sk-yyy",
  });
  assert.equal(isConfigured(conn), true);
  assert.deepEqual(missingFields(conn), []);
});

test("redactedSummary: 绝不包含明文 Key", () => {
  const conn = makeRoleConnection({
    role: ROLE_PLANNER,
    provider: "openai_compatible",
    model: "gpt-5.6-sol",
    api_key: "sk-super-secret-123",
    base_url: "https://molifangapi.com/v1",
  });
  const summary = redactedSummary(conn);
  assert.equal(summary, "configured");
  assert.ok(!summary.includes("sk-super-secret-123"));
});

test("redactedSummary: 缺失字段只列字段名", () => {
  const conn = makeRoleConnection({ role: ROLE_PLANNER, provider: "openai", model: "", api_key: "" });
  assert.equal(redactedSummary(conn), "missing: model, api_key");
});

test("validatePair: planner 必须完整", () => {
  const planner = makeRoleConnection({ role: ROLE_PLANNER, provider: "openai", model: "", api_key: "" });
  const errors = validatePair(planner, null);
  assert.equal(errors.length, 1);
  assert.ok(errors[0]!.startsWith("planner: missing"));
});

test("validatePair: executor 填了 model 就必须完整", () => {
  const planner = makeRoleConnection({
    role: ROLE_PLANNER,
    provider: "openai_compatible",
    model: "gpt-5.6-sol",
    api_key: "sk-a",
    base_url: "https://x/v1",
  });
  const executor = makeRoleConnection({
    role: ROLE_EXECUTOR,
    provider: "openai_compatible",
    model: "deepseek-chat",
    api_key: "",
    base_url: "https://x/v1",
  });
  const errors = validatePair(planner, executor);
  assert.equal(errors.length, 1);
  assert.ok(errors[0]!.startsWith("executor: missing"));
});

test("validatePair: 双角色齐全则通过", () => {
  const planner = makeRoleConnection({
    role: ROLE_PLANNER,
    provider: "openai_compatible",
    model: "gpt-5.6-sol",
    api_key: "sk-a",
    base_url: "https://x/v1",
  });
  const executor = makeRoleConnection({
    role: ROLE_EXECUTOR,
    provider: "deepseek",
    model: "deepseek-chat",
    api_key: "sk-b",
  });
  assert.deepEqual(validatePair(planner, executor), []);
});

test("connectionFromEnv: 从环境变量构建网关连接", () => {
  const conn = connectionFromEnv(ROLE_PLANNER, {
    ARID_PLANNER_MODEL: "gpt-5.6-sol",
    ARID_PLANNER_BASE_URL: "https://molifangapi.com/v1",
    ARID_PLANNER_API_KEY: "sk-secret",
  });
  assert.equal(conn.provider, "openai_compatible"); // 默认网关
  assert.equal(conn.model, "gpt-5.6-sol");
  assert.equal(conn.base_url, "https://molifangapi.com/v1");
  assert.equal(conn.api_key, "sk-secret");
});

test("roleSubprocessEnv: 导出 KEY/MODEL/BASE_URL/PROVIDER", () => {
  const conn = makeRoleConnection({
    role: ROLE_EXECUTOR,
    provider: "deepseek",
    model: "deepseek-chat",
    api_key: "sk-b",
  });
  assert.deepEqual(roleSubprocessEnv(conn), {
    ARID_EXECUTOR_API_KEY: "sk-b",
    ARID_EXECUTOR_MODEL: "deepseek-chat",
    ARID_EXECUTOR_PROVIDER: "deepseek",
  });
});

test("buildRoleEnvMap: 合并多角色，缺 base_url 的键不导出", () => {
  const planner = makeRoleConnection({
    role: ROLE_PLANNER,
    provider: "openai_compatible",
    model: "gpt-5.6-sol",
    base_url: "https://x/v1",
    api_key: "sk-a",
  });
  const executor = makeRoleConnection({
    role: ROLE_EXECUTOR,
    provider: "deepseek",
    model: "deepseek-chat",
    api_key: "sk-b",
  });
  const env = buildRoleEnvMap(planner, executor, null);
  assert.equal(env.ARID_PLANNER_API_KEY, "sk-a");
  assert.equal(env.ARID_EXECUTOR_API_KEY, "sk-b");
  assert.equal(env.ARID_PLANNER_BASE_URL, "https://x/v1");
  assert.equal(env.ARID_EXECUTOR_BASE_URL, undefined);
});

test("buildRoleEnvMap: 未配置的角色不导出 KEY", () => {
  const planner = makeRoleConnection({
    role: ROLE_PLANNER,
    provider: "openai_compatible",
    model: "gpt-5.6-sol",
    base_url: "https://x/v1",
    api_key: "sk-a",
  });
  const env = buildRoleEnvMap(planner, null, null);
  assert.equal(env.ARID_EXECUTOR_API_KEY, undefined);
  assert.equal(env.ARID_DECISION_API_KEY, undefined);
});
