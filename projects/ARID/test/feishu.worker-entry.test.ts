import { test } from "node:test";
import assert from "node:assert/strict";
import {
  allowedDirsFromEnv,
  botOpenIdFromEnv,
  extractText,
  normalizeEvent,
} from "../src/feishu/worker-entry.js";

test("allowedDirsFromEnv: 空则返回 []", () => {
  assert.deepEqual(allowedDirsFromEnv({}), []);
});

test("allowedDirsFromEnv: 分号分隔（Windows）去空白", () => {
  const dirs = allowedDirsFromEnv({
    ARID_FEISHU_ALLOWED_DIRS: "E:\\a ; E:\\b;",
  });
  assert.deepEqual(dirs, ["E:\\a", "E:\\b"]);
});

test("allowedDirsFromEnv: 兼容旧变量名 AIDER_FEISHU_ALLOWED_DIRS", () => {
  const dirs = allowedDirsFromEnv({ AIDER_FEISHU_ALLOWED_DIRS: "E:\\repo" });
  assert.deepEqual(dirs, ["E:\\repo"]);
});

test("botOpenIdFromEnv: 空则空串", () => {
  assert.equal(botOpenIdFromEnv({}), "");
  assert.equal(botOpenIdFromEnv({ ARID_FEISHU_BOT_OPEN_ID: "ou_x" }), "ou_x");
});

test("normalizeEvent: 单聊消息归一化", () => {
  const msg = normalizeEvent({
    message: {
      chat_id: "c1",
      message_id: "m1",
      chat_type: "p2p",
      content: "改 E:\\repo 修 bug",
      mentions: [],
    },
    sender: { sender_id: { open_id: "u1" } },
  });
  assert.ok(msg);
  assert.equal(msg!.chat_type, "p2p");
  assert.equal(msg!.text, "改 E:\\repo 修 bug");
  assert.equal(msg!.sender_open_id, "u1");
});

test("normalizeEvent: 群聊提及提取 open_id", () => {
  const msg = normalizeEvent({
    message: {
      chat_id: "g1",
      message_id: "m2",
      chat_type: "group",
      content: "@_user_1 状态",
      mentions: [{ key: "_user_1", id: { open_id: "ou_1" } }],
    },
    sender: { open_id: "u2" },
  });
  assert.ok(msg);
  assert.equal(msg!.chat_type, "group");
  assert.deepEqual(msg!.mentionKeys, ["_user_1"]);
  assert.deepEqual(msg!.mentionOpenIds, ["ou_1"]);
});

test("normalizeEvent: 缺 chat_id/message_id 返回 null", () => {
  assert.equal(normalizeEvent({ message: {} }), null);
  assert.equal(normalizeEvent({}), null);
});

test("extractText: 解析 text 消息 JSON", () => {
  assert.equal(extractText('{"text":"改 E:\\\\repo 修 bug"}'), "改 E:\\repo 修 bug");
});

test("extractText: 富文本 content 字段", () => {
  assert.equal(extractText('{"content":"hello"}'), "hello");
});

test("extractText: 非 JSON 原样返回 / 空返回空", () => {
  assert.equal(extractText("plain"), "plain");
  assert.equal(extractText(""), "");
  assert.equal(extractText(undefined), "");
});

test("normalizeEvent: 解析 text 消息 content 为纯文本", () => {
  const msg = normalizeEvent({
    message: {
      chat_id: "c1",
      message_id: "m1",
      chat_type: "p2p",
      content: '{"text":"改 E:\\\\repo 修 bug"}',
      mentions: [],
    },
    sender: { sender_id: { open_id: "u1" } },
  });
  assert.ok(msg);
  assert.equal(msg!.text, "改 E:\\repo 修 bug");
});
