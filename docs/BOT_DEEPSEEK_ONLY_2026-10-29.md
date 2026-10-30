# 微信 Bot：仅保留 DeepSeek API

日期：2026-10-29（东京时间）。用户确认全部停用本地模型，并移除生图、改图入口。

## 已完成变更

- Bot 文字调用固定 DeepSeek；配置 models 仅剩 deepseek，新会话默认 DeepSeek。
- 历史会话 model、thinking 字段迁移为 deepseek、off；保留消息水位、会话 epoch 和既有历史。本次迁移 6 个会话。
- 移除千问模型选择、思考/快速模式命令和帮助内容；状态栏统一标注 DeepSeek，保留字数、耗时与实际联网标记。
- 图片、引用图片、表情包和 GIF 沿用原有媒体提取逻辑，视觉接口仅调用带鉴权的 DeepSeek API，没有本地回退。
- 移除本地绘图、改图分流、后台绘图队列、GPU 推理锁和本地翻译器。
- 移除旧改图待办状态。已取消的生成/改图测试和实现移到 work 下的 retired_local_tests、retired_local_components，作为不可执行的 .txt 存档。
- 启动与守护不再启动或检查本地模型端口，控制台不再显示本地模型和编辑器；BotSwitch.exe 已重新编译。
- OpenClaw 的 llama-cpp provider 和千问模型别名已移除，默认仅 DeepSeek；已对运行中的 Gateway 重载配置。
- 两份当前 README 已更新；旧项目总览标注为历史记录。

## 保留能力

- DeepSeek 私聊与群聊、会话隔离、原有人设、分段/群聊单段规则。
- 识图、引用文字评论/核实、原有媒体失败提示。
- 自然联网、搜索及追问、每日公告与夜间规则。
- /help、/search、/compact、/reset、/clear；保留 /deepseek 与“切换大肥鱼”兼容入口。
- 开机自启、手动登录微信、程序开关。

## 验证证据

- Python 回归：105 项通过，包含新增的单模型命令、历史迁移、实际请求构造、视觉鉴权和无本地后台依赖检查。
- 已退役功能的测试一并归档，测试数量减少不代表仍覆盖已移除的绘图功能。
- PowerShell startup guards、backend autostart、stack lifecycle 三项检查通过；启动、守护和管理脚本语法检查无错误。
- 控制台 C# 编译成功；本次未进行人工界面验收。
- OpenClaw models list 返回 count=1，唯一模型 deepseek/deepseek-flash，available=true、local=false。
- 一次隔离诊断会话的真实 OpenClaw → DeepSeek API 调用约 5.69 秒返回“DeepSeek API连接正常”；未发送微信，不代表平均时延。
- Bot 桥接与守护继续保持关闭，当前开机停止标记保留；没有自动恢复微信机器人接收消息。

## 边界

- 未删除磁盘上的共享模型权重、外部运行器或历史图片；这些文件不再被当前 Bot 或 OpenClaw 调用。没有为了清空间执行不可逆删除。
- DeepSeek 为远程 API，文字和识图图片会发送到服务端；具体视觉支持与准确率取决于服务返回。
- 本次没有修改并发队列、数据库大锁、异常恢复及候选图片排序等 Claude 审查报告中的其他问题，不能把此次迁移当成其全部修复。
- 本次未向微信发消息，也未做图片真实消息的完整端到端验收。
- 未提交或推送 GitHub。

## 回滚资料

- 修改前的文件备份位于 work/backups/deepseek-only-20261029-171011 与 retire-local-20261029-171152。
- 原会话状态备份位于 work/wechat_ai_bridge_state/state.before-deepseek-only.json，包含私有状态，不应上传。
- OpenClaw 配置保护备份位于 /home/openclaw/.openclaw/backups/deepseek-only-1793261645，权限受限，包含配置秘密，不应输出或上传。
- 退役代码、旧测试和旧 README 已存档，不属于当前活动功能。
