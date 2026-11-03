# 知识库检索 Agent —— 一键安装包

把本文件夹复制到另一台电脑，解压后**双击 `install.bat`** 即可完成安装。

## 包内内容

| 路径 | 说明 |
|---|---|
| `install.bat` | 一键安装入口（双击运行） |
| `install.ps1` | 实际安装脚本（`install.bat` 会调用它） |
| `preset/` | 「知识库检索」agent 预设模板（含 `kb_search` / `kb_read` / `kb_list` 三个工具的插件） |
| `kb/` | 知识库内容（README 索引、资料模板、DSP 文档转换文本、原始 PDF） |

## 安装前要求

- **Windows 10/11**（本包按 Windows 设计）
- **Node.js 20 或更高**：https://nodejs.org （安装脚本会检查；没有则提示你先装）
- 如果目标电脑**还没装 DSH**，安装脚本会自动执行 `npm install -g @deepseek-ai/dsh`（需要联网，可能要几分钟）；失败时请手动运行该命令

## 安装过程（install.bat 自动完成）

1. **知识库** → 默认安装到 `C:\Users\<你的用户名>\kb`
   - 想装到别处：在 PowerShell 里运行
     `.\install.ps1 -KbRoot D:\我的知识库`
2. **agent 预设** → 安装到 `C:\Users\<你的用户名>\.dsh\.agent-presets\kbagent\`
   - 预设里的知识库路径会自动改成你机器上的实际位置，无需手动改配置
3. **环境检查** → 检查 Node.js / DSH，缺失则尝试自动安装

## 安装后使用

1. 启动 DSH（和你现在电脑上一样的启动方式），打开 Web 界面
2. **新建会话时，在预设列表选择「知识库检索」（kbagent）**
3. 直接说「查一下知识库里的 XXX」，助手就会用 kb_search / kb_read / kb_list 检索并引用原文回答

## 常见问题

- **双击 install.bat 一闪而过**：右键 → 以管理员身份运行；或在 PowerShell 中运行 `.\install.ps1`
- **报"禁止运行脚本"**：install.bat 已带 `-ExecutionPolicy Bypass`，正常情况下不会触发；若手动运行 install.ps1 报错，先执行 `Set-ExecutionPolicy -Scope Process Bypass`
- **npm 安装 DSH 失败**：检查网络（国内可配置 npm 镜像：`npm config set registry https://registry.npmmirror.com`），然后手动运行 `npm install -g @deepseek-ai/dsh`
- **知识库里有新的 PDF**：助手只能检索文本，PDF 需先转换（放到 kb 后让助手转换，或复制 `kb\文档\` 里已有的转换结果）

## 备份说明

本安装包由已配置好的 `kbagent` 预设模板化而来：预设文件里的路径占位符 `__KB_ROOT__` 会在安装时替换为你机器的实际路径。
