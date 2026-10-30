> 2026-10-29 拟人化更新：已启用会话队列、表情缓存、心情与保守成员档案；自动公告关闭，旧括号状态栏彻底移除。已接入的两个群加入白名单，主动发言仍为影子模式。详细开关见 docs/HUMANIZE_2026-10-29.md。

# 大肥鱼微信 AI Bot / WeChat AI Bot

当前架构：微信 Hook + Python 桥接 + OpenClaw + DeepSeek API + SearXNG。
Current architecture: WeChat Hook, Python bridge, OpenClaw, DeepSeek API and SearXNG.

## 2026-10-29：统一 DeepSeek / DeepSeek only

文字、图片、表情包和 GIF 的现有视觉调用统一走 DeepSeek API。本地推理服务与本地生图、改图入口已移除。
Text and existing visual workflows use DeepSeek API. Local inference, image generation and editing entrypoints have been retired.

API 对当前模型和图片格式的实际支持以服务返回为准；此迁移不保证识图准确率。
Actual image support depends on the provider response; this migration does not guarantee recognition accuracy.

## 功能 / Features

- 私聊自然分段，群聊 @ 触发并回复对应发送者；群聊单段。
- 联系人上下文隔离、群聊上下文、引用文字评论和核实。
- 普通图片、引用图片、表情包和 GIF 的现有识别流程。
- 自然联网、“搜索”“核实”及 /search；明确要求链接时提供来源链接。
- 东京时间每日公告，夜间静默规则、上下文压缩、字数与耗时标注。
- 开机后台自启、手动登录微信、BotSwitch.exe 开关。

Private/group chat, isolated private histories, quoted-message analysis, existing media recognition,
web search, daily help, quiet hours, context management, response timing and a desktop switch.

## 指令 / Commands

| 指令 | 用途 / Purpose |
|---|---|
| /help | 公告与使用帮助 / Help |
| /search 关键词 | 联网搜索 / Web search |
| /compact | 压缩上下文 / Compact context |
| /reset | 重置上下文 / Reset context |
| /clear | 清除当前会话记忆 / Clear conversation memory |
| /deepseek、切换大肥鱼 | 兼容旧客户端的 DeepSeek 确认入口 / Legacy DeepSeek alias |

也可以直接说“搜索……”或“核实……”。所有会话均使用 DeepSeek；不再提供本地模型或绘图模式切换。
All conversations use DeepSeek. Local model and drawing mode switches are no longer available.

## 部署 / Deployment

1. 在 Windows 准备 Python 环境 work/.venv、项目的微信 Hook 与兼容微信版本；AI 账号使用配置的 Hook 端口。
2. 在 WSL OpenClawGateway 安装 OpenClaw，注册 DeepSeek provider 和 wechat-public agent。只允许 web_search、web_fetch；不要给公开微信入口配置通用系统操作权限。
3. 本机部署 SearXNG，当前项目使用 WSL 原生服务 127.0.0.1:8888，不要求 Docker Desktop。
4. 在 outputs/WeChatAI/config.json 填写账号和 Hook 配置，models 仅保留 deepseek，initial_model 为 deepseek。
5. 在本机安全设置 DEEPSEEK_API_KEY 用户环境变量；不要把密钥提交到 Git。start.ps1 会为视觉调用读取该用户变量；OpenClaw provider 另需配置有效凭据。
6. 运行 enable_autostart.ps1 注册后台任务；安装控制台自启可用 install_bot_switch_autostart.ps1。微信仍由用户手动登录。

Use the existing Windows Python environment and compatible WeChat Hook, configure OpenClaw in WSL,
provide DeepSeek credentials, run SearXNG, then register the startup task. Keep credentials out of Git.
The Hook server is local on port 30001; Gateway uses 18789; search uses 8888. No local model port is required.

## 启停与检查 / Control and checks

- 控制台：outputs/WeChatAI/BotSwitch.exe。
- 后台管理：Windows PowerShell 5.1 中执行 manage_bot_stack.ps1 -Action Start / Stop / Status。
- 测试：work/.venv/Scripts/python.exe -m unittest discover -s work -p 'test_*.py'。
- 不发送微信的实际模型检查：work/.venv/Scripts/python.exe work/verify_openclaw_turn.py。
- 日志：work/wechat_ai_bridge_state/bridge.log、watchdog.log、startup.log。

## 隐私 / Privacy

DeepSeek 是远程 API，提交的对话与识图图片会发送给 API 服务；搜索会向搜索引擎发送必要查询。
DeepSeek is a remote API: prompts and recognition images are sent to the provider; search queries are sent to search engines.
不要将运行状态、私人媒体、数据库或凭据上传 GitHub。
Do not publish runtime state, private media, databases or credentials.

## 历史说明 / Historical notes

旧功能代码和说明保存在 work/retired_local_components 及 work/backups，用于回滚，不在 Bot 活跃导入路径中。
旧专项文档仅代表对应日期；当前变更见 docs/BOT_DEEPSEEK_ONLY_2026-10-29.md。
Retired sources are kept as inert backup files. Historical documents describe earlier versions.
