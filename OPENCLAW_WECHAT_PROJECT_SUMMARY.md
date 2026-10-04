# OpenClaw 微信 AI Bot 项目总结

## 2026-10-04 最新进展

- 用户已确认：完整提问和后续追问测试正常。
- 搜索恢复为 WSL 原生 SearXNG（127.0.0.1:8888）；当前不依赖 Docker Desktop。Docker 启动故障尚未修复。
- 自然联网适用于私聊与原有群聊 @ 入口；保留 /search 和中文搜索、核实指令。
- 搜索与普通对话使用同一个用户/群聊及 session epoch，不再将搜索回答隔离到临时会话。
- 搜索后 10 分钟内的简短疑问，结合上一搜索主题重新检索；话题按会话隔离，/reset、/clear 通过 epoch 切换隔离旧主题。普通问候、创作和图像请求排除。
- 状态栏按实际获取资料或成功调用工具添加“已联网”；不保证搜索来源或模型归纳绝对正确。
- Qwen 预压缩容量以当前本地模型为准，触发比例最高 50%，为系统提示、工具和输出留空间。
- 长问题出现不超过 4 字的异常短回复时，压缩后重试一次；持续异常走失败提示。该启发式也可能重试合法的短答案。
- 私聊分段、群聊单段及 @ 发送者、独立队列、用户记忆隔离保持。
- 修图功能停用，表情包名称识别和修图后续计划取消。
- 101 项桥接回归测试通过；用户确认真实微信追问验收正常。不能由此推断所有问题均会触发联网或答对。


更新时间：2026 年 9 月 20 日（东京时间）

## 项目目标

使用独立微信 AI 账号接收私聊和群聊消息，通过 OpenClaw 调用 DeepSeek 或本地 Qwen，并使用微信 Hook 发送回复，避免鼠标和前台窗口被抢占。

## 当前架构

- **微信接入**：`outputs/WeChatAI/bridge.py`
- **消息读取**：`wechatauto-replica` 读取本地微信数据库
- **消息发送**：WeChat Hook HTTP 接口，默认 `http://127.0.0.1:30001`
- **智能体**：OpenClaw `wechat-public`
- **DeepSeek**：`deepseek/deepseek-v4-pro`
- **本地模型**：llama.cpp 提供 Qwen3.5 9B Q4_K_M
- **联网搜索**：Docker Desktop 中的 SearXNG，地址 `http://127.0.0.1:8888`

## 已实现功能

### 私聊

- `/deepseek`、`/qwen` 模型切换
- `/think`、`/fast` 思考模式切换
- `/compact` 压缩当前会话上下文
- `/reset` 重置当前会话上下文
- `/help` 每日公告，日期按东京时间生成
- 普通对话分段拟人化回复
- 回复尾部显示模型、输出文字数量和耗时
- 不同微信用户独立会话记忆
- 图片、表情包、GIF 识别（仅 Qwen）
- DeepSeek 模式收到图片时给出不支持提示
- 每天第一次对话发送公告

### 群聊

- 只有 `@大肥鱼` 或配置的机器人名称才触发
- 群聊回复单段发送，不使用私聊分段逻辑
- 回复会直接 `@` 实际发送者名称
- 支持 `/qwen`、`/deepseek`、`/fast`、`/think`
- “总结对话”相关关键词触发最近约 70 条群消息总结
- 支持 `@机器人 识别图片` 后识别该用户 3 秒内发送的图片、表情包或 GIF
- 支持 `/help`

### 联网搜索

- `/search 关键词` 触发
- Docker SearXNG 检索新闻和普通网页
- 优先带发布日期、较新的新闻结果
- 取有限数量的标题和摘要交给模型归纳
- 搜索使用独立会话，避免个人长上下文拖慢搜索
- 默认不输出链接，用户明确要求时才保留来源链接
- 搜索结果经过纯文本润色，清理 Markdown 符号和多余空行
- 回复显示耗时

## 运行方式

### 启动微信桥接

在项目目录执行：

```powershell
.\outputs\WeChatAI\start.ps1
```

停止：

```powershell
.\outputs\WeChatAI\stop.ps1
```

微信账号需要由用户手动登录，桥接程序不负责微信自启或登录。

### Docker SearXNG

- 容器名：`openclaw-searxng`
- 本机地址：`http://127.0.0.1:8888`
- 配置：`work/searxng/settings.yml`

### OpenClaw

- WSL 发行版：`OpenClawGateway`
- 网关端口：`18789`
- 配置：`/home/openclaw/.openclaw/openclaw.json`
- `wechat-public` 显式允许 `web_search`、`web_fetch`

## 测试状态

当前桥接测试：

```text
Ran 70 tests
OK
```

## 重要配置

编辑 `outputs/WeChatAI/config.json` 时需要确认：

- `ai_account_dir` 与当前登录的 AI 微信账号一致
- `main_peer` 为主微信账号 wxid
- `hook_url` 与 Hook 服务端口一致
- `send_mode` 保持为 `hook`

## 安全与发布注意事项

不要上传以下内容：

- 微信数据库、缓存和密钥文件
- OpenClaw API 密钥、Token、认证配置
- `work/*_cache`、日志、临时图片
- 本地账号 wxid 和运行状态文件

公开 GitHub 项目应只包含源代码、示例配置和脱敏文档。真实运行配置应放在本地，不提交到仓库。

## 已知限制

- DeepSeek 图片识别未启用，图片功能走 Qwen。
- 搜索质量依赖 SearXNG 当前可用引擎和搜索摘要质量。
- 搜索仍需要一次模型归纳，因此速度取决于当前模型和上下文状态。
- 微信 Hook 接口属于本机组件，升级微信或 Hook 版本后需要重新验证。
