import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  FeishuRemoteBot,
  HELP_TEXT,
  type FeishuMessage,
  type FeishuTransport,
  type TaskExecutor,
} from "../src/feishu/bot.js";

function makeMsg(partial: Partial<FeishuMessage> & { text: string }): FeishuMessage {
  return {
    message_id: "m1",
    chat_id: "c1",
    chat_type: "p2p",
    text: partial.text,
    mentionKeys: [],
    mentionOpenIds: [],
    sender_open_id: "u1",
    ...partial,
  };
}

function makeTransport(): FeishuTransport & { replies: string[] } {
  const replies: string[] = [];
  return {
    replies,
    async reply(_messageId: string, text: string) {
      replies.push(text);
    },
  };
}

/** 建一个真实存在的白名单目录（用真实 fs，覆盖 handleRun 的 isDir 分支）。 */
function makeAllowedDir(): string {
  const dir = mkdtempSync(join(tmpdir(), "arid-test-"));
  return dir;
}

/** 占位执行器：让 whitelist/format/isdir 分支可达（原版先查 executor 再查这些）。 */
const dummyExecutor: TaskExecutor = async () => {};

test("未匹配命令回帮助文本", async () => {
  const transport = makeTransport();
  const bot = new FeishuRemoteBot({ transport, allowedDirs: [] });
  await bot.handle(makeMsg({ text: "你好" }));
  assert.equal(transport.replies[0], HELP_TEXT);
});

test("白名单外目录拒绝执行", async () => {
  const transport = makeTransport();
  const bot = new FeishuRemoteBot({
    transport,
    allowedDirs: [makeAllowedDir()],
    taskExecutor: dummyExecutor,
  });
  await bot.handle(makeMsg({ text: `改 ${join(tmpdir(), "no-such-arid")} 修复 bug` }));
  assert.ok(transport.replies[0]!.includes("白名单"));
});

test("目录不存在拒绝执行（在白名单内但路径不存在）", async () => {
  const ghost = join(tmpdir(), "arid-ghost-" + Math.random().toString(36).slice(2));
  const transport = makeTransport();
  const bot = new FeishuRemoteBot({
    transport,
    allowedDirs: [ghost], // 白名单精确匹配该路径，但磁盘上不存在
    taskExecutor: dummyExecutor,
  });
  await bot.handle(makeMsg({ text: `改 ${ghost} 修复 bug` }));
  assert.ok(transport.replies[0]!.includes("目录不存在"));
});

test("格式错误提示用法", async () => {
  const transport = makeTransport();
  const bot = new FeishuRemoteBot({
    transport,
    allowedDirs: [makeAllowedDir()],
    taskExecutor: dummyExecutor,
  });
  await bot.handle(makeMsg({ text: "改 只有目录没任务" }));
  assert.ok(transport.replies[0]!.includes("格式"));
});

test("状态：空闲时返回当前空闲", async () => {
  const transport = makeTransport();
  const bot = new FeishuRemoteBot({ transport, allowedDirs: [] });
  await bot.handle(makeMsg({ text: "状态" }));
  assert.equal(transport.replies[0], "当前空闲");
});

test("停止：无任务时提示没有运行中任务", async () => {
  const transport = makeTransport();
  const bot = new FeishuRemoteBot({ transport, allowedDirs: [] });
  await bot.handle(makeMsg({ text: "停止" }));
  assert.equal(transport.replies[0], "当前没有正在运行的任务");
});

test("群聊未 @ 机器人（配置了 bot_open_id）不响应", async () => {
  const transport = makeTransport();
  const bot = new FeishuRemoteBot({
    transport,
    allowedDirs: [],
    botOpenId: "ou_bot",
  });
  await bot.handle(
    makeMsg({ text: "状态", chat_type: "group", mentionOpenIds: ["ou_other"] }),
  );
  assert.equal(transport.replies.length, 0);
});

test("群聊 @ 机器人（配置了 bot_open_id）响应", async () => {
  const transport = makeTransport();
  const bot = new FeishuRemoteBot({
    transport,
    allowedDirs: [],
    botOpenId: "ou_bot",
  });
  await bot.handle(
    makeMsg({ text: "状态", chat_type: "group", mentionOpenIds: ["ou_bot"] }),
  );
  assert.equal(transport.replies[0], "当前空闲");
});

test("单飞：已有任务进行中时第二个任务被拒", async () => {
  const dir = makeAllowedDir();
  const transport = makeTransport();
  let release!: () => void;
  const gate = new Promise<void>((r) => (release = r));
  const executor: TaskExecutor = async (_task, _cwd, cb) => {
    cb.onStarted();
    await gate;
    cb.onFinished(0);
  };
  const bot = new FeishuRemoteBot({
    transport,
    allowedDirs: [dir],
    taskExecutor: executor,
  });

  const first = bot.handle(makeMsg({ text: `改 ${dir} 第一个任务` }));
  // 等第一个任务进入执行（已回发“已开始”）
  await new Promise((r) => setTimeout(r, 20));
  const second = bot.handle(makeMsg({ text: `改 ${dir} 第二个任务` }));
  await second;
  assert.ok(transport.replies.some((r) => r.includes("已有任务进行中")));
  release();
  await first;
});

test("任务成功回发结果摘要（含退出码与最近输出）", async () => {
  const dir = makeAllowedDir();
  const transport = makeTransport();
  const executor: TaskExecutor = async (_task, _cwd, cb) => {
    cb.onStarted();
    cb.onOutput("line-a");
    cb.onOutput("line-b");
    cb.onFinished(0);
  };
  const bot = new FeishuRemoteBot({
    transport,
    allowedDirs: [dir],
    taskExecutor: executor,
    outputMaxLines: 10,
  });
  await bot.handle(makeMsg({ text: `改 ${dir} 修 bug` }));
  const summary = transport.replies.find((r) => r.includes("已结束"));
  assert.ok(summary, "应回发结束摘要");
  assert.ok(summary!.includes("退出码 0"));
  assert.ok(summary!.includes("line-a"));
  assert.ok(summary!.includes("line-b"));
});

test("任务失败回发非零退出码", async () => {
  const dir = makeAllowedDir();
  const transport = makeTransport();
  const executor: TaskExecutor = async (_task, _cwd, cb) => {
    cb.onStarted();
    cb.onFinished(1);
  };
  const bot = new FeishuRemoteBot({
    transport,
    allowedDirs: [dir],
    taskExecutor: executor,
  });
  await bot.handle(makeMsg({ text: `改 ${dir} 修 bug` }));
  assert.ok(transport.replies.some((r) => r.includes("退出码 1")));
});

test("未配置任务执行器时拒绝执行", async () => {
  const dir = makeAllowedDir();
  const transport = makeTransport();
  const bot = new FeishuRemoteBot({ transport, allowedDirs: [dir] });
  await bot.handle(makeMsg({ text: `改 ${dir} 修 bug` }));
  assert.ok(transport.replies[0]!.includes("未配置任务执行器"));
});
