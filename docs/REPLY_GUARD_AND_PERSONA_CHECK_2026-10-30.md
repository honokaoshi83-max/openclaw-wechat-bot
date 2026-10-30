# 回复安全闸与人格体检

日期：2026-10-30。

## 回复安全闸

参考 Xiaojingyu-QQbot 的回复审计思路独立实现（该仓库许可证待定，未复制代码）。

- `reply_guard.py`：模型回复里出现本机用户路径（`X:\Users\`、`/mnt/<盘>/`、`/home/<名字>/`、`/root/`、`\\wsl$`）、`.openclaw`、`sk-` 开头的 key、`api_key=…` 这类赋值，或 config 里 `*_env` 指向的真实密钥值，整条换成「这条我不方便发。」，日志只记命中，不记内容。
- 接在 `bridge.sanitize_reply` 的最后一步，所有经过 OpenClaw 的回复都会过这道检查；联网标记保留。
- `C:\Program Files`、`/usr/bin` 这类通用路径和单独的 "token"、"密码" 字样不拦。已知代价：用户自己发了 `C:\Users\...` 路径、模型照着回时也会被拦。
- 没有做“上下文里没出现的链接一律替换”：OpenClaw 自己联网搜索，程序看不到搜索结果，照搬会误伤正常的联网回复。
- 开关：`reply_leak_guard_enabled`（默认 true）。

## 回复日志

- `reply_log.py`：Hook 发送并在数据库确认成功后，写一行到 `work/wechat_ai_bridge_state/replies.jsonl`（已在 .gitignore 范围内）。字段：时间、群聊/私聊、会话 id（wxid 的 sha256 前 12 位，不存原始 wxid）、回复原文。不记对方原话。
- 超过 `reply_log_max_bytes`（默认 2MB）时保留较新的一半。开关：`reply_log_enabled`（默认 true）。
- 和防复读一样，记录的是所有确认发出的文字，包括指令回执。

## 人格体检

- `check_persona.py` 移植自 chennianlaobai/qq-ai-bot（MIT，许可全文 `LICENSE-check_persona.txt`），去掉 NoneBot 配置依赖，改读上面的日志。
- 规则在 `persona_rules.json`，按 `persona.md` 改写：客服腔、主动提 AI、「您」、列表/标题/总结、客套收尾、安慰模板、脏话、内部标记泄露、兜底话术、解说图片、动作描写、微信表情代码超过 1 个，以及「本鲸」、白饭/鲸尾梗、反问「怎么了」过密。长度类只给 warn，因为认真问题允许长。
- 只离线读日志，不影响 Bot 运行。用法：

```
.\work\.venv\Scripts\python.exe outputs\WeChatAI\check_persona.py            # 最近 50 条
.\work\.venv\Scripts\python.exe outputs\WeChatAI\check_persona.py --kind group --last 200
.\work\.venv\Scripts\python.exe outputs\WeChatAI\check_persona.py --text "要测的一句话"
```

## 验证

- 备份：`work/backup_reply_guard_2026-10-30/`（bridge.py、config.json、test_humanize.py）。
- 新增 `work/test_reply_guard.py` 12 项：路径/密钥拦截、通用路径放行、真实密钥值匹配、开关、sanitize_reply 接入、日志写入/截断/关闭、Hook 确认发送后写日志、体检规则与密度规则。完整回归 166 项通过。
- Bot 未重启，改动在下次重启后生效；未发送微信测试消息；未提交或推送 GitHub。
