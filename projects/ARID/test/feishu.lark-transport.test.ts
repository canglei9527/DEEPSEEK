import { test } from "node:test";
import assert from "node:assert/strict";
import { createLarkTransport, type ReplyClient } from "../src/feishu/lark-transport.js";

test("createLarkTransport: 按 message_id 回发纯文本", async () => {
  const calls: Array<{ messageId: string; content: string; msgType: string }> = [];
  const stub: ReplyClient = {
    im: {
      message: {
        async reply(payload) {
          calls.push({
            messageId: payload.path.message_id,
            content: payload.data.content,
            msgType: payload.data.msg_type,
          });
        },
      },
    },
  };
  const transport = createLarkTransport(stub);
  await transport.reply("om_123", "你好");
  assert.equal(calls.length, 1);
  assert.equal(calls[0]!.messageId, "om_123");
  assert.equal(calls[0]!.msgType, "text");
  assert.deepEqual(JSON.parse(calls[0]!.content), { text: "你好" });
});

test("createLarkTransport: 多段回复逐个调用", async () => {
  const ids: string[] = [];
  const stub: ReplyClient = {
    im: {
      message: {
        async reply(payload) {
          ids.push(payload.path.message_id);
        },
      },
    },
  };
  const transport = createLarkTransport(stub);
  await transport.reply("m1", "a");
  await transport.reply("m1", "b");
  assert.deepEqual(ids, ["m1", "m1"]);
});
