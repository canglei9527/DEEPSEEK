# DEEPSEEK

canglei9527 的公开项目集合。

每个项目在 `projects/` 下独立成目录，保留各自的完整结构与文档。

## 项目列表

| 项目 | 说明 | 语言 |
|---|---|---|
| [`projects/ARID`](projects/ARID) | 把 aider-gui 的核心思想在 DeepSeek Harness (DSH) 上重实现：双模型分工与升级流程、多平台模型/密钥管理（含 OpenAI 兼容网关）、本地知识库检索、飞书远程控制 | JavaScript |
| [`projects/codex-agent-console`](projects/codex-agent-console) | Windows desktop console for Codex dual-model orchestration and token statistics | Python |
| [`projects/dsh-router`](projects/dsh-router) | Task-aware reasoning-mode router ported from dsh-routing-suite to ZCode: classifies each prompt (build/fix/ambiguous) and injects the matching working style and workflow stage near-field via hooks | JavaScript |
| [`projects/f28p55x-driverlib-guide-zh-cn`](projects/f28p55x-driverlib-guide-zh-cn) | F28P55x DriverLib API Guide v5.04.00.00 中文离线指南 | HTML / 中文 |
| [`projects/f28p55x-study-library-zh-cn`](projects/f28p55x-study-library-zh-cn) | F28P55x 00–19 中文开发学习资料，含译文、中英对照和原文 | PDF |
| [`projects/flow-pilot`](projects/flow-pilot) | AI 项目维护与初始化工作流 / Reusable workflows for focused maintenance, verification, and project bootstrap | JavaScript |
| [`projects/jingxiao-ipad-pwa`](projects/jingxiao-ipad-pwa) | Standalone offline-first iPad web app | JavaScript |
| [`projects/PowerLens`](projects/PowerLens) | Android 功耗归因工具：C 采集器（root 侧 procfs/dumpsys 双源）+ 免 root Java UI，逐对核算消除充电伪数据，四口径归因（全部/前台/后台/前台服务），含真机实测基线与 KernelSU 踩坑记录 | Java / C |
| [`projects/upstream-price-adjuster`](projects/upstream-price-adjuster) | D-AI upstream price adjustment and lifecycle sync console | JavaScript |

## 说明

- 各项目保留原始目录结构与文档，`README` 在各自目录下
- 本仓库是**汇总视图**，各项目仍以原始仓库为准
- 私有项目不在本仓库中

## 许可

各项目许可见其自身目录下的 `LICENSE` 文件。