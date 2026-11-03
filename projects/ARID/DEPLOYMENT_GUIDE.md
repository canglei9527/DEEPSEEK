# 📦 ARID 双模式打包与部署指南

## 🎯 目标

将当前电脑上的 ARID 双模式（含 dsh-vision-router）部署到其他电脑。

---

## ⚠️ 重要说明

### 现在点"一键安装"会装什么？

**取决于代码是否已提交到 Git！**

| 情况 | 其他电脑安装结果 |
|------|----------------|
| ✅ **已 git commit + push** | 新版（dsh-vision-router，11 个工具） |
| ❌ **未提交** | 旧版（arid-dualmodel-vision.cjs，1 个工具） |

**当前状态**: 你的修改还在本地，**未提交到 git**

---

## 📝 方案对比

### 方案 1️⃣: Git 同步（推荐）✅

**优点**:
- ✅ 自动同步最新代码
- ✅ 版本控制，可回滚
- ✅ 团队协作友好

**步骤**:

#### 在当前电脑（首次）

```bash
cd "E:\DEEPSEEK ai\ARID"

# 提交修改
git add .
git commit -m "feat: 升级到 dsh-vision-router (11 个视觉工具)

- 集成 dsh-vision-router 替代旧视觉方案
- 从 1 个工具升级到 11 个
- 添加 Node 版本自动检测
- GUI 显示视觉插件状态
- 保持向后兼容"

git push
```

#### 在其他电脑

```bash
# 方法 A: 首次部署（自动化）
# 双击运行：deploy-new-computer.bat
# 脚本会自动：
#   1. 检查 Node/Git
#   2. Clone 仓库
#   3. 安装 dsh
#   4. 安装 dsh-vision-router（Node ≥22）
#   5. 启动 GUI

# 方法 B: 手动部署
git clone <你的仓库地址>
cd ARID/tools/dual-model-gui
node server.mjs
# 浏览器打开 http://127.0.0.1:3090
# 点击"一键安装 Harness"
```

---

### 方案 2️⃣: 打包压缩文件（离线）💾

**优点**:
- ✅ 无需 Git
- ✅ 适合离线环境
- ✅ 一次打包，多次使用

**缺点**:
- ❌ 更新需要重新打包
- ❌ 文件较大（~50MB+）

**步骤**:

#### 在当前电脑

```bash
# 1. 进入父目录
cd "E:\DEEPSEEK ai"

# 2. 打包（使用 7-Zip / WinRAR / tar）

# 方法 A: 使用 PowerShell
Compress-Archive -Path "ARID" -DestinationPath "ARID-v2024-vision-router.zip"

# 方法 B: 使用 Git Bash
tar -czf ARID-v2024-vision-router.tar.gz ARID/

# 方法 C: 右键 ARID 文件夹 → 添加到压缩文件
```

#### 在其他电脑

```bash
# 1. 解压文件
# 解压到任意目录，如 C:\Projects\ARID

# 2. 进入目录
cd C:\Projects\ARID\tools\dual-model-gui

# 3. 启动 GUI
node server.mjs

# 4. 浏览器打开 http://127.0.0.1:3090

# 5. 点击"一键安装 Harness"
#    系统会自动安装：
#    - dsh（全局）
#    - dsh-vision-router（Node ≥22）
```

---

### 方案 3️⃣: 便携版（完全离线）📀

**适用场景**: 完全无网络环境

**步骤**:

1. **打包时** - 预先下载依赖：
```bash
# 在当前电脑
cd "E:\DEEPSEEK ai\ARID\tools\dual-model-gui"

# 下载 dsh 和 dsh-vision-router 的离线安装包
npm pack @deepseek-ai/dsh
npm pack dsh-vision-router

# 将生成的 .tgz 文件也打包到 ARID 目录
```

2. **部署时** - 离线安装：
```bash
# 在其他电脑（无网络）
cd ARID\tools\dual-model-gui

# 离线安装 dsh
npm install -g deepseek-ai-dsh-*.tgz

# 离线安装 dsh-vision-router
dsh plugin --profile web add dsh-vision-router-*.tgz
```

---

## 🚀 推荐流程

### 第一步：提交代码到 Git

```bash
cd "E:\DEEPSEEK ai\ARID"

# 查看当前修改
git status

# 添加所有修改
git add preset/agent.cordis.yml
git add tools/dual-model-gui/lib/
git add tools/dual-model-gui/index.html
git add tools/dual-model-gui/*.md
git add deploy-new-computer.bat

# 提交
git commit -m "feat: 升级到 dsh-vision-router (11 个视觉工具)"

# 推送到远程
git push
```

### 第二步：在其他电脑部署

**选择以下任一方式**:

#### 方式 A: 自动部署脚本 ⚡（最简单）

1. 将 `deploy-new-computer.bat` 发送给对方
2. 双击运行
3. 输入 Git 仓库地址
4. 等待自动完成

#### 方式 B: 手动 Git Clone 🔧

```bash
git clone <你的仓库地址> ARID
cd ARID/tools/dual-model-gui
node server.mjs
# 浏览器打开，点击"一键安装 Harness"
```

#### 方式 C: 打包传输 📦

```bash
# 压缩 ARID 文件夹
# 传输到其他电脑
# 解压后运行 node server.mjs
# 点击"一键安装 Harness"
```

---

## ✅ 验证部署成功

### 在其他电脑上

1. **启动 GUI**:
   ```bash
   cd ARID/tools/dual-model-gui
   node server.mjs
   ```

2. **打开浏览器**: http://127.0.0.1:3090

3. **查看"双模式体检"部分**:
   ```
   预设挂载: arid-dualmodel ✅ 已安装
   执行插件: ✅ 已安装 arid-dualmodel-ext6.cjs
   视觉插件: ✅ dsh-vision-router (11 个工具)  ← 看到这个就成功了！
   ```

4. **如果显示旧方案或未安装**，点击"一键安装 Harness"

---

## 🔍 故障排查

### 问题 1: 其他电脑显示旧版视觉方案

**原因**: Git 代码未同步

**解决**:
```bash
# 在当前电脑
git push

# 在其他电脑
git pull
# 重启 GUI
```

### 问题 2: 点击"一键安装"失败

**可能原因**:
- npm 网络问题
- Node 版本 < 22

**解决**:
```bash
# 检查 Node 版本
node --version

# 手动安装
npm install -g @deepseek-ai/dsh
dsh plugin --profile web add dsh-vision-router
```

### 问题 3: GUI 显示"未配置"

**原因**: 视觉插件未安装或未启动

**解决**:
- 点击"一键安装 Harness"
- 或手动运行 `dsh plugin --profile web add dsh-vision-router`

---

## 📊 总结

| 方案 | 适用场景 | 难度 | 推荐度 |
|------|---------|------|--------|
| **Git 同步** | 有网络，多台电脑 | ⭐ 简单 | ⭐⭐⭐⭐⭐ |
| **打包传输** | 无 Git，少量电脑 | ⭐⭐ 中等 | ⭐⭐⭐ |
| **便携版** | 完全离线 | ⭐⭐⭐ 复杂 | ⭐⭐ |
| **自动脚本** | 新手部署 | ⭐ 最简单 | ⭐⭐⭐⭐ |

**推荐**: Git + 自动部署脚本（`deploy-new-computer.bat`）
