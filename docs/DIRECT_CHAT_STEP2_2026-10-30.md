# 直连 DeepSeek：第 2 步（群里剩下的功能）

日期：2026-10-30。目标：群聊里所有回复都不再经过 OpenClaw；私聊留到第 3 步。

## 修改

- `bridge.ask_model`：程序自己备好材料的请求（搜索归纳、群聊总结、引用评论、私聊引用图片）的统一入口。直连会话一次无工具调用，内容为"【现在】时间 + 请求 + 语气提示"，温度用 `direct_chat_task_temperature`；其他会话照旧走 OpenClaw，且 OpenClaw 提示（`prepare_model_prompt`）只在这条路径上生成，参数与原来一致。
- 迁到直连的群功能：
  - `/search` 与引用后核实：`summarize_search` 仍在本机 SearXNG 搜索、读网页，只把最后的归纳交给 `ask_model`；联网标记保留。
  - 群聊总结：`ask_model`。
  - 引用后评价：`ask_model`（不联网）。
  - 引用图片后再提问的文字部分：改走 `ask_chat`（按点名请求处理，可用搜索工具）。
  - 私聊引用图片的回复也改用 `ask_model`；私聊未切换，所以现在仍走 OpenClaw，参数不变。
- 群聊总结名字修复（原有问题，OpenClaw 路径同样受益）：`group_summary_prompt` 原来用发送者 wxid 当名字、且正文带 `wxid:` 前缀，真实 wxid 会进提示、可能出现在总结里。现在用 `_group_plain_message_text` 去前缀，并通过 `namer` 显示群昵称。
- 直连失败的兜底：新增开关 `direct_chat_openclaw_fallback`（当前 true，失败仍回落 OpenClaw，观察期保留这层保险）。设为 false 后：插话失败保持沉默，点名失败回"刚才没反应过来 再说一次？"，不再调用 OpenClaw。第 4 步删除 OpenClaw 前先关掉它。

## 现状

- 群聊：所有回复都走直连（失败时按上面的开关回落）。
- 私聊：仍全部走 OpenClaw（`process_new_messages` 的两处调用），第 3 步处理。
- 群里的 `/compact`、`/reset` 仍操作 OpenClaw 会话；直连群聊本身不保存会话，这两个指令对群聊已无实际作用，第 3、4 步一起整理。

## 验证

- 新增 `work/test_direct_step2.py` 9 项（直连/旧路径、三种兜底、总结/引用评论/搜索归纳路由、总结不含 wxid）；完整回归 212 项通过；pyflakes 无输出。
- 端到端实测（真实 `process_group_task` + DeepSeek + SearXNG，只替换发送函数，OpenClaw 被调用即报错）：`/search` 赛果 2.7 秒、群聊总结 0.9 秒（显示昵称）、引用评价 0.9 秒、引用核实谣言 2.9 秒，均未调用 OpenClaw。
- 备份 `work/backup_step2_2026-10-30/`；15:41 重启桥接。尚未提交到 GitHub。
