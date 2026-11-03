# 🚀 ARID 双模式快速开始

## 📖 三种部署方式

### 方式 1️⃣: 在线部署（推荐）✅

**适用**: 有网络连接的电脑

```bash
# 克隆仓库
git clone https://github.com/canglei9527/ARID.git
cd ARID/tools/dual-model-gui

# 启动 GUI
node server.mjs

# 浏览器打开 http://127.0.0.1:3090
# 点击"一键安装 Harness"
```

**或使用自动部署脚本**:
```bash
# 双击运行
deploy-new-computer.bat
```

---

### 方式 2️⃣: 离线部署 💾

**适用**: 无网络或内网隔离环境

#### 在有网络的电脑上：
```bash
cd ARID
pack-offline.bat
# 生成 ARID-Offline-20260816.zip
```

#### 在目标电脑上：
```bash
# 1. 解压 ZIP 文件
# 2. 双击运行 install-offline.bat
# 3. 完成！
```

详见: [OFFLINE_DEPLOYMENT_GUIDE.md](OFFLINE_DEPLOYMENT_GUIDE.md)

---

### 方式 3️⃣: 已有仓库更新 🔄

**适用**: 已部署过的电脑

```bash
cd ARID
git pull
cd tools/dual-model-gui
node server.mjs
```

---

## 🎯 安装后验证

打开 GUI (http://127.0.0.1:3090)，查看"双模式体检"：

```
✅ 预设挂载: arid-dualmodel 已安装
✅ 执行插件: 已安装 arid-dualmodel-ext6.cjs
✅ 视觉插件: dsh-vision-router (11 个工具)  ← 成功标志
✅ 规划模型: ...
✅ 执行模型: ...
```

---

## 📚 相关文档

- [DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md) - 完整部署指南（Git / 打包 / 便携版）
- [OFFLINE_DEPLOYMENT_GUIDE.md](OFFLINE_DEPLOYMENT_GUIDE.md) - 离线安装详细说明
- [DSH_VISION_ROUTER_INTEGRATION.md](tools/dual-model-gui/DSH_VISION_ROUTER_INTEGRATION.md) - 技术集成文档
- [ONE_CLICK_INSTALL_TEST.md](tools/dual-model-gui/ONE_CLICK_INSTALL_TEST.md) - 测试指南

---

## ⚡ 特性

| 功能 | 说明 |
|------|------|
| **11 个视觉工具** | vision_describe, vision_ground, vision_ocr 等 |
| **自动版本检测** | Node ≥22 使用新方案，<22 自动降级 |
| **一键安装** | GUI 界面点击按钮即可完成所有安装 |
| **离线支持** | 完全无网络环境也能部署 |
| **跨电脑部署** | Git 同步或打包传输 |

---

## 🔧 Node 版本要求

- **推荐**: Node 22+ （获得 11 个视觉工具）
- **最低**: Node 18+ （自动降级到 1 个视觉工具）

检查版本:
```bash
node --version
```

---

## 💡 常见问题

### Q: 其他电脑如何使用？

**A**: 三种方式任选其一：
1. `git clone` + 点击"一键安装"（有网络）
2. 运行 `deploy-new-computer.bat`（有网络）
3. 使用离线包 `pack-offline.bat`（无网络）

### Q: 如何更新到最新版？

**A**: 
```bash
git pull
# 无需重新安装，代码会自动更新
```

### Q: 视觉插件显示旧方案怎么办？

**A**: 
- 检查 Node 版本是否 ≥22
- 点击"一键安装 Harness"重新安装
- 或运行 `dsh plugin --profile web add dsh-vision-router`

---

## 📞 获取帮助

- 📖 查看 [DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md) 详细文档
- 🐛 遇到问题查看 [故障排查](OFFLINE_DEPLOYMENT_GUIDE.md#故障排查)
- 💬 提交 Issue 到 GitHub

---

**版本**: 2026-08-16  
**视觉工具**: dsh-vision-router 1.2.3 (11 个工具)
