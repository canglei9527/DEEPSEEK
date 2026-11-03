# Bug 历史

## 初始记录

- 创建日期：2026-08-20
- 技术路线：原生 HTML/CSS/ES modules + Node.js 内置静态服务器

## 2026-08-21：浏览器页面已登录但同步 API 返回 401

- 现象：隔离 Chrome 页面可显示已登录的 D-AI 上游页面，但后台 `/api/v1/tenants/me/*` GET 持续返回 HTTP 401；apply 因 GET 失败按安全规则停止，未发送 PATCH。
- 原因：仅使用根登录页的原生 `fetch` 无法复现真实业务前端的 Axios/XHR 拦截器、CSRF/租户请求头和页面初始化。
- 修改方式：增加 `browser.requestPageUrl`，后台加载真实已登录业务页；在页面上下文内捕获同源 fetch/XHR 请求头并复用，同时恢复 sessionStorage 和候选 Authorization。认证内容不返回 Node、控制台或日志。
- 未验证风险：真实 D-AI 业务页的请求头捕获和实时接口响应仍需用户重新保存会话后 dry-run 验证；在 dry-run 成功前不应启用 apply。
## 2026-08-21：同步倍率固定为 0.02，无法按控制台选择

- 现象：同步器目标倍率始终按上游倍率加 `0.02` 计算，控制台不能选择其他调整值。
- 原因：调整值只存在于核心常量，未贯穿配置、CLI、控制器和控制台请求链路。
- 修改方式：新增 `adjustment` 配置和 `--adjustment` CLI 覆盖；控制台提供 `0.001`、`0.005`、`0.01`、`0.02`、`0.05` 下拉选项，默认保持 `0.02`；增加核心、CLI 和 dashboard 回归测试。
- 验证：自定义 `0.005` 时 `0.2` 计算为 `0.205`；默认行为仍为 `0.22`。


- 原因：原同步器只支持环境变量请求头；控制台没有登录入口，也没有复用浏览器会话的路径。
- 修改方式：接入受控 Playwright Chrome 会话。用户在隔离登录窗口完成登录后，控制台把 storage state 保存到 `runtime/`，同步 CLI 以 headless Chrome 同源请求读取 upstream/groups 并在双重 apply 开关满足时发送 PATCH；不读取现有 Chrome profile，不展示或记录 Cookie/密码/Token。
- 未验证风险：真实 D-AI 登录页、验证码/二次验证流程、实时响应字段和 PATCH schema 尚未由本地测试访问；需用户先手动登录并运行 dry-run 检查日志，再自行启动 apply。

## 2026-08-21：分组状态子路径与状态枚举返回 HTTP 422

- 现象：生命周期 `disable-group` 使用 `/status` 子路径并提交 `inactive` 时返回 HTTP 422；改为完整分组 PATCH 但仍使用 `inactive` 也返回 HTTP 422。
- 原因：分组管理前端的实际状态切换调用 `updateGroupStatus(id, "disabled")`，服务端停用枚举为 `disabled`，不是 `inactive`。
- 修改方式：分组停用与恢复调用前端同款 `PATCH /groups/{id}/status`，请求体分别为 `status: "disabled"` 和 `status: "active"`；生命周期恢复判断同步识别 `disabled`。保留独立 `allowDisableStale` / `allowEnableRestored` 安全门，规则不生成停用动作。
- 验证：只读提取当前已登录前端的 API 客户端和分组状态处理器；本地 adapter 合同测试断言 URL、请求体和状态枚举；未执行新的真实停用或恢复请求。


- 现象：生命周期模式只处理新分组创建时，已有分组的实时倍率更新被遗漏；停用后的分组在上游恢复后也不会重新启用。
- 原因：生命周期计划把已有分组当作完全只读资源，且没有对 `disabled` 资源生成对称的 active 状态动作。
- 修改方式：已有匹配分组继续生成原有倍率 `updateGroup` 计划；已有分组仍不补目标或规则。新增精确匹配的 `enable-group`/`enable-rule` 计划、`allowEnableRestored` 配置门和 `--enable-restored` CLI 开关；人工与生命周期分组均可参与精确恢复，disabled stale 资源不重复停用。
- 验证：新增生命周期核心、runner、REST adapter、CLI 和 dashboard 回归测试；未执行真实恢复或其它生命周期写请求。



- 现象：同步器原本只能按同名分组调倍率；生命周期读取每个分组时会重复启动 headless Chrome context/page，真实 dry-run 在输出完成后还可能因浏览器连接未关闭而迟迟不退出；失效规则也可能误伤人工分组。
- 修改方式：增加纯本地生命周期计划，按上游目标或精确名称匹配分组，依次生成创建分组、关联上游目标和逐模型 exact 规则；浏览器 session 在一次 CLI 运行内复用隔离 request page，adapter/CLI 结束时关闭。新增 `allowLifecycleApply`、`allowDisableStale` 和 `--lifecycle`/`--disable-stale` 独立安全门。
- 保护方式：停用只针对与上游供应商精确匹配的已有分组，不停用调度规则；恢复可针对精确匹配的分组和规则。`[dai-lifecycle]` 只用于新分组描述。
- 验证：browser fake Playwright、生命周期计划/runner、dashboard lifecycle 状态解析和本地 mock 均通过；真实环境只执行过认证 GET dry-run，没有发送创建、关联、规则或停用请求。
