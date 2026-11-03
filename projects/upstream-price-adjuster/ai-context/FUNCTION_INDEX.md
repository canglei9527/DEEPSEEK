# 函数索引

| 符号 | 源文件 | 职责 | 一跳项目自有依赖 | 最小测试 | 扩大范围条件 |
|---|---|---|---|---|---|
| `parseMultiplier` | `src/pricing.js` | 将数字、`x`/`×` 前缀规范化为非负倍率 | `src/config.js` | `tests/pricing.test.mjs` | 输入格式契约变化 |
| `calculateAdjustedMultiplier` | `src/pricing.js` | 执行“上游倍率 + 调整值”并按精度取整 | `parseMultiplier`, `roundTo` | `tests/pricing.test.mjs` | 计价公式或货币规则变化 |
| `calculateRows` | `src/pricing.js` | 批量校验并计算模型行 | `calculateRow` | `tests/pricing.test.mjs` | 数据模型跨进程或远程同步 |
| `parseBulkText` | `src/pricing.js` | 解析表格、逗号和 `×` 格式的批量文本 | `parseMultiplier` | `tests/pricing.test.mjs` | 新增文件格式 |
| `rowsToCsv` | `src/pricing.js` | 生成可下载的 CSV | `formatMultiplier` | `tests/pricing.test.mjs` | 导出 schema 变化 |
| `init` | `src/app.js` | 绑定界面事件并初始化本地状态 | `src/pricing.js`, `src/config.js` | 浏览器手动检查 | 引入框架或后端 API |
| `createDashboardServer` | `server.mjs` | 绑定 loopback 静态资源与同步控制 API；关闭时请求停止子进程 | `src/dai-sync-controller.js` | `tests/dashboard.test.mjs` | API 路由或本地边界变化 |
| `chooseDashboardPort` / `launchDashboard` | `scripts/launch-dashboard.mjs` | 探测新版 `/api/sync/status`，跳过旧/未知占用者，启动并打开实际 loopback 端口 | `server.mjs`, `launch-dashboard.cmd` | `tests/dashboard.test.mjs` | 启动器或端口选择策略变化 |
| `restart-dashboard.cmd` | `restart-dashboard.cmd`, `scripts/restart-dashboard.ps1` | 仅识别本项目 dashboard API 和 Node `server.mjs` 进程，停止后调用现有启动器重新加载代码 | `server.mjs`, `launch-dashboard.cmd` | PowerShell 语法检查 + 手动双击 | Windows 进程识别或启动策略变化 |
| `DaiSyncController.start` / `preview` / `stop` | `src/dai-sync-controller.js` | 以固定参数数组独占启动/停止现有 CLI，维护状态、日志、差异和跳过列表 | `bin/dai-group-sync.mjs`, `normalizeDaiSyncConfig` | `tests/dashboard.test.mjs` | 子进程协议、停止策略或敏感值边界变化 |
| `sync-console-app` | `src/sync-console-app.js` | 控制台状态卡片、配置校验、dry-run/apply 模式选择和日志列表 | `server.mjs` `/api/sync/*` | `tests/dashboard.test.mjs` + 浏览器手动检查 | 浏览器 API 或 UI 交互变化 |
| `createDaiSyncPlan` | `src/dai-sync-core.js` | 严格同名一对一匹配，计算上游倍率 + `adjustment`（默认 0.02），生成更新/跳过计划 | `parseMultiplier`, `calculateAdjustedMultiplier` | `tests/dai-sync.test.mjs` | D-AI 字段名或规则变化 |
| `createDaiLifecyclePlan` | `src/dai-lifecycle-core.js` | 按已保存上游目标/精确名称生成已有分组倍率更新、新分组创建、关联目标、逐模型规则，以及精确匹配的恢复和失效计划；已有分组不补目标/规则 | `createDaiLifecyclePlan` 无远程依赖 | `tests/dai-sync.test.mjs` | D-AI targets/rules 字段、匹配或生命周期策略变化 |
| `runDaiLifecycleCycle` | `src/dai-sync-runner.js` | 读取上游/分组/目标/规则，dry-run 输出倍率更新、创建、恢复和分组停用计划，apply 按倍率更新→创建→恢复→分组停用顺序执行，首个写入错误停止 | `createDaiLifecyclePlan`, adapter lifecycle methods | `tests/dai-sync.test.mjs` | 生命周期写入顺序、安全门或部分成功语义变化 |
| `normalizeDaiSyncConfig` | `src/dai-rest-adapter.js` | 校验轮询、精度、容差、字段路径、端点和请求模板 | `DAI_STATIC_ENDPOINTS` | `tests/dai-sync.test.mjs` | 认证或 REST schema 变化 |
| `createDaiRestAdapter` | `src/dai-rest-adapter.js` | 配置化读取上游/分组，并在显式 apply 时使用环境变量 REST 或保存的 browser 会话发送 PATCH | `normalizeDaiSyncConfig`, `DaiBrowserSession`, `fetch` | `tests/dai-sync.test.mjs`（mock fetch/session） | 实际 API 认证/响应确认 |
| `DaiBrowserSession` | `src/dai-browser-session.js` | 打开隔离 Chrome 供手动登录，受控保存 storage state，加载 `requestPageUrl` 捕获同源前端认证请求头，并用 headless 页面请求复用会话 | `playwright-core`, Chrome | `tests/dai-browser-session.test.mjs`（fake Playwright） | 实际登录/验证码流程或前端请求拦截器变化 |
| `runDaiSyncCycle` | `src/dai-sync-runner.js` | 单轮读取、dry-run 预览、顺序更新、写入错误停止和摘要 | `createDaiSyncPlan`, adapter | `tests/dai-sync.test.mjs` | 轮询策略或并发策略变化 |
| `main` | `bin/dai-group-sync.mjs` | CLI 参数、安全双开关、一次/循环运行 | `createDaiRestAdapter`, `runDaiSyncCycle` | `npm run sync -- --help`, `npm run sync:mock` | CLI contract 变化 |
