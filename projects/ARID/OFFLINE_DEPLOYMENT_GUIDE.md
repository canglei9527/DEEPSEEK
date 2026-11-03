# 📦 ARID 双模式离线安装包制作与使用指南

## 🎯 适用场景

- ✅ 完全无网络的环境
- ✅ 内网隔离环境
- ✅ 网络限制的企业环境
- ✅ 需要批量部署多台电脑

---

## 📋 目录

1. [制作离线安装包](#制作离线安装包)
2. [使用离线安装包](#使用离线安装包)
3. [故障排查](#故障排查)
4. [包大小说明](#包大小说明)

---

## 制作离线安装包

### 前置条件

**在有网络的电脑上**：
- ✅ 已安装 Node.js
- ✅ 已安装 Git
- ✅ 网络连接正常
- ✅ 已有 ARID 仓库

### 步骤

#### 1️⃣ 运行打包脚本

```bash
cd "E:\DEEPSEEK ai\ARID"

# 双击运行或命令行执行
pack-offline.bat
```

#### 2️⃣ 等待打包完成

脚本会自动：
- ✅ 复制 ARID 完整代码
- ✅ 下载 `@deepseek-ai/dsh` 离线包
- ✅ 下载 `dsh-vision-router` 离线包
- ✅ 下载所有依赖包（sharp, potrace 等）
- ✅ 生成离线安装脚本
- ✅ 自动压缩为 ZIP 文件

**预计时间**: 3-5 分钟（取决于网络速度）

#### 3️⃣ 获取打包文件

打包完成后会生成：

```
ARID-Offline-20260816.zip  （推荐传输此文件）

或

ARID-Offline-Package/
├── ARID/                  （完整代码）
├── npm-packages/          （离线 npm 包）
│   ├── deepseek-ai-dsh-x.x.x.tgz
│   ├── dsh-vision-router-x.x.x.tgz
│   ├── sharp-x.x.x.tgz
│   └── ...
├── install-offline.bat    （离线安装脚本）
└── README.md             （说明文档）
```

**包大小**: 约 50-100MB（取决于依赖数量）

---

## 使用离线安装包

### 前置条件

**在目标电脑上**：
- ✅ 已安装 Node.js（推荐 22+）
- ✅ Windows 系统
- ❌ 不需要网络连接

### 步骤

#### 1️⃣ 传输文件

将 `ARID-Offline-20260816.zip` 通过以下任一方式传输到目标电脑：
- 💾 U盘
- 📀 光盘
- 🔗 内网共享
- 📧 邮件（如果允许）

#### 2️⃣ 解压文件

```bash
# 解压到任意目录，如：
C:\Projects\ARID-Offline-Package\
```

#### 3️⃣ 运行安装脚本

```bash
# 进入解压目录
cd C:\Projects\ARID-Offline-Package

# 双击运行或命令行执行
install-offline.bat
```

#### 4️⃣ 安装过程

脚本会自动：

```
[1/5] 检查 Node.js...
✅ Node 版本: v24.11.1

[2/5] 安装 DeepSeek Harness (dsh)...
正在安装: deepseek-ai-dsh-0.1.0.tgz
✅ dsh 安装成功

[3/5] 检查是否支持 dsh-vision-router...
✅ Node v24.11.1 支持 dsh-vision-router

[4/5] 安装 dsh-vision-router...
正在安装: dsh-vision-router-1.2.3.tgz
✅ dsh-vision-router 安装成功 (11 个视觉工具)

[5/5] 导入双模式预设...
✅ 预设导入成功

========================================
  离线安装完成！
========================================

下一步：
  1. 运行: node server.mjs
  2. 浏览器访问: http://127.0.0.1:3090
```

#### 5️⃣ 启动 GUI

```bash
cd ARID\tools\dual-model-gui
node server.mjs
```

浏览器访问: http://127.0.0.1:3090

#### 6️⃣ 验证安装

在 GUI 的"双模式体检"部分，应该看到：

```
预设挂载: arid-dualmodel ✅ 已安装
执行插件: ✅ 已安装 arid-dualmodel-ext6.cjs
视觉插件: ✅ dsh-vision-router (11 个工具)  ← 成功！
```

---

## 故障排查

### 问题 1: Node.js 未安装

**现象**:
```
❌ 未检测到 Node.js
```

**解决**:
1. 下载 Node.js 安装包（在有网络的电脑上）
2. 传输到目标电脑
3. 安装后重新运行 `install-offline.bat`

**Node.js 离线安装包**:
- 下载地址: https://nodejs.org/dist/
- 推荐版本: v22.x.x LTS

---

### 问题 2: npm 安装失败

**现象**:
```
❌ dsh 安装失败
```

**解决**:

```bash
# 方法 1: 手动安装
cd npm-packages
npm install -g deepseek-ai-dsh-0.1.0.tgz

# 方法 2: 使用国内镜像（如果有网络）
npm config set registry https://registry.npmmirror.com
npm install -g @deepseek-ai/dsh
```

---

### 问题 3: dsh-vision-router 安装失败

**现象**:
```
⚠ dsh-vision-router 安装失败，将使用旧版方案
```

**影响**: 不是致命问题，系统会自动降级到旧版视觉方案（1 个工具）

**解决**:

```bash
# 检查 Node 版本
node --version

# 如果 Node < 22，升级 Node
# 如果 Node ≥ 22，手动安装：
cd npm-packages
dsh plugin --profile web add dsh-vision-router-1.2.3.tgz
```

---

### 问题 4: Node 版本 < 22

**现象**:
```
⚠ Node v20.x.x < 22
将使用旧版视觉方案 (1 个工具)
```

**影响**: 只能使用 1 个视觉工具（而非 11 个）

**解决**:
- 升级 Node 到 22+
- 或接受旧版方案

**如何升级**:
1. 在有网络的电脑下载 Node 22+ 安装包
2. 传输到目标电脑安装
3. 重新运行 `install-offline.bat`

---

### 问题 5: 预设导入失败

**现象**:
```
⚠ 预设导入可能失败
```

**解决**:

```bash
cd ARID
install.bat

# 或手动复制预设
xcopy /E /I preset "%USERPROFILE%\.dsh\.agent-presets\arid-dualmodel"
```

---

## 包大小说明

### 典型包大小

| 组件 | 大小 |
|------|------|
| ARID 代码 | ~10-20 MB |
| @deepseek-ai/dsh | ~5-10 MB |
| dsh-vision-router | ~10-15 MB |
| 依赖包（sharp 等） | ~20-40 MB |
| **总计** | **50-100 MB** |

### 优化包大小

如果需要减小包大小，可以：

1. **移除不必要的文件**:
   - 删除 `.git` 目录（已自动排除）
   - 删除文档和示例（可选）

2. **只包含必要的依赖**:
   - 编辑 `pack-offline.bat`
   - 注释掉不需要的 `npm pack` 命令

3. **使用压缩**:
   - ZIP 压缩率约 30-50%
   - 7z 压缩率约 50-70%（更高压缩比）

---

## 更新离线包

### 何时需要更新？

- ✅ ARID 代码有重大更新
- ✅ dsh-vision-router 发布新版本
- ✅ 依赖包有安全更新

### 如何更新？

```bash
# 1. 在有网络的电脑上
cd "E:\DEEPSEEK ai\ARID"
git pull

# 2. 重新运行打包脚本
pack-offline.bat

# 3. 传输新的 ZIP 文件到目标电脑

# 4. 在目标电脑解压并运行 install-offline.bat
```

---

## 批量部署

### 场景：需要在 10+ 台电脑部署

#### 方法 1: 网络共享

```bash
# 1. 将离线包放到共享目录
\\server\share\ARID-Offline-Package\

# 2. 在每台电脑上运行
\\server\share\ARID-Offline-Package\install-offline.bat
```

#### 方法 2: 域策略部署

```bash
# 使用 GPO (Group Policy) 部署
# 1. 创建批处理脚本
# 2. 配置计算机启动脚本
# 3. 自动部署到域内所有电脑
```

#### 方法 3: U盘批量安装

```bash
# 1. 准备多个 U盘
# 2. 复制离线包到每个 U盘
# 3. 逐台电脑插入 U盘安装
```

---

## 验证清单

安装完成后，检查以下项目：

- [ ] GUI 能正常启动 (`node server.mjs`)
- [ ] 浏览器能访问 http://127.0.0.1:3090
- [ ] "双模式体检"显示所有插件已安装
- [ ] 视觉插件显示 `dsh-vision-router (11 个工具)` 或 `arid-dualmodel-vision (1 个工具)`
- [ ] 能启动 DSH Web 工作台
- [ ] AI 能正常响应消息

---

## 附录：手动离线安装

如果自动脚本失败，可以手动安装：

### 1. 安装 dsh

```bash
cd npm-packages
npm install -g deepseek-ai-dsh-0.1.0.tgz
```

### 2. 安装 dsh-vision-router

```bash
dsh plugin --profile web add dsh-vision-router-1.2.3.tgz
```

### 3. 导入预设

```bash
cd ..\ARID
install.bat
```

### 4. 启动 GUI

```bash
cd tools\dual-model-gui
node server.mjs
```

---

## 总结

| 步骤 | 有网络电脑 | 离线电脑 |
|------|-----------|---------|
| 1. 打包 | ✅ 运行 `pack-offline.bat` | - |
| 2. 传输 | ✅ 生成 ZIP 文件 | ✅ 接收 ZIP 文件 |
| 3. 安装 | - | ✅ 运行 `install-offline.bat` |
| 4. 验证 | - | ✅ 启动 GUI 验证 |

**关键优势**:
- ✅ 完全离线，无需任何网络连接
- ✅ 一次打包，多台电脑复用
- ✅ 自动安装，最小化人工操作
- ✅ 包含所有依赖，开箱即用

---

**打包时间**: 约 5 分钟  
**传输时间**: 取决于传输方式（U盘 ~1 分钟）  
**安装时间**: 约 2 分钟  
**总计**: 约 10 分钟完成一台电脑的部署
