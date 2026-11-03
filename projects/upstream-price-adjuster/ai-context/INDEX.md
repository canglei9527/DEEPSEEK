# 项目上下文索引

## 根目录职责

| 路径 | 职责 | 入口/验证 |
|---|---|---|
| `index.html` / `sync-console.css` | 本地 D-AI 同步控制台界面，含倍率与供应商生命周期模式 | `tests/dashboard.test.mjs`；浏览器手动检查 |
| `pricing.html` / `styles.css` | 原手动倍率计算器 | `tests/pricing.test.mjs` |
| `server.mjs` | 仅 loopback 的静态资源与 `/api/sync/*` 控制 API | `tests/dashboard.test.mjs` |
| `src/dai-sync-core.js` | 同名倍率计划与校验 | `tests/dai-sync.test.mjs` |
| `src/dai-lifecycle-core.js` | 生命周期纯本地计划：已有分组倍率更新、新分组、上游目标、逐模型规则、精确匹配资源的停用与恢复 | `tests/dai-sync.test.mjs` |
| `src/dai-rest-adapter.js` | 配置化 REST/browser GET 与受安全门保护的 PATCH/POST | `tests/dai-sync.test.mjs` |
| `src/dai-browser-session.js` | 隔离 Chrome 登录、保存会话、同源请求和单次运行 page 复用 | `tests/dai-browser-session.test.mjs` |
| `src/dai-sync-runner.js` | 倍率/生命周期读计划、dry-run/apply 顺序执行和摘要 | `tests/dai-sync.test.mjs` |
| `src/dai-sync-controller.js` | dashboard 子进程、生命周期状态解析和安全启动门 | `tests/dashboard.test.mjs` |
| `src/sync-console-app.js` | 控制台状态、生命周期动作列表和本地 API 调用 | `tests/dashboard.test.mjs`；浏览器手动检查 |
| `bin/dai-group-sync.mjs` | CLI：`--lifecycle`、`--disable-stale`、`--enable-restored`、dry-run 默认 | `npm run check`；`tests/dai-sync.test.mjs` |
| `tests/` | 核心、browser session、dashboard 和手动调价回归测试 | `npm test` |
| `ai-context/` | 架构、函数、Bug 和本索引 | 维护变更时更新 |

## 安全边界

- 默认 dry-run；倍率 apply 需要 `allowApply: true` + `--apply`。
- 生命周期创建额外需要 `allowLifecycleApply: true` + `--lifecycle --apply`。
- 失效停用额外需要 `allowDisableStale: true` + `--disable-stale`，仅处理与上游供应商精确匹配的已有分组，不停用规则。
- 生命周期恢复额外需要 `allowEnableRestored: true` + `--enable-restored`，仅处理 `disabled` 且精确匹配恢复对象的已有分组/规则；`[dai-lifecycle] ` 仅用于新分组描述。
- browser session 不复用用户 Chrome profile，不向 Node、控制台、测试或日志暴露认证内容。
- 本地测试和静态检查不向真实 D-AI 发请求；真实环境本轮只做过认证 GET dry-run。
