# 🧪 一键安装测试指南

## 目标
测试 GUI 的"一键安装 Harness"功能是否正确安装 dsh-vision-router。

---

## 前置条件

- ✅ Node v24.11.1（支持 dsh-vision-router）
- ✅ 已更新的 GUI 代码
- ✅ 已修改的 preset/agent.cordis.yml

---

## 测试步骤

### 1️⃣ 启动 GUI

```bash
cd "E:\DEEPSEEK ai\ARID\tools\dual-model-gui"
node server.mjs
```

应该看到：
```
DSH_GUI_PORT=3090
[ARID 双模式管理台] http://127.0.0.1:3090
```

---

### 2️⃣ 打开浏览器

访问: http://127.0.0.1:3090

---

### 3️⃣ 查看"双模式体检"部分

你应该看到：

```
双模式体检 [只读概览]

预设挂载:    arid-dualmodel ✅ 已安装
执行插件:    ✅ 已安装 arid-dualmodel-ext6.cjs
视觉插件:    ⚠ 未安装  或  ✅ arid-dualmodel-vision (1 个工具（旧方案）)
规划模型:    ... 
执行模型:    ...
视觉模型:    ...
```

**关键新增**: "视觉插件" 这一行会显示：
- ✅ dsh-vision-router（11 个工具） - 如果已安装
- ✅ arid-dualmodel-vision（1 个工具（旧方案）） - 如果是旧方案
- ⚠ 未安装 - 如果都没有

---

### 4️⃣ 点击"一键安装 Harness"按钮

找到页面上的按钮：
```
[一键安装 Harness]
```

点击后，应该看到安装进度：
```
正在启动安装…
正在通过 npm 安装 DeepSeek Harness（dsh）…
正在安装 dsh-vision-router（11 个视觉工具）…
dsh-vision-router 安装成功！
正在导入双模式预设…
安装完成：dsh 已就绪，双模式预设已导入。
```

---

### 5️⃣ 验证安装结果

#### 方法 1: 刷新页面

刷新浏览器，查看"双模式体检"部分：
```
视觉插件: ✅ dsh-vision-router (11 个工具)
```

#### 方法 2: 命令行验证

```bash
dsh plugin list --profile web | grep vision-router
```

应该看到：
```
dsh-vision-router@1.2.3
```

---

### 6️⃣ 测试视觉功能

#### 启动 DSH Web 工作台

在 GUI 中点击"启动 DSH Web"（如果有）或手动启动：

```bash
dsh web
```

#### 发送图片消息

在工作台中：
1. 上传一张图片
2. 发送给 AI
3. 查看 AI 的工具调用

你应该看到 AI 使用了：
- `vision_describe` - 描述图片
- `vision_ground` - 定位对象
- 或其他 11 个视觉工具之一

---

## 预期结果

### ✅ 成功标志

- [ ] GUI 显示"视觉插件: ✅ dsh-vision-router (11 个工具)"
- [ ] `dsh plugin list --profile web` 包含 dsh-vision-router
- [ ] AI 可以使用 vision_describe 等 11 个工具
- [ ] 图片轮自动切换到视觉模型

### ❌ 失败场景

**场景 1: Node < 22**
- 显示: "视觉插件: ✅ arid-dualmodel-vision (1 个工具（旧方案）) Node v20.x.x < 22"
- 提示: 建议升级 Node

**场景 2: 安装失败**
- 显示: "安装 dsh-vision-router 失败，将使用旧版方案。"
- 降级到 arid-dualmodel-vision.cjs

---

## 多台电脑使用

### 问题: "我又不止这一台电脑"

**解决方案**: 每台电脑都需要运行一次"一键安装 Harness"

#### 为什么？

1. **dsh 是全局安装的**:
   ```bash
   npm install -g @deepseek-ai/dsh
   ```
   每台电脑需要独立安装。

2. **dsh-vision-router 是 DSH 插件**:
   ```bash
   dsh plugin --profile web add dsh-vision-router
   ```
   插件安装在 DSH 的全局目录，不会随代码同步。

3. **预设文件会同步**:
   - `preset/agent.cordis.yml` - 已修改，git 同步
   - `preset/arid-dualmodel-vision.cjs` - 旧方案后备，git 同步

#### 推荐流程（新电脑）

```bash
# 1. 克隆仓库
git clone <your-repo>
cd ARID

# 2. 启动 GUI
cd tools/dual-model-gui
node server.mjs

# 3. 浏览器打开 http://127.0.0.1:3090

# 4. 点击"一键安装 Harness"
#    系统会自动：
#    ✓ 安装 dsh
#    ✓ 安装 dsh-vision-router（如果 Node ≥22）
#    ✓ 导入预设

# 5. 完成！
```

---

## 常见问题

### Q: 为什么不把 dsh-vision-router 打包进仓库？

**A**: dsh-vision-router 是一个 npm 包（~10MB + 依赖），且需要全局安装。"一键安装"按钮会自动处理。

### Q: 可以手动安装吗？

**A**: 可以！
```bash
# 安装 dsh
npm install -g @deepseek-ai/dsh

# 安装 dsh-vision-router
dsh plugin --profile web add dsh-vision-router

# 导入预设
cd ARID
./install.bat  # 或 ./install.ps1
```

### Q: 如果 Node < 22 怎么办？

**A**: 系统会自动降级到 `arid-dualmodel-vision.cjs`（1 个工具）。建议升级 Node 到 22+ 以获得 11 个工具。

---

## 调试命令

```bash
# 检查 Node 版本
node --version

# 检查 dsh 是否安装
dsh --version

# 检查插件列表
dsh plugin list --profile web

# 检查预设状态
dsh profile list

# 查看 GUI 日志
# 在启动 GUI 的终端窗口查看输出
```

---

## 下一步

✅ 测试完成后，你就可以在任何电脑上：
1. Clone 仓库
2. 点击"一键安装"
3. 立即使用 11 个视觉工具

不需要手动配置！🎉
