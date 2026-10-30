# 群里引用 Bot 的消息时回复

日期：2026-10-30。

## 问题

群友引用 Bot 的消息回话、但没有 @ 它时，Bot 不回。原因：主流程只把"@大肥鱼 开头"和"引用卡片标题以 @大肥鱼 开头"当成对它说话；插话判断又只处理普通文字，引用卡片被直接丢弃。

## 修改

- `bridge.extract_group_reply_to_bot`：被引用消息的发送者 wxid（`<refermsg>` 里的 `<chatusr>`）等于 Bot 自己（`state["own_user"]`），或被引用者显示名是 `group_mention_names` 之一时，用卡片标题（群友新写的那句）作为请求；被引用的是图片时走看图流程。
- 任务带上 `reply_to_bot_quote`，提示写成"某某引用了你刚才说的「……」回你"。
- 插话判断的 `explicit` 同步认这种引用。

## 验证

- 新增 `work/test_quote_reply.py` 8 项；完整回归 186 项通过。备份 `work/backup_quote_reply_2026-10-30/`。
- 11:25 重启后，用户在测试群实测引用 Bot 的消息能收到回复。
