import { test } from "node:test";
import assert from "node:assert/strict";
import { splitReplyText, stripMentions } from "../src/feishu/text.js";

test("splitReplyText: 空文本返回 ['']", () => {
  assert.deepEqual(splitReplyText("", 100), [""]);
});

test("splitReplyText: 短文本不分段", () => {
  assert.deepEqual(splitReplyText("hello", 100), ["hello"]);
});

test("splitReplyText: 优先按行边界切分", () => {
  const text = "line1\nline2\nline3";
  const parts = splitReplyText(text, 11);
  assert.ok(parts.length >= 2);
  assert.equal(parts.join(""), text);
});

test("splitReplyText: 不丢内容", () => {
  const text = "A".repeat(250) + "\n" + "B".repeat(50);
  const parts = splitReplyText(text, 100);
  assert.equal(parts.join(""), text);
});

test("splitReplyText: maxChars<=0 抛错", () => {
  assert.throws(() => splitReplyText("x", 0), /positive/);
});

test("stripMentions: 去掉 @提及 占位符", () => {
  assert.equal(stripMentions("@_user_123 你好", ["_user_123"]), "你好");
});

test("stripMentions: 兜底清理残余 @xxx", () => {
  assert.equal(stripMentions("@_all 一起", []), "一起");
});

test("stripMentions: 压缩空白", () => {
  assert.equal(stripMentions("  a   b ", []), "a b");
});

test("splitReplyText: 文本长度恰好等于 maxChars 返回单个元素", () => {
  const text = "A".repeat(100);
  assert.deepEqual(splitReplyText(text, 100), [text]);
});

test("splitReplyText: 单行超长每段长度不超过 maxChars 且不丢内容", () => {
  const text = "A".repeat(250);
  const parts = splitReplyText(text, 100);
  for (const part of parts) {
    assert.ok(part.length <= 100);
  }
  assert.equal(parts.join(""), text);
});

test("splitReplyText: maxChars=1 时 ab 分为 a b", () => {
  assert.deepEqual(splitReplyText("ab", 1), ["a", "b"]);
});

test("splitReplyText: 换行符恰好在切分边界时不丢内容", () => {
  const text = "a\n" + "B".repeat(99);
  const parts = splitReplyText(text, 100);
  assert.equal(parts.join(""), text);
});

test("stripMentions: mention key 含正则特殊字符做了转义", () => {
  assert.equal(stripMentions("@a.b 你好", ["a.b"]), "你好");
});

test("stripMentions: 文本中间提及只留一个空格", () => {
  assert.equal(stripMentions("你好 @_u1 世界", ["_u1"]), "你好 世界");
});

test("stripMentions: 不含提及的普通文本原样返回", () => {
  assert.equal(stripMentions("普通文本", ["_nope"]), "普通文本");
});
