/**
 * 飞书 lark SDK 长连接 transport。
 *
 * 对接官方 SDK `@larksuiteoapi/node-sdk` 的：
 * - `WSClient` + `EventDispatcher`：长连接（WebSocket）接收 `im.message.receive_v1`。
 * - `Client.im.message.reply`：按 message_id 回复纯文本（与 Python 版 reply_to 语义一致，
 *   普通文本消息，不用交互卡片避免 HTTP 400）。
 *
 * 本模块只做 SDK 接线；路由/白名单/单飞在 bot.ts，事件归一化在 worker-entry.ts。
 * `createLarkTransport` 只依赖 `ReplyClient` 结构化接口，可注入 stub 独立测试。
 */
import * as lark from "@larksuiteoapi/node-sdk";
import type { FeishuMessage, FeishuTransport } from "./bot.js";
import { normalizeEvent } from "./worker-entry.js";

/** 可回复的客户端最小接口（便于测试注入 stub，不硬绑 SDK Client）。 */
export interface ReplyClient {
  im: {
    message: {
      reply(payload: {
        path: { message_id: string };
        data: { content: string; msg_type: string; reply_in_thread?: boolean };
      }): Promise<unknown>;
    };
  };
}

/** 用 lark Client 构建 FeishuTransport：按 message_id 回发纯文本。 */
export function createLarkTransport(client: ReplyClient): FeishuTransport {
  return {
    async reply(messageId: string, text: string): Promise<void> {
      await client.im.message.reply({
        path: { message_id: messageId },
        data: {
          content: JSON.stringify({ text }),
          msg_type: "text",
        },
      });
    },
  };
}

export interface LarkWorkerOptions {
  appId: string;
  appSecret: string;
  /** 可选：开启加密推送时填 encryptKey。 */
  encryptKey?: string;
  /** 处理归一化消息（通常接到 FeishuRemoteBot.handle）。 */
  onMessage: (msg: FeishuMessage) => Promise<void> | void;
  /** 可选：就绪/错误回调。 */
  onReady?: () => void;
  onError?: (error: unknown) => void;
}

/**
 * 启动长连接 worker：建立 WS 长连接，收到 `im.message.receive_v1` 时
 * 归一化并交给 onMessage。返回后连接保持，由 SDK 内部维持。
 */
export async function startLarkWorker(opts: LarkWorkerOptions): Promise<void> {
  const dispatcher = new lark.EventDispatcher({
    encryptKey: opts.encryptKey,
  }).register({
    "im.message.receive_v1": async (data) => {
      const msg = normalizeEvent(data as unknown as Record<string, unknown>);
      if (msg) await opts.onMessage(msg);
    },
  });

  const wsClient = new lark.WSClient({
    appId: opts.appId,
    appSecret: opts.appSecret,
    loggerLevel: lark.LoggerLevel.info,
    onReady: opts.onReady,
    onError: opts.onError,
  });

  await wsClient.start({ eventDispatcher: dispatcher });
}
