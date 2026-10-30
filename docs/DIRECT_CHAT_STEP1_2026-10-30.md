# 直连 DeepSeek 聊天：第 0、1 步

日期：2026-10-30。目标：去掉 OpenClaw agent 带来的助手腔和"解说腔"，最终让 Bot 不再依赖 OpenClaw。微信收发（读本地数据库 + WeChat Hook）不变。

## 第 0 步：清理画图残留

画图功能已于 2026-10-29 移除（见 BOT_DEEPSEEK_ONLY_2026-10-29.md）。本次删除无调用方的 `_send_group_edit_prompt` 与 `process_new_messages` 的 `drawing_dispatcher` 参数；`workflows/instruct_pix2pix_api.json` 移到 `work/retired_local_components/` 存档。`config.json.before-drawing-*` 等旧配置备份未动。

## 第 1 步：测试群闲聊走直连

- `chat_engine.py`：每次都是全新调用，消息顺序为 system（`persona_core.md`）→ 示范对话（`examples.json`，作为真实的 user/assistant 轮次）→ user（当前时间 + 最近群聊 + 场景 + 对方原话 + 防复读/语气提示）。system 和示范每次不变，可以吃到 DeepSeek 的前缀缓存。去掉"蓝鲸："名字前缀；5xx/网络错误重试一次。
- `persona_core.md`：约 1000 字，只写这只鱼是谁、具体的梗和雷点、怎么说话，规矩压成最后一段底线（不编事实、被认真问时如实说是 AI、不写动作描写）。`persona.md` 仍供 OpenClaw 使用，未改。
- `examples.json`：23 组示范，按情绪分组，每种 2~3 个说法，含群里看比赛起哄和认真问题的示范。改完即时生效，不用重启。
- `bridge.ask_chat`：只有 `direct_chat_peers` 里的会话、且是普通文字或图片/表情包回复时走直连；需要联网的（同 `_ask_openclaw` 的自动搜索判定）、群聊总结、引用评论/核实、指令仍走 OpenClaw。直连失败自动回落 OpenClaw。
- 配置：`direct_chat_enabled`、`direct_chat_peers`（当前只有 `<测试群ID>`）、`direct_chat_persona_path`、`direct_chat_examples_path`、`direct_chat_temperature`（1.3）、`direct_chat_max_tokens`（300）、`direct_chat_timeout_seconds`（30）。回退：把 `direct_chat_enabled` 改成 false 并重启。

## 已知差别

- 直连的回复不进 OpenClaw 会话；之后同群走 OpenClaw 的提问靠程序给的最近群聊知道上文。
- 示范会被模型照抄：实测"一光年多远"几乎原样复述了示范，已换成不常被问到的例子。示范宜写"说话方式"，少放具体知识。

## 验证

- 备份：`work/backup_direct_chat_2026-10-30/`（bridge.py、config.json、workflows）。
- 新增 `work/test_chat_engine.py` 12 项（按会话开关、稳定前缀、提示结构、名字前缀、空回复、重试、鉴权、路由与回落）。完整回归 178 项通过。
- 上线前用真实 API 跑了 5 条仿今天比赛群聊的输入（未发微信），回复比上午短、更像群友，但仍有个别偏分析的句子。
- 11:12 重启桥接，`bridge ready`。真实群内效果待用户验收；未提交或推送 GitHub。

## 补充：语气调整（11:37）

- `persona_rules.json` 新增 D4 解说腔密度规则（"还不如""不太现实""就不好办了""整局"等点评词连续出现）；上午 5 条回复由满分变为命中 3 处。
- `examples.json` 新增 5 组插话示范，u 侧用与真实提示相同的"【你没被点名，是看到某某这句话想插一句。】"格式，a 侧全是纯反应；共 28 组。`persona_core.md` 补一句"没被点名插一句时多半是一句反应、吐槽或接梗"。
- `chat_engine` 去掉模型偶尔输出的空表情代码 `[]`。

## 补充：直连联网工具（11:44）

- `chat_engine.complete(..., tool_runner=...)`：向 DeepSeek 提供 `web_search`、`web_fetch` 两个工具，按模型的工具调用循环执行；每轮最多 1 次搜索、2 次读网页（与原 agent_rules 一致），最多 3 轮，最后一轮不再提供工具以强制作答；工具失败时告诉模型"查询失败"而不是报错。查资料规则（`NETWORK_RULES`）追加在 system 末尾，前缀仍可缓存。回复带 `networked` 标记，沿用原有的联网状态显示。
- `bridge.searxng_results`：从 `summarize_search` 抽出的 SearXNG 查询，两边共用，`summarize_search` 行为不变。`bridge.chat_tool_runner` 用它和 `fetch_public_page` 实现工具，搜索后照旧写追问话题文件。
- `ask_chat`：`direct_chat_tools_enabled` 打开时，测试群需要联网的问题也走直连，由模型自己决定查不查；关键词判定命中时在提示里加"先查再答"。看图回复不给工具。关闭开关即回到"需要联网的交给 OpenClaw"。
- `/search` 指令、引用核实、群聊总结仍走 OpenClaw（第 2 步再迁）。
- 验证：新增 10 项测试，完整回归 197 项通过。真实 API + 本机 SearXNG 实测：赛事进度、天气各搜索 1 次（3~4 秒，天气没搜到当天数据时如实说没查到）；闲聊、TCP 握手不搜（约 1 秒）；给网址时读网页后概括。缓存命中约八成前缀。赛事回答里的具体日期未逐一核对。

## 补充：删除旧搜索代码

- 删除 `bridge.search_web`（Bing RSS 搜索，已无调用方）及只被它使用的 `xml.etree.ElementTree` 导入。所有搜索统一走 `searxng_results`（本机 SearXNG）。备份 `work/backup_remove_search_web_2026-10-30/`，回归 197 项通过。
