/**
 * 飞书远程控制核心（transport 注入，无 DSH/SDK 依赖，可测试）。
 *
 * 从 feishu_bot.py FeishuRemoteBot 移植的核心语义：
 * - 消息路由：`改 <目录> <任务>` / `状态` / `停止`。
 * - 群聊需 @ 机器人；单聊直接响应。
 * - 同一时刻只允许一个任务（单飞）。
 * - 白名单目录才允许执行；未配置白名单时默认禁用。
 * - 完成/超时/失败都回发文本，不静默。
 * - 回复普通文本消息（不用交互卡片，避免 HTTP 400 导致无回复）。
 */

import { stripMentions, splitReplyText } from "./text.js";
import { resolve as pathResolve } from "node:path";
import { statSync } from "node:fs";

/** 归一化后的飞书消息（由 transport 把飞书 SDK 事件转换而来）。 */
export interface FeishuMessage {
  message_id: string;
  chat_id: string;
  chat_type: "p2p" | "group";
  text: string;
  /** @提及 的 key 列表（用于剥离占位符与匹配机器人）。 */
  mentionKeys: string[];
  /** 被 @ 的实体 open_id 列表（用于匹配 bot_open_id）。 */
  mentionOpenIds: string[];
  sender_open_id: string;
}

/** 回复 transport（由外部注入，对接飞书 SDK）。 */
export interface FeishuTransport {
  reply(messageId: string, text: string): Promise<void>;
}

/** 任务执行器契约：executor(task, cwd, bot)。 */
export type TaskExecutor = (
  task: string,
  cwd: string,
  bot: TaskCallbacks,
) => Promise<void>;

export interface TaskCallbacks {
  onStarted(): void;
  onOutput(line: string): void;
  onError(line: string): void;
  onFinished(exitCode: number): void;
}

export interface ActiveTask {
  task: string;
  cwd: string;
  startedAt: number;
  replyTo: string;
}

export interface FeishuBotOptions {
  transport: FeishuTransport;
  allowedDirs: string[];
  timeoutSeconds?: number;
  botOpenId?: string;
  taskExecutor?: TaskExecutor;
  replyMaxChars?: number;
  outputMaxLines?: number;
  now?: () => number;
}

// 注意：不能用 \b——JS 的 \b 只认 ASCII \w，中文命令词后不匹配（Python \w 是 Unicode）。
// 用「空白或行尾」作为命令词边界。
const COMMAND_RE = /^(改|状态|停止)(?:[\s\u3000]|$)([\s\S]*)$/;
export const HELP_TEXT =
  "支持的命令：\n" +
  "改 <目录> <任务>   在指定仓库执行 AI 编程任务\n" +
  "状态               查看当前会话\n" +
  "停止               停止当前任务\n" +
  "示例：改 E:\\myrepo 修复登录 bug";

export class FeishuRemoteBot {
  private readonly transport: FeishuTransport;
  private readonly allowedDirs: Set<string>;
  private readonly timeoutSeconds: number;
  private readonly botOpenId: string;
  private readonly taskExecutor?: TaskExecutor;
  private readonly replyMaxChars: number;
  private readonly outputMaxLines: number;
  private readonly now: () => number;

  private active: ActiveTask | null = null;
  private output: string[] = [];
  private timer: ReturnType<typeof setTimeout> | null = null;
  private lastFinished = "空闲";

  constructor(opts: FeishuBotOptions) {
    this.transport = opts.transport;
    this.allowedDirs = new Set(
      opts.allowedDirs.filter(Boolean).map((p) => normCase(abspath(p))),
    );
    this.timeoutSeconds = opts.timeoutSeconds ?? 1800;
    this.botOpenId = opts.botOpenId ?? "";
    this.taskExecutor = opts.taskExecutor;
    this.replyMaxChars = opts.replyMaxChars ?? 3500;
    this.outputMaxLines = opts.outputMaxLines ?? 0;
    this.now = opts.now ?? (() => Date.now() / 1000);
  }

  /** 对外入口：transport 事件回调。绝不抛给 SDK 事件循环。 */
  async handle(msg: FeishuMessage): Promise<void> {
    try {
      let text: string;
      if (msg.chat_type === "group") {
        if (!this.mentionedBot(msg)) return; // 群聊未 @ 机器人不响应
        text = stripMentions(msg.text, msg.mentionKeys);
      } else {
        text = msg.text.trim();
      }
      text = text.trim();
      if (!text) return;
      const match = COMMAND_RE.exec(text);
      if (!match) {
        await this.reply(msg, HELP_TEXT);
        return;
      }
      const action = match[1]!;
      const rest = (match[2] || "").trim();
      if (action === "状态") await this.handleStatus(msg);
      else if (action === "停止") await this.handleStop(msg);
      else await this.handleRun(msg, rest);
    } catch (exc) {
      await this.reply(msg, `处理消息出错：${String(exc)}`);
    }
  }

  private async handleRun(msg: FeishuMessage, rest: string): Promise<void> {
    if (!this.taskExecutor) {
      await this.reply(msg, "未配置任务执行器，无法执行任务");
      return;
    }
    const parts = rest.split(/\s+/, 2);
    if (parts.length < 2 || !parts[0] || !parts[1]) {
      await this.reply(msg, "格式：改 <目录> <任务>\n例如：改 E:\\myrepo 修复登录 bug");
      return;
    }
    const [rawDir, task] = [parts[0], parts[1]];
    const cwd = abspath(rawDir);
    if (!this.allowedDirs.has(normCase(cwd))) {
      await this.reply(
        msg,
        `目录不在允许执行的白名单：${rawDir}\n` +
          "启动前请设置环境变量 ARID_FEISHU_ALLOWED_DIRS（多个用平台路径分隔符分隔）",
      );
      return;
    }
    if (!isDir(cwd)) {
      await this.reply(msg, `目录不存在：${cwd}`);
      return;
    }
    if (this.active) {
      await this.reply(
        msg,
        `已有任务进行中：《${this.active.task}》（${this.active.cwd}）。可发送“状态”查看或“停止”终止`,
      );
      return;
    }
    this.active = { task, cwd, startedAt: this.now(), replyTo: msg.message_id };
    this.output = [];
    this.timer = setTimeout(() => void this.onTimeout(), this.timeoutSeconds * 1000);

    await this.reply(msg, `已开始处理《${task}》\n目录：${cwd}\n完成后会回发结果摘要`);
    try {
      await this.taskExecutor(task, cwd, {
        onStarted: () => {},
        onOutput: (line) => this.onOutput(line),
        onError: (line) => this.onOutput(line),
        onFinished: (code) => this.onFinished(code),
      });
    } catch (exc) {
      this.clearActive();
      await this.reply(msg, `启动任务失败：${String(exc)}`);
    }
  }

  private async handleStatus(msg: FeishuMessage): Promise<void> {
    const active = this.active;
    if (active) {
      await this.reply(
        msg,
        `进行中：《${active.task}》\n目录：${active.cwd}\n已运行 ${Math.round(this.now() - active.startedAt)} 秒`,
      );
    } else {
      await this.reply(msg, this.lastFinished !== "空闲" ? this.lastFinished : "当前空闲");
    }
  }

  private async handleStop(msg: FeishuMessage): Promise<void> {
    if (!this.active) {
      await this.reply(msg, "当前没有正在运行的任务");
      return;
    }
    // 交由外部 task controller 停止；此处只清理本地状态。
    this.clearActive();
    this.lastFinished = "任务已由用户停止";
    await this.reply(msg, "已请求停止任务");
  }

  private onOutput(line: string): void {
    if (!this.active) return;
    this.output.push(line);
    if (this.outputMaxLines > 0 && this.output.length > this.outputMaxLines) {
      this.output = this.output.slice(this.output.length - this.outputMaxLines);
    }
  }

  private onFinished(exitCode: number): void {
    const active = this.active;
    if (!active) return;
    const lines =
      this.outputMaxLines > 0 ? this.output.slice(-this.outputMaxLines) : this.output;
    const outputText = lines.join("\n");
    this.clearActive();
    this.lastFinished = `上次任务《${active.task}》退出码 ${exitCode}`;
    const summary =
      `任务《${active.task}》已结束，退出码 ${exitCode}\n\n` +
      `最近输出：\n${outputText || "（无输出）"}`;
    void this.replyTo(active.replyTo, summary);
  }

  private async onTimeout(): Promise<void> {
    const active = this.active;
    if (!active) return;
    await this.replyTo(
      active.replyTo,
      `任务《${active.task}》超过 ${this.timeoutSeconds} 秒未完成，已强制停止`,
    );
    this.clearActive();
  }

  private mentionedBot(msg: FeishuMessage): boolean {
    // 双保险：配置了 bot_open_id 时严格匹配 @ 提及；
    // 未配置时放行（im:message.group_at_msg 权限保证只收到 @ 自己的消息）。
    if (!this.botOpenId) return true;
    return msg.mentionOpenIds.includes(this.botOpenId);
  }

  private clearActive(): void {
    if (this.timer) {
      clearTimeout(this.timer);
      this.timer = null;
    }
    this.active = null;
    this.output = [];
  }

  private async reply(msg: FeishuMessage, text: string): Promise<void> {
    await this.replyTo(msg.message_id, text);
  }

  private async replyTo(messageId: string, text: string): Promise<void> {
    try {
      for (const part of splitReplyText(text, this.replyMaxChars)) {
        await this.transport.reply(messageId, part);
      }
    } catch {
      // 回复失败不阻断事件循环（如 SDK 未就绪）
    }
  }
}

// ---- 纯工具（可注入 fs 适配器，避免直接依赖 Node） ----
export interface FsAdapter {
  isDir(path: string): boolean;
  abspath(path: string): string;
}

export const defaultFs: FsAdapter = {
  isDir: (p) => {
    try {
      return statSync(p).isDirectory();
    } catch {
      return false;
    }
  },
  abspath: (p) => pathResolve(p),
};

function abspath(p: string): string {
  return defaultFs.abspath(p);
}
function isDir(p: string): boolean {
  return defaultFs.isDir(p);
}
function normCase(p: string): string {
  // Windows 大小写不敏感；其它平台保持原样。
  return process.platform === "win32" ? p.toLowerCase() : p;
}
