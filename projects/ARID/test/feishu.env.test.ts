import { test, after } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { loadEnvFile, parseEnvLine } from "../src/feishu/env.js";

// 记录每个测试可能写入的 env 键，跑完删除，绝不污染其他测试的 process.env。
const touchedKeys = new Set<string>();
const tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), "arid-env-test-"));

after(() => {
  for (const k of touchedKeys) {
    delete process.env[k];
  }
  fs.rmSync(tmpRoot, { recursive: true, force: true });
});

/** 写入一个临时 .env 文件，返回绝对路径。 */
function writeTmpEnv(content: string): string {
  const p = path.join(tmpRoot, `env-${Date.now()}-${Math.random().toString(36).slice(2)}.env`);
  fs.writeFileSync(p, content, "utf8");
  return p;
}

test("parseEnvLine: 正常 KV", () => {
  assert.deepEqual(parseEnvLine("A=1"), { key: "A", value: "1" });
  assert.deepEqual(parseEnvLine("A=B=C"), { key: "A", value: "B=C" }); // 值内含 =
});

test("parseEnvLine: 两侧双引号去除，仅一边不去除", () => {
  assert.deepEqual(parseEnvLine('A="x y"'), { key: "A", value: "x y" });
  assert.deepEqual(parseEnvLine('A="x y'), { key: "A", value: '"x y' });
  assert.notDeepEqual(parseEnvLine('A="x" '), null);
});

test("parseEnvLine: 非法键返回 null", () => {
  assert.equal(parseEnvLine("1BAD=x"), null); // 数字开头
  assert.equal(parseEnvLine("A-B=1"), null); // 连字符非法
  assert.equal(parseEnvLine("A B=1"), null); // 空格非法
  assert.equal(parseEnvLine("=1"), null); // 无键
});

test("loadEnvFile: 正常解析 + 中文路径/中文值", () => {
  const dir = "E:\\myrepo;E:\\GLM5.2\\hubway-smart-router";
  const p = writeTmpEnv(
    `FEISHU_APP_ID=cli_test_app\nARID_FEISHU_ALLOWED_DIRS="${dir}"\nARID_PLANNER_MODEL="gpt-5.6-sol"\nA=B=C\n`,
  );
  const written = loadEnvFile(p);
  assert.equal(written.FEISHU_APP_ID, "cli_test_app");
  assert.equal(written.ARID_FEISHU_ALLOWED_DIRS, dir);
  assert.equal(written.ARID_PLANNER_MODEL, "gpt-5.6-sol");
  assert.equal(written.A, "B=C");
  touchedKeys.add("FEISHU_APP_ID");
  touchedKeys.add("ARID_FEISHU_ALLOWED_DIRS");
  touchedKeys.add("ARID_PLANNER_MODEL");
  touchedKeys.add("A");
});

test("loadEnvFile: 注释与空行跳过，非法键跳过", () => {
  const p = writeTmpEnv(
    "# 注释行\n\nGOOD=1\n1BAD=x\n# 后面注释 GOOD2=2\nUNUSED_REALLY=3\n",
  );
  const written = loadEnvFile(p);
  assert.deepEqual(Object.keys(written), ["GOOD", "UNUSED_REALLY"]);
  assert.equal(written.GOOD, "1");
  touchedKeys.add("GOOD");
  touchedKeys.add("UNUSED_REALLY");
});

test("loadEnvFile: 文件不存在返回 {}", () => {
  assert.deepEqual(
    loadEnvFile(path.join(tmpRoot, "no-such-dir", "missing.env")),
    {},
  );
});

test("loadEnvFile: 不覆盖已存在的环境变量", () => {
  process.env.ARID_ENV_TEST_EXISTING = "original";
  touchedKeys.add("ARID_ENV_TEST_EXISTING");
  const p = writeTmpEnv("ARID_ENV_TEST_EXISTING=fromfile\nARID_ENV_TEST_NEW=added\n");
  const written = loadEnvFile(p);
  // 已存在的键不被覆盖，也不计入返回值
  assert.equal(process.env.ARID_ENV_TEST_EXISTING, "original");
  assert.equal(Object.prototype.hasOwnProperty.call(written, "ARID_ENV_TEST_EXISTING"), false);
  // 新增键被写入
  assert.equal(written.ARID_ENV_TEST_NEW, "added");
  touchedKeys.add("ARID_ENV_TEST_NEW");
});
