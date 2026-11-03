/**
 * 飞书 worker 主入口（真实 lark SDK 长连接）。
 *
 * 用法（对齐 run_feishu_bot.py 的 .env 约定）：
 *   在 ARID 项目根建 .env（见 .env.example）：
 *     FEISHU_APP_ID=cli_xxx
 *     FEISHU_APP_SECRET=xxx
 *     ARID_FEISHU_ALLOWED_DIRS=E:\myrepo;E:\GLM5.2\hubway-smart-router
 *   然后： npx tsx src/feishu/worker-main.ts
 *
 * 密钥纪律：App Secret / API Key 只从环境读，不写日志、不进命令。
 */
import * as lark from "@larksuiteoapi/node-sdk";
import {
  buildRoleEnvMap,
  connectionFromEnv,
  ROLE_PLANNER,
  ROLE_EXECUTOR,
  isConfigured,
} from "../roles.js";
import { buildBot } from "./worker-entry.js";
import { buildDshTaskExecutor } from "./dsh-executor.js";
import { createLarkTransport, startLarkWorker } from "./lark-transport.js";
import { loadEnvFile } from "./env.js";
import * as path from "node:path";

async function main(): Promise<void> {
  // 自动加载 .env（不覆盖已存在的环境变量）。路径可用 ARID_FEISHU_ENV 覆盖，
  // 默认项目根 .env。仅打印路径，绝不打印值。
  const envPath = process.env.ARID_FEISHU_ENV || path.join(process.cwd(), ".env");
  const written = loadEnvFile(envPath);
  if (Object.keys(written).length > 0) {
    console.log(`[ARID飞书] 已加载 .env: ${envPath}`);
  }

  const appId = process.env.FEISHU_APP_ID || "";
  const appSecret = process.env.FEISHU_APP_SECRET || "";
  const encryptKey = process.env.FEISHU_ENCRYPT_KEY || "";

  if (!appId || !appSecret) {
    console.error(
      "[ARID飞书] 未配置 FEISHU_APP_ID / FEISHU_APP_SECRET，无法启动长连接。\n" +
        "请参考 README「飞书接入」一节完成飞书侧准备，并在 .env 里填好凭证（见 .env.example）。",
    );
    process.exitCode = 1;
    return;
  }

  const dirs = (process.env.ARID_FEISHU_ALLOWED_DIRS || "").trim();
  if (!dirs) {
    console.warn("[ARID飞书] 警告：未配置 ARID_FEISHU_ALLOWED_DIRS，远程执行默认禁用。");
  }

  // 双模型角色连接：从环境变量读取（与 aider-gui 飞书流程一致的变量约定）。
  const planner = connectionFromEnv(ROLE_PLANNER, process.env);
  const executor = connectionFromEnv(ROLE_EXECUTOR, process.env);
  if (!isConfigured(planner)) {
    console.warn(
      "[ARID飞书] 警告：未完整配置高等级模型（ARID_PLANNER_MODEL/_BASE_URL/_API_KEY），任务将用 profile 默认模型。",
    );
  }
  const roleEnv = buildRoleEnvMap(planner, isConfigured(executor) ? executor : null);

  // 任务执行器：每个飞书任务 spawn 一个 `dsh --profile headless` 子进程。
  const executor_ = buildDshTaskExecutor({ env: roleEnv });

  const client = new lark.Client({ appId, appSecret });
  const transport = createLarkTransport(client as unknown as Parameters<typeof createLarkTransport>[0]);
  const bot = buildBot({ transport, taskExecutor: executor_ });

  console.log(`[ARID飞书] 启动长连接（白名单：${dirs || "（未配置，禁用执行）"}）`);

  await startLarkWorker({
    appId,
    appSecret,
    encryptKey: encryptKey || undefined,
    onMessage: (msg) => bot.handle(msg),
    onReady: () => console.log("[ARID飞书] 长连接已建立，等待消息…"),
    onError: (err) => console.error("[ARID飞书] 连接错误：", err),
  });
}

main().catch((err) => {
  console.error("[ARID飞书] 启动失败：", err);
  process.exitCode = 1;
});
