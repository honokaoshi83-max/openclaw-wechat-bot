# 2026-10-04 搜索恢复与千问超时修复

## 搜索部署

Docker Desktop 启动因 sailor-ingest.sock 残留通信文件失败。没有删除 Docker 数据，也没有恢复出厂设置。为恢复搜索，在 OpenClawGateway WSL 中直接安装 SearXNG。

- 源码：`/home/openclaw/.local/share/wechat-search/searxng`
- Python 环境：同级 `venv`
- 配置：同级 `settings.yml`
- 服务：`systemctl --user status wechat-searxng.service`
- 地址：`http://127.0.0.1:8888`
- 引擎：Bing、DuckDuckGo，以及对应新闻引擎。Brave 因限流和错误停用。
- 服务随 OpenClaw Gateway 启动，并通过 PartOf 随 Gateway 停止，保持整个 BOT 开关的停用效果。
- 原 OpenClaw 和微信桥接的搜索地址无需修改。

验证：Windows 和 WSL 均获取 26 条搜索结果；OpenClaw DeepSeek Flash 独立测试实际调用 web_search 一次、失败零次，整轮约 3.9 秒。测试没有发送微信消息。

Docker 本身的套接字故障仍未修复，现有搜索不再依赖 Docker。未来恢复 Docker 容器前需避免同时占用 8888 端口。

## 千问超时原因与修复

本地 llama-server 健康检查正常，直接问候测试约 0.23 秒返回。问题发生在共享会话从 DeepSeek 切回 Qwen：会话计数仍显示 DeepSeek 的 1000000 token 容量，桥接跳过提前压缩，但本地 Qwen 只有 32768 token 容量。

日志显示上下文估算 50376 token，预算 24576；自动压缩耗时约 176 秒后超时。

修复：调用前携带当前模型名称，Qwen 的压缩判断以本地模型容量为上限，不再信任上一模型的大容量计数。当前受影响会话已完成摘要压缩，28151 -> 16417 token。没有重置或删除会话记忆。

已增加切换模型容量回归测试，91 项 bridge 测试通过；桥接已重启加载修复。

实际受影响用户会话验证：第一轮经桥接 ask_openclaw 返回“千问已恢复”，总耗时 21.45 秒，其中预压缩约 15 秒；第二轮返回“正常”，耗时 2.12 秒。两轮均没有发送微信消息。

安装依据：[SearXNG 官方安装文档](https://docs.searxng.org/admin/installation-searxng.html)。
