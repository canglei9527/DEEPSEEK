# 项目 AI 维护规则

## 读取边界

- 项目根目录：当前目录 `upstream-price-adjuster`。
- 先读取本文件和 `ai-context/` 下的架构、函数索引、Bug 历史，再按目标符号打开源码。
- 默认只读取目标模块、一跳项目依赖和最小相关测试；不递归展开依赖或扫描父级工作区。
- 排除 `node_modules`、构建产物、缓存、凭据和生成文件。

## 修改范围

- 价格规则的唯一实现位于 `src/pricing.js`；界面只负责输入、展示和导出。
- 默认规则是“调整后倍率 = 上游倍率 + 调整值”，默认调整值为 `0.02`。
- 保持浏览器端无第三方依赖；不要为了视觉效果引入框架或网络资源。
- 不覆盖、回滚或删除用户已有修改。
- 浏览器界面、构建、静态检查、单元测试和本地 mock 验证不得向远程服务发送请求。
- `server.mjs` 是本地桌面控制台边界：只绑定 `127.0.0.1`，静态资源白名单和 `/api/sync/*` 控制 API 均不提供 CORS。`src/dai-sync-controller.js` 独占同步子进程生命周期，通过 `spawn` 参数数组调用 `bin/dai-group-sync.mjs`，禁止 shell 拼接；状态接口不得返回认证值。
- `index.html` 是同步控制台入口；原手动调价页面位于 `pricing.html`。`launch-dashboard.cmd` 调用 `scripts/launch-dashboard.mjs`，先验证 4173 是否为新版 API，旧服务占用时选择后续空闲端口并打开实际 URL；不得终止未知进程。
- `bin/dai-group-sync.mjs` 是独立的运行时边界：默认只读 dry-run；倍率 apply 需要配置显式 `allowApply: true` 且命令行显式传入 `--apply`；生命周期创建还需要 `allowLifecycleApply: true`；失效分组停用还需要 `allowDisableStale: true` 与 `--disable-stale`；恢复已识别资源还需要 `allowEnableRestored: true` 与 `--enable-restored`。生命周期模式仍会实时更新已有分组倍率，但不会为已有分组补目标或规则；失效处理只停用精确匹配的分组，不停用规则。凭据只能通过环境变量引用传入，禁止读取/保存/打印 Cookie、密码或令牌。

## 验证

- 语法检查：`npm run check`
- 核心测试：`npm test`
- 手动界面检查：`npm start` 后打开 `http://127.0.0.1:4173/`
