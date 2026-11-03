# 上游倍率自动调价器

无第三方依赖的 Node.js 18 项目，包含三个彼此独立的本地工具：

- 桌面式 D-AI 同名分组同步控制台：本机浏览器窗口控制现有安全 CLI 的一次预览、dry-run 监视器和受配置/CLI 双重安全门保护的 apply 监视器。
- 网页调价器：录入上游倍率后，计算 `调整后倍率 = 上游倍率 + 调整值`，位于 `/pricing.html`。
- D-AI 同名模型分组同步器：轮询上游资源和模型分组，并使目标分组的默认倍率等于同名上游资源倍率 `+ 调整值`，默认调整值为 `0.02`。

同步器严格按原始名称一对一匹配：不 trim、不忽略大小写、不做模糊匹配。因此 `model-a` 与 `model-a ` 被视为不同名称。

## 桌面式同步控制台（推荐日常使用）

要求 Node.js 18 或更高版本：

```bash
# 首次使用：创建只属于本机的配置文件
Copy-Item .\dai-sync.config.example.json .\dai-sync.config.json
```

双击项目根目录的 [launch-dashboard.cmd](launch-dashboard.cmd)，它会先探测 `127.0.0.1:4173`：如果那里已经是新版控制台就直接复用；如果是旧服务或其他占用者，则自动选择下一个空闲端口，不会终止未知进程。随后它会用实际端口尝试以 Edge/Chrome 的应用窗口打开控制台。也可以在终端运行：

```bash
npm run dashboard:open
```

需要完全重启并加载最新代码时，双击项目根目录的 [restart-dashboard.cmd](restart-dashboard.cmd)。它只会停止返回本项目 dashboard 标识、且命令确认为 Node `server.mjs` 的本机服务，然后调用原启动器重新打开控制台；其他项目或未知端口不会被终止。也可以运行：

```powershell
npm run dashboard:restart
```



控制台的日常操作顺序：

1. 在“本地配置”填写私有 JSON 文件名（默认 `dai-sync.config.json`）并点击“校验配置”。页面只显示环境变量名称及其是否已设置，绝不显示认证值。
2. 点击“预览一次（dry-run）”，检查“倍率不一致”“跳过与不匹配”和进程日志。这个按钮固定不会传 `--apply`，因此不会 PATCH。
3. 确认预览正常后，点击“启动 dry-run 监视器”。它在独立本机子进程中运行已有 CLI；“停止”会先请求安全退出，必要时才强制终止子进程。
4. 使用 browser 模式时，点击“打开 D-AI 登录”，在打开的隔离 Chrome 窗口中手动完成账号密码、验证码或二次验证；返回控制台点击“保存登录会话”。
5. 会话已保存且私有配置中 `allowApply` 为 `true` 后，选择 `apply` 并启动监视器，控制台就会把 `--apply` 传给 CLI。

控制台不会把令牌、Cookie 或密码显示在状态和日志中。browser 模式只在受控页面上下文内复用已登录页面产生的同源认证状态与请求头，不把其内容返回 Node 或控制台；REST 模式仍只能使用配置引用的本机环境变量。

## 手动网页调价器

`npm start` 现在同样启动同步控制台。启动后在控制台侧边栏点击“手动调价器”，或直接打开 <http://127.0.0.1:4173/pricing.html>。该页面仅在浏览器本地计算和保存录入数据，不参与同步进程。

## D-AI 同步器：安全模型

同步器默认只运行一轮 dry-run，绝不调用更新 API。CLI 真正写入需要两个独立的显式开关：

1. 配置文件中设置 `"allowApply": true`。
2. 命令行传入 `--apply`。

控制台在操作者显式选择 `apply` 后传递 `--apply`；配置仍必须显式设置 `allowApply: true`。没有同时满足 CLI 双重安全门时，程序只会读取并打印差异。读取/计划错误时，本轮不会更新；写入时采用顺序执行，第一条 PATCH 出错后立即停止后续写入。

### 供应商模型生命周期同步

在“同步类型”选择“供应商模型生命周期同步”后，CLI 使用 `--lifecycle` 执行生命周期计划。它仍然会实时检查并更新已有同名分组的倍率；“已有分组不动”只表示生命周期不会补充已有分组缺失的上游目标或调度规则。新供应商才会进入创建链，生命周期 dry-run 会读取上游资源、分组、分组目标和调度规则，并按以下顺序预览：

```text
已有分组倍率有差异 -> 更新已有分组倍率
缺少同名分组 -> 创建分组 -> 关联上游目标 -> 逐个创建模型规则
已识别资源恢复 -> 恢复分组/规则
上游模型已消失 -> 不停用规则
供应商资源已消失 -> 仅列出精确匹配分组的待停用计划
```

生命周期 apply 必须同时满足：

1. `allowApply: true`；
2. `allowLifecycleApply: true`；
3. CLI/控制台显式选择 apply 和生命周期模式。

停用失效分组还需要单独打开 `allowDisableStale: true` 并传入 `--disable-stale`。该开关只会停用确认失效的分组，不会停用调度规则。恢复已停用的已识别资源同样是独立危险动作，需要 `allowEnableRestored: true` 并传入 `--enable-restored`；dry-run 可以预览恢复项，但不会发送恢复请求。停用/恢复只处理与上游供应商精确匹配的已有分组；恢复规则仅在模型精确恢复时启用，不会停用规则，不要求描述带有 `[dai-lifecycle] ` 标识；该标识仅用于新分组的创建描述。人工分组及其中规则不会补充缺失目标或规则。没有可复制的 `retail_price_book_id`、无效上游倍率、重复资源或模型列表缺失时，不生成创建动作。

生命周期写入严格串行：创建分组返回的 ID 会传给关联和规则请求；任一写入失败后停止剩余动作，不回滚已经成功的远程操作，下一轮 dry-run 用于重新对账。

```bash
# 生命周期只读预览
npm run sync -- --config .\\dai-sync.config.json --lifecycle --dry-run

# 只有完成预览并明确打开两个生命周期配置门后才考虑创建/关联/规则写入
npm run sync -- --config .\\dai-sync.config.json --lifecycle --apply

# 恢复已识别资源（必须先完成 dry-run，并明确打开独立配置门）
npm run sync -- --config .\\dai-sync.config.json --lifecycle --apply --enable-restored

# 失效停用是额外危险动作，默认不要打开
npm run sync -- --config .\\dai-sync.config.json --lifecycle --apply --disable-stale
```

```bash
# 显示帮助；不读取配置、不访问网络
npm run sync -- --help

# 一轮预览（默认 --once --dry-run）
npm run sync -- --config .\dai-sync.config.json

# 每 60 秒持续预览；也可用 --interval-ms 30000 覆盖
npm run sync -- --config .\dai-sync.config.json --watch

# 只有完成预览并将 allowApply 改为 true 后才会写入
npm run sync -- --config .\dai-sync.config.json --apply --watch
```

每轮会输出待更新项、重复名/缺失名/无效值的跳过记录，以及上游数、分组数、匹配数、一致数、待更新数和跳过数摘要。

## 配置和凭据

先将 [dai-sync.config.example.json](dai-sync.config.example.json) 复制为本机私有配置，例如 `dai-sync.config.json`。示例不含真实凭据，也保持 `allowApply: false`。

```powershell
Copy-Item .\dai-sync.config.example.json .\dai-sync.config.json
```

认证有两种模式：

- 推荐使用 browser 模式：在配置中设置 `browser.loginUrl` 和已登录业务页 `browser.requestPageUrl`，控制台点击“打开 D-AI 登录”，在隔离 Chrome 窗口中完成登录，再点击“保存登录会话”。会话只保存到项目 `runtime/`，不会显示 Cookie 或 Token；之后先 dry-run，再选择 `apply` 启动监视器。
- 仍可使用 REST Token 模式：请求头通过环境变量引用，例如 `"Authorization": { "env": "DAI_SYNC_AUTHORIZATION" }`。

browser 模式示例：

```json
"headers": {},
"browser": {
  "loginUrl": "https://miao.313619.xyz",
  "requestPageUrl": "https://miao.313619.xyz/tenant/ai/models/upstreams",
  "storagePath": "runtime/dai-browser-storage.json"
}
```


`baseUrl` 必须是不含路径、查询参数、片段或用户名/密码的服务源地址；非本机地址必须使用 `https`。明文 `http` 仅用于 `127.0.0.1`/`localhost`/`[::1]` 本地 mock。

可配置项：

| 配置项 | 默认值 | 含义 |
|---|---:|---|
| `intervalMs` | `60000` | `--watch` 的轮询间隔，最小 1000ms |
| `decimals` | `4` | `上游 + 调整值` 的目标倍率取整精度 |
| `adjustment` | `0.02` | 目标倍率相对上游倍率增加的值；控制台可选择 `0.001`、`0.005`、`0.01`、`0.02` 或 `0.05` |
| `tolerance` | `0.000000001` | 当前倍率和目标倍率的最大允许差；不超过时不更新 |
| `browser.requestPageUrl` | `browser.loginUrl` | 登录后用于加载真实业务前端的同源页面；页面产生的同源认证请求头仅在浏览器上下文内复用 |
| `allowLifecycleApply` | `false` | 生命周期创建分组、关联上游和创建规则的独立安全门 |
| `allowDisableStale` | `false` | 生命周期停用失效分组的独立安全门；不会停用规则；默认关闭 |
| `allowEnableRestored` | `false` | 生命周期恢复已识别规则/分组的独立安全门；默认关闭 |
| `endpoints.*.itemsPath` | `items` | 列表在 JSON 响应中的数组路径 |
| `endpoints.*.fields` | 见示例 | 上游/分组的 ID、名称、倍率字段映射 |
| `endpoints.updateGroup.bodyTemplate` | 见示例 | PATCH 请求体模板；完整变量会保留数字和布尔类型 |

模板可使用 `{{group.字段}}`、`{{upstream.字段}}` 和 `{{desiredMultiplier}}`。缺失字段会从请求体中省略，而不是写成字符串 `"undefined"`。

## D-AI API 的已知边界

本实现已通过项目自身的隔离 browser-session 对已登录业务页执行只读 GET 核验，并只读提取到 D-AI 分组状态前端契约：`PATCH /api/v1/tenants/me/groups/{id}/status`，请求体为 `{ "status": "disabled" }` 或 `{ "status": "active" }`。没有执行新的真实创建、关联、规则、停用或恢复请求。

已核验的只读接口包括：

| 用途 | 路径/字段 |
|---|---|
| 上游资源 | `GET /api/v1/tenants/me/upstream-resources`，`items[*].id/name/tenant_multiplier/models[*]` |
| 模型分组 | `GET /api/v1/tenants/me/groups`，`items[*].id/name/description/default_user_multiplier/retail_price_book_id` |
| 上游目标 | `GET /api/v1/tenants/me/groups/{id}/targets`，目标使用 `account_id`、`priority`、`status` |
| 调度规则 | `GET /api/v1/tenants/me/groups/{id}/dispatch-rules`，规则使用 `client_surface`、`match_type`、`match_value`、`target_model_code`、`required_capability` |

真实 browser-session 现在在一次 CLI 运行期间复用一个隔离的 headless context/page，运行结束后关闭；不会复用用户实际 Chrome profile，也不会把 Cookie、Authorization 或 storage 内容返回 Node、控制台、测试或日志。


这不是对实时响应、认证机制、权限或 PATCH 完整 schema 的验证。实际启用前，请由有权限的操作者在官方 API 文档或自己的已登录浏览器 DevTools Network 中确认：

1. 上述两个 GET 的真实响应仍为示例配置的 `items` 数组和字段名；否则只修改私有配置的 `itemsPath` / `fields`，先 dry-run。
2. 更新分组请求的真实方法、路径和完整 body；若与示例不同，只修改 `endpoints.updateGroup` 的 `method`、`path`、`bodyTemplate`，再 dry-run。
3. 是否存在专用于自动化的、最小权限、可轮换的 API/服务令牌，以及应使用的请求头名称。把令牌设置为环境变量，不要复制 Cookie。
4. 预览结果只包含预期分组、没有重复名/缺失名/无效值后，才将 `allowApply` 和 `--apply` 同时打开。

示例中的路径与字段是可覆写的适配器默认值，而不是伪造的已验证生产连接。

## 同步规则

对每个同时存在且名称完全相同的上游资源/分组：

```text
目标分组默认倍率 = round(上游资源 tenant_multiplier + adjustment, decimals)
```

- 当前分组倍率在 `tolerance` 内时不更新。
- 任一侧重复名、任一侧缺失、空名称、无效/负数倍率、或分组缺少 ID 时，该名称不写入，并在每轮输出跳过原因。
- 列表读取失败时，本轮不会进行任何更新。
- 一次 PATCH 失败后，停止本轮剩余写入，避免配置/认证错误扩散。

## Windows EXE 打包

在 Windows + Node.js 24 环境构建单文件 EXE：

```bash
npm install
npm run build:exe
```

产物位于 `dist/upstream-price-adjuster.exe`。将 `dist/` 整个目录复制到目标 Windows 机器，复制 `dai-sync.config.example.json` 为 `dai-sync.config.json` 后填写配置，再双击 EXE。Node.js 不需要安装；本机需要 Google Chrome 或 Microsoft Edge 才能使用 browser 登录会话。`runtime/` 用于保存本机浏览器会话，绝不会打进 EXE 或提交到 Git。

EXE 只绑定 `127.0.0.1`，默认 dry-run；真实 apply、生命周期创建、分组停用和恢复仍需要现有独立安全门。
```bash
npm run check
npm test
npm run sync -- --help
npm run sync:mock
```

`sync:mock` 启动临时 `127.0.0.1` HTTP mock，以同一个 CLI 执行一次 dry-run，断言只收到两个 GET 而没有 PATCH；它不访问 D-AI。

`npm test` 还包含控制台的假子进程和本地 HTTP 测试：验证重复启动会被拦截、dry-run 只传 `--dry-run`、apply 缺少 `allowApply` 不能启动，以及仪表盘能在 loopback 载入。测试不会读取真实认证信息，也不会访问 D-AI。

## 原网页工具使用

- 在表格中逐条填写模型名和上游倍率，结果会随输入即时更新。
- “批量粘贴”支持 `模型名<TAB>倍率`、`模型名,倍率` 以及 `模型名 ×倍率` 格式。
- 可调整加价值和小数位数；数据只保存在当前浏览器的本地存储中。
- 结果可复制，或导出为 JSON/CSV，便于录入模型分组或价格表。
