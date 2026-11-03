# ARID 双模型执行配置 GUI

一个零依赖、纯 Node 内置模块 + 本地 yaml 库的迷你 Web 工具，用于编辑 DSH 用户配置
`~/.dsh/settings.yaml` 中的 `arid-dual-model.executor` 段（执行模型的 provider / model），
并支持自动备份与一键恢复。

> 配置文件默认是 `C:\Users\<用户名>\.dsh\settings.yaml`（Windows）。

## 快速启动

双击 `start-dual-model-gui.bat`：

1. 先探测 `http://127.0.0.1:3090/api/health`——若服务已在运行，只打开浏览器；
2. 否则用 PowerShell `Start-Process -WindowStyle Hidden` 在后台启动
   `node server.mjs`（工作目录 = 批处理所在目录），轮询本机 health 接口至多 10s，
   就绪后自动打开浏览器。

命令行的等价启动：

```bat
node server.mjs                       :: 默认端口 3090
node server.mjs --port 3091           :: 换端口
node server.mjs --settings "C:\path\settings.yaml"
node server.mjs --backup-dir "D:\my-backups"
```

服务只监听 `127.0.0.1`，并对备份读取/删除做了路径穿越防护。

## 机制说明（热重载原理）

底层事实：`@deepseek-ai/dsh-settings-file` 在用户目录 `settings.yaml` 上用 chokidar 监听
（`watch:true`、`debounceMs 100`、`awaitWriteFinish` stabilityThreshold 100ms）。当文件被
外部替换（原子 rename）时：

- watcher 触发 **hot-publish**（reload），把新内容发布进 settings 服务；
- 下一次 `subagent_executor` 调用即读到新模型值；
- **无需重启、无需任何 IPC**。

所以本 GUI 只要**原子地覆盖文件**，DSH 约 100ms 内就会热重载。为与 DSH 自身的写入互斥，
本工具复刻了 `@deepseek-ai/dsh-atomic-write` 的写协议：

- **原子写**：先在目标同目录写 `<file>.<6字节hex>.tmp`（`flag:'wx'`、模式 `0o600`），
  再 `fs.rename` 覆盖目标。绝不先删目标再写（watcher 在 ENOENT 时会发布空配置）。
- **写入锁**：通过创建 `<settings.yaml>.lock`（`wx`、写入 `pid\n`、模式 `0o600`）与其他
  写入者互斥；EEXIST 则指数退避重试（20ms 起、最大 200ms、总超时 2s），完成后 finally 移除。

编辑方式与 DSH 一致：`yaml` 库 `parseDocument` → 只对 `arid-dual-model.executor.provider`
与 `.model` 两个叶子 `setIn/deleteIn` → `toString`。因此**注释行、其他段、无关内容逐字节
保留**，只动目标两个叶子。

## 备份 / 恢复

- **自动备份**：首次访问 `/api/state` 时，若备份目录为空则自动生成一份
  `settings-initial-<时间戳>.yaml`（“初始”基线）。每次“保存执行模型 / 恢复”在写文件前
  先生成一份 `settings-<时间戳>.yaml`（自动）。
- **手动备份**：页面按钮生成 `settings-manual-<时间戳>.yaml`。
- **自动清理**：每次产生自动备份后，保留最近 30 个 auto+manual（按 mtime 排序），
  initial 从不清理。
- **恢复**：选择某个备份“恢复”，会先把当前配置自动备份一份，再用备份内容原子覆盖
  `settings.yaml`（写入前会先校验 YAML 是否可解析，损坏内容拒写）。

**密钥纪律提示**：备份文件是 `settings.yaml` 的完整快照，**可能包含敏感配置**（例如
provider 的 apiKeyEnv 名、以及其他任何被写进该文件的密钥/令牌的引用），属敏感文件，
**请勿外传、勿提交到任何仓库**（本目录的 `.gitignore` 已忽略 `node_modules/`、`defaults/`
与 `*.bak*`，请同样保护备份目录）。

## 测试运行方式

在 `tools/dual-model-gui` 目录下执行：

```bat
node --test test/                              :: 常规方式（每个用例文件跑在独立子进程）
node --test --test-isolation=none test/server.test.mjs   :: 单进程方式（沙箱/受限环境适用）
```

测试用 `node:test` + `node:assert`，通过 `startServer()` 在**进程内**启动被测 server
（不 spawn 子进程、不依赖固定端口），在系统临时目录建立独立 fixture，覆盖：状态读取、
注释保留的叶子编辑、相同值跳过写入、参数校验、恢复往返、路径穿越防护、删除、
损坏 YAML（409）。无需任何额外依赖，也**不会触碰真实 `settings.yaml`**。

## 交付文件

| 文件 | 说明 |
| --- | --- |
| `server.mjs` | HTTP 后端（默认 127.0.0.1:3090，全套 API） |
| `index.html` | 单文件前端（内联 CSS/JS，无外部引用，中英、深色/浅色自适应） |
| `start-dual-model-gui.bat` | 双击启动/复用服务的启动器 |
| `test/server.test.mjs` | 自动化测试（`node --test test/`） |

> 本目录下历史遗留的 `启动.bat`（旧端口 38765）与 `defaults/` 属于更早的设计，非本
> 交付物；如需清理可由项目负责人决定。
