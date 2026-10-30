# 直连 DeepSeek：第 3 步（私聊与记忆）

日期：2026-10-30。私聊改走直连，记忆由本机自己保存；从 OpenClaw 导出摘要作为起点。

## 记忆怎么存

- `chat_memory.py`：每个私聊一个文件，放在 `work/wechat_ai_bridge_state/chat_memory/`（已在 .gitignore 范围内），文件名是 wxid 的哈希。内容 = 最近若干轮对话 + 一段滚动摘要 + 所属的 `session_epoch`。
- 每次成功发出私聊回复后写入这一轮；总字数超过 `direct_chat_memory_max_chars`（6000）时，用 DeepSeek 把较早的对话并进摘要，只保留最近 `direct_chat_memory_keep_messages`（8）条。整理规则沿用 persona.md"压缩会话时"那段：保留偏好、称呼、纠正、当前话题、没聊完的问题和人物指代，标出不确定的，丢掉客套和长摘录。写入或整理失败只记日志，不影响已经发出的回复。
- 请求顺序：人设 → 示范 →【之前和这个人聊过的事】摘要 → 最近对话 → 本轮消息。前两段与群聊相同，仍可命中缓存。
- `/compact`：立即整理这个私聊的记忆，回"记忆已整理：x → y 字"。`/clear`：删除记忆文件。`/reset`：照旧把 `session_epoch` 加一，旧记忆按 epoch 不匹配自动作废，下一轮覆盖。群聊不保存记忆（每次带最近群聊）。

## 私聊流程的改动

- `bridge.ask_private`：私聊统一入口，带这个会话的摘要和最近对话；有搜索工具；办事请求降温度并加提示；失败按 `direct_chat_openclaw_fallback` 回落。
- 文字、图片/表情包、`/search`、引用图片四种私聊回复，发送成功后都写入记忆（图片记成"[发了一张图片：识别描述]"，搜索记成"（让你搜：…）"）。
- 图片回复不再按图片描述里的关键词触发自动搜索（与群聊一致）。
- 直连的私聊跳过 OpenClaw 的"慢回复后自动压缩"。
- `visual_reply_prompt` / `sticker_context_prompt` 新增 `wrap=False`，给直连用不带 OpenClaw 指令的原文；默认行为不变。
- 开关 `direct_chat_private`（现为 true）。回退：改 false 并重启，私聊回到 OpenClaw（直连期间的新对话不会回写进 OpenClaw）。

## 迁移

- `work/migrate_openclaw_memory.py`：对每个私聊，先请它自己的 OpenClaw 会话按同样规则写摘要；会话已归档无法对话时，用 `openclaw sessions export-trajectory` 导出脱敏记录，只取用户原话（去掉 bridge 附加的方括号指令）和回复正文，再用 DeepSeek 整理。导出文件读完立即删除。已有直连记忆的会话不覆盖，可重复运行。脚本只打印长度和检查结果，不打印摘要内容；摘要里出现 wxid 或群号则不导入。
- 结果：4 个私聊全部导入（1 个由会话自己整理 397 字；3 个已归档，分别从 6、4、2 条记录整理成 226、202、60 字）。
- 发现：这 3 个私聊在 OpenClaw 里已被归档，此前这些用户发私聊时 OpenClaw 会报错（回"AI 暂时无法回复"）。改走直连后不再依赖这些会话。注意直连失败时的 OpenClaw 回落对它们同样无效。

## 验证

- 新增 `work/test_chat_memory.py` 15 项（按会话隔离、epoch、整理阈值、强制整理、导入不覆盖、清除、提示顺序与缓存前缀、开关、ask_private、写回与失败不影响、群聊不记、/compact /clear、两轮端到端）；完整回归 227 项通过；pyflakes 无输出。
- 真实 API 冒烟（测试 ID、临时目录，未发微信）：第三轮"还有别的吗"接上电影话题，第四轮答出"小林 学摄影的"；强制整理 234 → 140 字，摘要保留称呼、近况和没聊完的话题，并标出未确认的部分。
- 备份 `work/backup_step3_2026-10-30/`（含 state.json）；15:54 重启桥接。尚未提交到 GitHub。

## 补充：私聊切回 OpenClaw（16:07）

- 用户反馈私聊直连后触发了微信安全警告，按要求把 `direct_chat_private` 改回 false 并重启；群聊仍走直连。改前配置备份为 `work/backup_step3_2026-10-30/config.before-private-revert.json`。
- 直连记忆文件保留未删（`work/wechat_ai_bridge_state/chat_memory/`），重新打开开关即可继续使用；切回期间 OpenClaw 上的私聊对话不会写进这些文件。
- 警告原因尚未确认：两种方案都通过同一个 WeChat Hook 发送，可量化的差别是直连回复更快（约 1 秒，对 OpenClaw 约 4 秒）。
- 遗留问题：有 3 个私聊的 OpenClaw 会话已归档，切回后这些用户发私聊仍会收到"AI 暂时无法回复"，需另行处理。

## 补充：私聊恢复直连（16:16）

- 查明警告原因：主号在手机上收到的是"非法插件"警告。本机微信数据目录显示主号最近一次在这台电脑登录是 10 月 27 日 21:21；`D:\Weixin` 安装目录里的 Hook（`version.dll`，9 月 19 日起）会被从该目录启动的任何微信加载，`start_dual_wechat.ps1` 的双开也会用 Hook 启动主号。警告最可能来自主号曾登录被注入的 PC 客户端，与直连无关：直连期间 Bot 只用 AI 小号经同一 Hook 接口发了 1 条 16 字回复，约 3.5 秒送达，与 OpenClaw 路径无可区分的差别。
- 按用户要求把 `direct_chat_private` 改回 true 并重启；16:07~16:16 没有私聊对话，直连记忆没有缺口。
- 建议（待用户确认）：手机上移除这台电脑的登录设备；主号不再用 `D:\Weixin` 登录；双开脚本改为只启动 AI 小号。
