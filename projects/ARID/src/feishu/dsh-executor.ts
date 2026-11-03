/**
 * DSH 任务执行器：把飞书任务交给 DeepSeek Harness 的 headless 会话跑。
 *
 * 与 aider-gui 的 build_aider_task_executor 语义对齐：
 * - 每个任务 spawn 一个子进程（dsh --profile headless "<任务>"），cwd = 任务目录。
 * - 双模型角色密钥经环境变量注入（ARID_PLANNER_API_KEY 等），绝不进命令行。
 * - 输出流式回调给 bot（on_output / on_error），结束后回调 on_finished。
 *
 * 等价命令（用户也可手工在终端跑）：
 *   dsh --profile headless "修复登录 bug"
 */
import { spawn } from "node:child_process";
import type { TaskCallbacks } from "./bot.js";

export interface DshExecutorOptions {
  /** dsh 可执行文件（默认 PATH 上的 dsh）。 */
  dshCmd?: string;
  /** 传给 dsh 的 profile（默认 headless）。 */
  profile?: string;
  /** 双模型角色连接（planner/executor），api_key 经环境变量注入。 */
  env?: Record<string, string | undefined>;
  /** 额外环境变量（合并到子进程 env）。 */
  extraEnv?: Record<string, string | undefined>;
  /** 单任务超时毫秒（超时后杀进程并报错）。默认 30 分钟。 */
  timeoutMs?: number;
}

export function buildDshTaskExecutor(opts: DshExecutorOptions = {}) {
  const dshCmd = opts.dshCmd || process.env.DSH_CMD || "dsh";
  const profile = opts.profile || "headless";
  const baseEnv = opts.env || {};
  const extraEnv = opts.extraEnv || {};
  const timeoutMs = opts.timeoutMs ?? 30 * 60 * 1000;

  return function execute(task: string, cwd: string, callbacks: TaskCallbacks): Promise<void> {
    return new Promise<void>((resolve, reject) => {
      let settled = false;
      const finish = (fn: () => void) => {
        if (settled) return;
        settled = true;
        fn();
      };

      const child = spawn(dshCmd, ["--profile", profile, task], {
        cwd,
        env: { ...process.env, ...baseEnv, ...extraEnv },
        stdio: ["ignore", "pipe", "pipe"],
        shell: false,
        windowsHide: true,
      });

      const timer = setTimeout(() => {
        try {
          child.kill("SIGKILL");
        } catch {
          // 进程可能已退出
        }
        finish(() => reject(new Error(`任务超过 ${timeoutMs / 1000} 秒未完成，已强制停止`)));
      }, timeoutMs);

      child.stdout?.on("data", (chunk: Buffer) => {
        for (const line of chunk.toString("utf8").split(/\r?\n/)) {
          if (line) callbacks.onOutput(line);
        }
      });
      child.stderr?.on("data", (chunk: Buffer) => {
        for (const line of chunk.toString("utf8").split(/\r?\n/)) {
          if (line) callbacks.onError(line);
        }
      });
      child.on("error", (err) => {
        clearTimeout(timer);
        finish(() => reject(err));
      });
      child.on("close", (code) => {
        clearTimeout(timer);
        finish(() => {
          callbacks.onFinished(code ?? 1);
          resolve();
        });
      });
    });
  };
}
