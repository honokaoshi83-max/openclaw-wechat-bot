# OpenClaw WeChat AI Bot

一个基于 OpenClaw、llama.cpp、DeepSeek、Qwen 和 Docker SearXNG 的微信 AI 机器人。

An AI bot for WeChat built with OpenClaw, llama.cpp, DeepSeek, Qwen, and Docker SearXNG.

## 项目简介 | Overview

本项目通过独立微信账号接收私聊和群聊消息，使用 OpenClaw 管理会话和模型路由，并通过 WeChat Hook 发送回复。模型可以在云端 DeepSeek 与本地 Qwen 之间切换，联网搜索由本机 Docker 中的 SearXNG 完成。

This project uses a dedicated WeChat account for private and group conversations. OpenClaw manages sessions and model routing, while WeChat Hook sends replies. Users can switch between cloud DeepSeek and local Qwen. Web search is provided by a local SearXNG container running in Docker.

## 主要能力 | Features

- 私聊和群聊回复 / Private and group chat replies
- DeepSeek 与本地 Qwen 切换 / Switch between DeepSeek and local Qwen
- `/fast` 和 `/think` 模式 / Fast and thinking modes
- Qwen 低精度图片、表情包、GIF 识别 / Qwen image, emoji, and GIF recognition
- DeepSeek V4.1 Flash 高准确识图 / DeepSeek V4.1 Flash visual recognition
- 群聊 `@` 触发和回复时 `@` 实际发送者 / Group mentions and sender mentions
- 最近群消息总结 / Summarize recent group messages
- Docker SearXNG 联网搜索 / Web search through Docker SearXNG
- 搜索结果按日期优先归纳 / Date-aware search result synthesis
- 搜索结果纯文本润色 / Plain-text cleanup for WeChat
- 每个微信用户独立会话记忆 / Per-user conversation isolation
- 回复文字数量和耗时提示 / Output length and response time notices

## 技术架构 | Architecture

```text
WeChat desktop + Hook
          │
          ▼
Python WeChat Bridge
          │
          ├── OpenClaw Gateway
          │      ├── DeepSeek API
          │      └── llama.cpp → local Qwen
          │
          └── Docker SearXNG → search results → model summary
```

核心组件：

- `outputs/WeChatAI/bridge.py`：消息读取、命令解析、会话隔离、回复发送和搜索归纳。
- OpenClaw `wechat-public`：模型路由、上下文管理和工具权限。
- llama.cpp：提供本地 Qwen 推理接口。
- Docker SearXNG：本地联网搜索服务，避免直接依赖 Google 网页验证。
- WeChat Hook：后台发送消息，不需要抢占鼠标或前台窗口。

Core components:

- `outputs/WeChatAI/bridge.py`: message polling, command parsing, session isolation, delivery, and search synthesis.
- OpenClaw `wechat-public`: model routing, context management, and tool permissions.
- llama.cpp: local Qwen inference endpoint.
- Docker SearXNG: local web search service without direct Google browser automation.
- WeChat Hook: background message delivery without taking over the mouse or foreground window.

## 使用命令 | Commands

在微信中发送：

| 命令 | 作用 |
| --- | --- |
| `/deepseek` | 切换 DeepSeek |
| `/qwen` | 切换本地 Qwen |
| `/fast` | Qwen 快速模式 |
| `/think` | Qwen 思考模式 |
| `/compact` | 压缩当前会话上下文 |
| `/reset` | 重置当前会话上下文 |
| `/help` | 查看每日公告和帮助 |
| `/search 关键词` | 联网搜索并归纳 |
| `/clear` | 清除当前用户或群聊的会话记忆 |

Send these commands in WeChat:

- `/deepseek`: use DeepSeek.
- `/qwen`: use local Qwen.
- `/fast`: Qwen fast mode.
- `/think`: Qwen thinking mode.
- `/compact`: compact the current session context.
- `/reset`: start a new session context.
- `/help`: show the daily help notice.
- `/search query`: search the web and summarize the results.
- `/clear`: clear memory for the current private chat or group only.

默认不会输出搜索链接。只有在查询中明确要求“标明链接”“提供来源”或“给出网址”时，才保留来源链接。

Search replies omit URLs by default. URLs are kept only when the user explicitly asks for links or sources.

## 安装与运行 | Installation and Run

### 1. 前置条件 | Prerequisites

- Windows 10/11
- WeChat desktop with the required Hook component
- Python 3.12 or the project virtual environment
- OpenClaw Gateway in WSL
- Docker Desktop
- llama.cpp server for local Qwen

### 2. 启动 SearXNG | Start SearXNG

使用 Docker Desktop 启动名为 `openclaw-searxng` 的容器，并将容器 8080 端口映射到本机 8888 端口。配置文件位于：

```text
work/searxng/settings.yml
```

Start a container named `openclaw-searxng` and map container port 8080 to host port 8888. The configuration is stored at:

```text
work/searxng/settings.yml
```

### 3. 启动 OpenClaw | Start OpenClaw

确认 OpenClaw Gateway 正常运行，并在 `wechat-public` 智能体中允许 `web_search` 和 `web_fetch`。

Make sure the OpenClaw Gateway is running and that the `wechat-public` agent allows `web_search` and `web_fetch`.

## 部署步骤 | Deployment Guide

### A. 部署 Docker SearXNG | Deploy Docker SearXNG

1. 安装并启动 Docker Desktop。
2. 确认 Docker 引擎处于运行状态。
3. 使用 `work/searxng/settings.yml` 创建 SearXNG 配置。
4. 启动容器并映射端口：

```powershell
docker run -d --name openclaw-searxng `
  -p 127.0.0.1:8888:8080 `
  -v "${PWD}\work\searxng\settings.yml:/etc/searxng/settings.yml:ro" `
  searxng/searxng:latest
```

5. 验证服务：

```powershell
Invoke-RestMethod "http://127.0.0.1:8888/search?q=test&format=json"
```

如果容器已经存在，使用 `docker start openclaw-searxng`，不要重复创建。

If the container already exists, use `docker start openclaw-searxng` instead of creating it again.

### B. 部署 OpenClaw Gateway | Deploy OpenClaw Gateway

1. 在 WSL 中安装 Node.js 和 OpenClaw。
2. 创建或确认 `wechat-public` 智能体。
3. 配置 DeepSeek API 凭据（如使用 DeepSeek）。
4. 配置本地搜索地址和工具权限：

```json
{
  "tools": {
    "web": {
      "search": {
        "enabled": true,
        "provider": "searxng"
      }
    }
  },
  "agents": {
    "entries": {
      "wechat-public": {
        "tools": {
          "profile": "coding",
          "allow": ["web_search", "web_fetch"]
        }
      }
    }
  }
}
```

5. 将 SearXNG 地址配置为 `http://127.0.0.1:8888`。
6. 验证并重启：

```bash
openclaw config validate
openclaw gateway restart
curl http://127.0.0.1:18789/health
```

健康检查返回 `status: live` 后再继续下一步。

### C. 部署本地 Qwen | Deploy local Qwen

1. 安装 llama.cpp server。
2. 准备支持文本和图像输入的 Qwen GGUF 模型。
3. 启动 OpenAI 兼容接口，默认地址：

```text
http://127.0.0.1:8080/v1
```

4. 在 OpenClaw 模型提供商中填写 llama.cpp 的 `baseUrl`、模型 ID、上下文长度和输出上限。
5. 先用 OpenClaw 或 curl 做一次本地文本测试，再启动微信桥接。

The local Qwen endpoint must be reachable before `/qwen` or image recognition can work.

### D. 配置微信 Hook | Configure WeChat Hook

1. 手动登录 AI 微信账号。
2. 启动与当前微信版本匹配的 WeChat Hook 服务。
3. 确认发送接口可访问：

```text
http://127.0.0.1:30001/SendTextMsg
```

4. 确认 Hook 读取到当前 AI 微信账号，并记录账号 wxid。

The Hook process and WeChat account must belong to the same desktop session.

### E. 配置并启动桥接 | Configure and Start the Bridge

复制示例配置并按本机情况修改：

```text
outputs/WeChatAI/config.json
```

至少需要确认：

```json
{
  "ai_account_dir": "你的 AI 微信账号目录",
  "main_peer": "主微信 wxid",
  "send_mode": "hook",
  "hook_url": "http://127.0.0.1:30001",
  "agent": "wechat-public",
  "searxng_url": "http://127.0.0.1:8888"
}
```

启动：

```powershell
.\outputs\WeChatAI\start.ps1
```

检查日志：

```powershell
Get-Content ".\work\wechat_ai_bridge_state\bridge.log" -Tail 30
```

看到 `bridge ready` 和 `online recovery complete` 后，发送一条普通文字测试，再测试 `/help` 和 `/search 测试关键词`。

### F. 开机启动 | Start at Boot

推荐只设置后台服务自启：

- Docker Desktop：系统登录后自动启动；
- OpenClaw Gateway：使用 systemd user service；
- llama.cpp：使用隐藏启动脚本或任务计划程序；
- 微信：手动登录 AI 账号；
- WeChat Bridge：使用任务计划程序，在微信和 Hook 就绪后启动 `start.ps1`。

Do not store API keys or WeChat credentials in the startup scripts. Keep them in local-only configuration or the system credential store.

### 4. 登录微信并启动桥接 | Log in to WeChat and start the bridge

微信账号需要手动登录，然后在项目目录运行：

```powershell
.\outputs\WeChatAI\start.ps1
```

停止桥接：

```powershell
.\outputs\WeChatAI\stop.ps1
```

Log in to WeChat manually, then run the start script from the project directory. Use `stop.ps1` to stop the bridge.

## 配置 | Configuration

运行配置文件不应提交到公开仓库。请根据本机环境创建：

```text
outputs/WeChatAI/config.json
```

不要上传微信账号目录、wxid、Hook 密钥、API Key、数据库、缓存或日志。

Do not commit WeChat account directories, wxids, Hook credentials, API keys, databases, caches, or logs.

## 应用场景 | Use Cases

- 个人微信 AI 助手 / Personal WeChat AI assistant
- 本地模型聊天和隐私优先的问答 / Local-model chat and privacy-oriented Q&A
- 群聊问答、群消息总结 / Group Q&A and conversation summaries
- 新闻和赛事等实时信息查询 / Current news and event lookup
- 图片、表情包和 GIF 内容理解 / Understanding images, emojis, and GIFs
- 电脑后台运行的自动化客服或信息助手 / Background automation for support and information services

## 隐私与安全 | Privacy and Security

私聊记忆按微信用户隔离。联网搜索只发送搜索问题和必要的摘要内容。使用云端 DeepSeek 时，对话内容会发送到对应 API；使用本地 Qwen 时，文本推理在本机 llama.cpp 服务中完成。

Private conversation memory is isolated by WeChat user. Web search sends only the query and the minimum required summaries. When DeepSeek is selected, conversation content is sent to its API. When local Qwen is selected, text inference runs through the local llama.cpp service.

## 测试 | Tests

运行桥接测试：

```powershell
.\work\.venv\Scripts\python.exe -m unittest work.test_bridge
```

当前基线为 70 项测试全部通过。

The current baseline is 70 passing tests.

## 项目状态 | Project Status

详细进度和已知限制见：

```text
OPENCLAW_WECHAT_PROJECT_SUMMARY.md
```

See `OPENCLAW_WECHAT_PROJECT_SUMMARY.md` for detailed progress and known limitations.
