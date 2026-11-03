# 架构说明

## 技术路线

原生 HTML/CSS/ES modules，Node.js 18 内置 `http` 模块提供仅 loopback 的静态服务器和控制 API，Node 内置 `child_process` 管理已有同步 CLI，Node 内置测试运行器执行核心逻辑测试；无第三方依赖、无远程数据存储。

## 目录结构

```text
upstream-price-adjuster/
├── index.html              # D-AI 同名分组同步桌面式控制台入口
├── sync-console.css        # 控制台响应式样式
├── pricing.html            # 原手动调价器页面
├── styles.css              # 手动调价器响应式样式
├── server.mjs              # 仅 127.0.0.1 的静态文件/API 服务器
├── launch-dashboard.cmd    # Windows 双击启动器（本机服务 + 浏览器应用窗口）
├── restart-dashboard.cmd   # Windows 一键停止旧本项目服务并重新启动
├── scripts/launch-dashboard.mjs # 探测/选择端口、启动服务并打开实际 URL
├── scripts/restart-dashboard.ps1 # 受控识别并停止旧 dashboard，再调用启动器
├── src/
│   ├── config.js           # 默认值和界面常量
│   ├── pricing.js          # 倍率解析、计算、批量解析、导出
│   ├── app.js              # DOM 事件和界面状态
│   ├── sync-console-app.js # 控制台 DOM、状态轮询与本地 API 调用
│   ├── dai-sync-core.js    # 同名精确映射、校验和倍率差异计划（纯本地）
│   ├── dai-lifecycle-core.js # 供应商分组、目标、规则、已有分组倍率更新、恢复和失效计划（纯本地）
│   ├── dai-rest-adapter.js # 可配置 REST 读/写适配器
│   ├── dai-browser-session.js # 受控 Chrome 登录、会话保存和同源请求
│   ├── dai-sync-runner.js  # 倍率/生命周期 dry-run、apply、摘要与错误停止
│   └── dai-sync-controller.js # CLI 子进程生命周期、日志/摘要解析和安全启动门
├── bin/dai-group-sync.mjs  # Node CLI：一次或持续轮询，dry-run 默认
├── scripts/mock-sync-dry-run.mjs # 本地 HTTP mock CLI 验证
├── dai-sync.config.example.json  # 不含凭据的配置示例
├── tests/pricing.test.mjs  # 核心逻辑回归测试
├── tests/dai-sync.test.mjs # 同步核心和 mock fetch 测试
├── tests/dashboard.test.mjs # 控制台假子进程和 loopback HTTP 测试
└── ai-context/             # 维护索引与记录
```

## 模块职责

| 模块 | 职责 | 对外接口 |
|---|---|---|
| `src/config.js` | 集中管理默认调整值、精度、行数上限和示例数据 | 配置常量 |
| `src/pricing.js` | 规范化倍率、执行加价公式、解析批量文本、生成导出内容 | `calculateAdjustedMultiplier`、`calculateRows`、`parseBulkText`、`rowsToCsv` |
| `src/app.js` | 管理浏览器状态、输入事件、结果渲染和下载 | 页面入口 `init` |
| `server.mjs` | 仅在 `127.0.0.1` 提供白名单静态文件及 `/api/sync/*` 控制 API；关闭时请求停止子进程 | `createDashboardServer` |
| `src/sync-console-app.js` | 渲染控制台状态，轮询 loopback API，发起预览/启动/停止/校验请求 | 浏览器入口 |
| `src/dai-sync-controller.js` | 独占一个 CLI 子进程，按参数数组启动/停止，解析安全日志与摘要；状态绝不包含认证值 | `DaiSyncController`, `createDaiSyncController` |
| `scripts/launch-dashboard.mjs` | 仅探测 loopback `/api/sync/status` 判断新版服务；旧端口占用时选择下一个空闲端口，并把实际 URL 交给应用窗口 | `chooseDashboardPort`, `launchDashboard` |
| `src/dai-sync-core.js` | 严格一对一同名匹配、重复/缺失/无效值拦截、`上游 + adjustment` 差异计划（默认 adjustment 为 0.02） | `createDaiSyncPlan`、`summarizeDaiSyncPlan` |
| `src/dai-rest-adapter.js` | 将配置化 GET/PATCH 映射为 REST 或 browser 会话适配器；请求体模板和字段路径可覆写 | `normalizeDaiSyncConfig`、`createDaiRestAdapter` |
| `src/dai-browser-session.js` | 在受控 Chrome 隔离上下文中手动登录、保存会话并执行同源 JSON 请求；后台可加载 `browser.requestPageUrl` 让真实业务前端产生并复用同源认证请求头 | `DaiBrowserSession`、`createDaiBrowserSession` |
| `src/dai-lifecycle-core.js` | 以精确名称/已保存目标匹配供应商与分组，生成已有分组倍率更新、新分组创建、关联目标、逐模型规则、精确匹配的恢复和失效计划 | `createDaiLifecyclePlan` |
| `src/dai-sync-runner.js` | 获取两端列表、倍率/生命周期 dry-run、显式 apply、顺序写入、错误停止和摘要 | `runDaiSyncCycle`、`runDaiLifecycleCycle` |
| `bin/dai-group-sync.mjs` | 读取外部配置、环境变量头引用、`--once`/`--watch` CLI | `main` |

## 调用关系

`pricing.html` -> `src/app.js` -> `src/pricing.js`；`src/app.js` 将结果导出为下载文件或剪贴板文本。

同步控制台调用链为 `index.html` -> `src/sync-console-app.js` -> 同源 `server.mjs` `/api/sync/*`（含 browser 登录/保存会话 API）-> `src/dai-sync-controller.js` -> 独立子进程 `bin/dai-group-sync.mjs`。`/api/sync/status` 带有固定 `service`/`apiVersion` 标记，供启动器区分新版控制台和旧占用者。控制台 API 和服务器都只监听/服务 loopback；浏览器只接收无敏感值的状态、解析后的差异/跳过记录和已脱敏日志。`server.mjs` 构造或静态页面载入不会启动 CLI 或发起远程请求。

子进程同步调用链为 `bin/dai-group-sync.mjs` -> `src/dai-rest-adapter.js`（REST 环境变量请求或保存的 browser 会话）-> `src/dai-sync-runner.js` -> `src/dai-sync-core.js` / `src/dai-lifecycle-core.js`。browser 模式由受控 Chrome 隔离上下文打开真实登录页，storage state 仅写入项目 `runtime/` 下的忽略文件；一次 CLI 运行期间复用一个 headless request page，结束时关闭。生命周期读取还会获取每个分组的 targets 和 dispatch-rules；生命周期模式仍可更新已有分组倍率，但只对新分组创建目标/规则，精确匹配的已有分组可按独立安全门恢复或停用；分组状态写入使用前端同款 `PATCH /groups/{id}/status`，并使用 `disabled` 停用、`active` 恢复，停用不作用于调度规则。只有 GUI 显式选择 apply、CLI `--apply` 与配置安全门全部满足时，runner 才调用写接口；GET 失败会在任何写入前终止本轮，写入失败会停止后续写入且不回滚已完成动作。

## 维护最小上下文

倍率规则变更时读取 `src/pricing.js` 与 `tests/pricing.test.mjs`；手动界面交互变更时再读取 `src/app.js` 和 `pricing.html`；同步控制台变更时读取 `src/sync-console-app.js`、`src/dai-sync-controller.js`、`server.mjs` 和 `tests/dashboard.test.mjs`。导出格式变更时同时更新 README 和对应测试。

## 扩大范围条件

远程同步是有意增加的独立运行时边界。项目已通过自身隔离 browser-session 对已登录业务页执行只读 GET，核实上游资源、分组、目标和调度规则的字段与路径；没有发送真实创建、关联、规则或停用写请求。生命周期 POST/PATCH 的完整权限和最终 schema 仍需由有权限操作者确认；示例配置把 endpoint/body 显式化并默认关闭安全门，绝不复制浏览器 Cookie。
