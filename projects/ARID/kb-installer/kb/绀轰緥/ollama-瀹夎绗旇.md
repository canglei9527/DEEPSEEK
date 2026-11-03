---
标题: Ollama 本地安装笔记
标签: ollama, 大模型, 本地部署
来源: 官方文档 https://ollama.com
更新日期: 2025-01-01
---

# Ollama 本地安装笔记

## 安装

- Windows 直接下载安装包：https://ollama.com/download/windows
- 安装后命令行可用 `ollama` 命令

## 拉取模型

```bash
ollama pull nomic-embed-text   # 本地 embedding 模型，常用于知识库
ollama pull llama3.2           # 对话模型示例
```

## 常用命令

```bash
ollama list            # 查看已安装模型
ollama serve           # 启动服务（默认 127.0.0.1:11434）
ollama ps              # 查看正在运行的模型
```

## 备注

- 服务默认监听 `http://127.0.0.1:11434`，API 路径为 `/api/embed`、`/api/chat` 等
- 该笔记记录于搭建本地知识库（RAG 方案）的调研阶段
