# Claude Code 微信 Bot 项目审查报告

日期：2026-10-29（东京时间）
来源：Claude Code 独立会话 ce6c84f2-f635-493b-a76f-7f2f5638120c。以下为其最终报告原文；只读静态审查，未运行测试，也未实施修复。

---

# OpenClaw 微信 Bot 只读审查报告

## 审查说明
- **没有执行任何测试或验证。** 没有运行单元测试，没有启停服务，没有发送微信，也没有修改、提交任何文件。下文"验证办法"都是建议，需要你来执行。
- **读了哪些文件：**
  - 文档：10-09 状态文档、AGENT_HANDOFF.md、README.md、总结.md，以及 10-06 的绘图、改图、色彩、人设、延迟专项记录。
  - 代码：bridge.py 全文（2958 行）、vision_media.py、vision_client.py、image_generation.py、drawing_reference.py、local_edit_flow.py、image_edit_state.py（只看了意图匹配部分）、master_switch.psm1、manage_bot_stack.ps1、startup_backend.ps1、wait_for_ai_wechat.ps1、start.ps1、stop.ps1、start_watchdog.ps1、enable_autostart.ps1、stop_llama_for_image_edit.ps1、agent_rules.md。
  - 为核实 GPU 共享，还读了 `D:\AI\QwenImage21\generate.py` 第 40–68 行。
  - 测试：用 grep 统计了 work/test_*.py，共 147 个 `test_` 函数，其中 test_bridge.py 有 103 个。
- **日志：** bridge.log 只做了计数，外加读了 1 段 10-09 的 traceback（第 52008–52029 行）。报告里不出现联系人、群 ID 或消息内容。没有读凭据、数据库、二维码或附件。config.json 只 grep 了非敏感的开关和超时字段。
- **标注：** 【证实】表示代码路径可以直接推出缺陷；【待验证】表示需要实测确认；"过时文档"单列在最后。

---

## 前三优先项
1. **P0-1：全局 `db_access_lock` 把模型推理包在锁里。** 整个 Bot 实际是串行的，绘图期间来一条千问请求就会冻结全部会话。
2. **P0-2：用户原文经 `wsl.exe -- … --message <prompt>` 传给 WSL。** 需要先安全确认它会不会被 Linux shell 解释。如果会，就是可远程执行命令的问题。
3. **P1-1：单条消息出异常会让整轮轮询失败。** 接着进入"离线恢复"，丢弃所有私聊积压，还可能发出误导性的"已上线"通知。日志里有一次实际证据。

---

## P0

### P0-1【证实】全局 DB 锁包住了模型调用和 GPU 等待，所谓"独立队列"失效
- **位置：**
  - bridge.py:2908-2909：群 worker 在 `with db_access_lock:` 里执行整个 `process_group_task`，其中包含 `ask_openclaw`。
  - bridge.py:2932-2942：主轮询也在同一把锁里执行 `process_new_messages`。私聊的模型调用直接在轮询线程里跑（2596、2674 行）。
  - bridge.py:1552：千问调用要再拿 `GPU_INFERENCE_LOCK`。
  - image_generation.py:179、228：绘图在整个生成期间都持有 `GPU_INFERENCE_LOCK`。
- **证据：** 只要群任务在推理（OpenClaw 超时 210 秒；截断重试时还会加上最长 930 秒的压缩），主轮询就拿不到锁，其他群的 worker 也拿不到。绘图期间，任何千问请求都会"持有 DB 锁、等待 GPU 锁"，时长约等于一次生图（文档记录约 239 秒以上）。
- **触发条件：** 群聊 think 模式、搜索、总结；或者绘图时有人用千问发消息。
- **影响：**
  - 所有私聊和群聊停止轮询，DeepSeek 用户也一起等。
  - 绘图回执延迟。
  - 改图 5 秒窗口、表情合并 3 秒窗口这类依赖时效的逻辑受到干扰。
  - 文档里"轮询线程只入队，不等待模型""排队的千问请求继续处理"的说法与实际不符。
- **修复建议：**
  - 把 DB 锁缩小到"读消息 / 发送 / 核验"本身，模型和视觉调用放到锁外。
  - 私聊也改为按会话分发的队列。
  - 缩小 DB 锁后，`state` 的修改和 `save_state` 需要单独加锁。目前其实是靠这把大锁顺带串行化的，直接缩锁会引入 dict 并发修改问题。
- **验证办法：**
  - 单元测试：mock `ask_openclaw` 阻塞在一个 Event 上，断言另一个会话的消息仍然能被处理。
  - 实测：绘图期间让一个 DeepSeek 私聊发消息，看日志里的 incoming 时间戳。

### P0-2【待验证·高危】prompt 作为 `wsl.exe --` 的命令行参数传入，可能被 Linux shell 解释
- **位置：** bridge.py:1490-1498 拼出 `wsl.exe -d OpenClawGateway -- openclaw agent … --message <prompt>`；1571、1585 行执行。prompt 里包含微信用户原文、引用文字、群聊记录和网页正文。
- **证据：** 据我理解，`wsl.exe --` 后面的命令行会交给默认 shell，只有 `--exec/-e` 会绕过 shell。这一点我没有实测。另一方面，日志显示普通消息都能正常完成，说明常见输入没有被破坏；但这不能排除 `$(...)`、反引号、`$VAR` 被展开。
- **触发条件：** 任何能私聊 Bot 或在群里 @ 它的人，发送含 shell 元字符的文字。
- **影响：** 如果成立，就是以 WSL 默认用户身份执行任意命令。另外，Windows 命令行约有 32K 字符上限，长引用加网页证据可能超限。
- **修复建议：** 改用 `wsl.exe -e`（`--exec`）。更好的做法是通过 stdin 或临时文件传 prompt，或者直接调用 Gateway 的 HTTP API。
- **验证办法：**
  - 在 Python 里用列表形式执行无害命令：`subprocess.run(["wsl.exe","-d","OpenClawGateway","--","printf","%s\n","a$(echo X)b"])`。输出 `aXb` 就说明被 shell 解释了。
  - 再测一次含换行、不含空格的参数。
  - 测试时不要发微信。

---

## P1

### P1-1【证实】单条消息异常会触发"离线恢复"，丢弃所有私聊积压
- **位置：**
  - bridge.py:2339-2340：先保存水位，再处理消息（最多处理一次）。
  - 2606：私聊识图只捕获 `ImageUnavailable/VisionError/OSError/KeyError`。`ask_openclaw` 抛出的 `RuntimeError`、`subprocess.TimeoutExpired`、`json` 错误都会向上冒。
  - 同样没有兜底的地方：引用识图里的 `ask_openclaw`（565 行）、静默期通知发送（2504）、改图通知（98-101、2718-2721）、群聊"修图已停用"提示（2373）。
  - 2944：任何异常都会把 `was_offline` 设为 True。
  - 1741-1777：离线恢复直接推进水位，丢弃消息内容，而且 1768 行在发送之前就保存了状态。
- **日志证据：** 2026-10-09 11:51:53，离线恢复时 Hook 返回 `ret=-2 not logged in`，抛出 poll failed；下一轮显示 `online recovery complete notices=0`。也就是说，该会话的积压已被推进，通知却丢了。
- **触发条件：** 千问超时、OpenClaw 返回非 JSON、Hook 短暂拒绝等。
- **影响：** 所有私聊里尚未处理的消息被静默丢弃，或者收到一句"已上线"，却没有任何回答。
- **修复建议：**
  - 每条消息单独 try/except，记录失败并给出提示。
  - 只有 DB 或 Hook 连接级故障才进入离线模式。
  - 离线恢复时，等通知发送成功后再持久化，或者记录待补发的通知。
- **验证办法：** 单元测试：识图路径的 `ask_openclaw` 抛出 `TimeoutExpired`，断言同批次的其他消息和其他会话仍然被处理，并且没有调用 `process_offline_messages`。

### P1-2【证实】引用图片 / 附近图片选成了优先级最低的候选
- **位置：** bridge.py:531 按（同一发送者、距离、前后）升序排序，最佳候选在最前面；但 546、591、2067 行用的是 `candidates[-1]`。
- **触发条件：** md5 精确匹配失败，并且请求前后各 2 条范围内有多张图。比如群里有别人在请求之后、worker 执行之前发了图。
- **影响：** 识别错图片；把其他成员的图片内容回复到群里。
- **修复建议：**
  - 改成 `candidates[0]`。
  - "之后"的候选只接受同一发送者，并限制时间窗口。
  - md5 正则只对内容做了一次 `unescape`，群聊 refermsg 如果是双重转义可能匹配失败（【待验证】），建议循环反转义。
- **验证办法：** 构造"同一发送者的图在请求前、他人的图在请求后"的历史记录，断言选中的是前者。目前的测试没有覆盖 `nearby_visual_messages` 的选择顺序。

### P1-3【证实】所有群任务超过 45 秒都会被提示"图片识别超时"
- **位置：** bridge.py:2882-2904。所有群任务都会启动这个计时器，不区分是否视觉任务；config 中 `group_visual_timeout_seconds` 为 45。
- **触发条件：** 非绘图期间，群里的 think 模式、搜索、总结、压缩任务，或者等 DB 锁（见 P0-1）超过 45 秒。
- **影响：**
  - 先误报"图片识别超时，请稍后重发"，稍后真正的回复又发出来，用户会重发。
  - 计时器在锁外使用同一个 `group_db` 对象，与 worker 并发访问 DB 缓存。
  - `timed_out` 被设置了但没有任何地方使用。
- **说明：** 当前 bridge.log 里还没有出现过这条日志。
- **修复建议：** 只给视觉任务启动计时器；文案改为"处理较慢"；发送前拿锁，或者使用独立的 DB 实例。
- **验证办法：** 单元测试：mock 一个耗时 60 秒的文字群任务，断言不会发出超时文案。

### P1-4【证实】图片已生成，但千问 90 秒内没恢复，就按失败处理
- **位置：** image_generation.py:249-255。先 `wait_for_text_backend()`（90 秒），不成功就抛异常，然后才检查退出码和 PNG。bridge.py:2286-2291 随后发送"生图失败"。
- **触发条件：**
  - llama 加载超过 90 秒。
  - 计划任务设置为 `IgnoreNew`（enable_autostart.ps1:21），实例正在运行时 `Start-ScheduledTask` 会被忽略，只能等下一分钟。
  - `-hf` 启动时可能做联网检查（startup_backend.ps1:106，【待验证】）。
- **影响：** 用户等了约 4 分钟后收到失败提示，实际已经生成好的图片被丢弃。
- **修复建议：** 先校验并返回图片，文字后端没恢复只记 warning 或另行提示。
- **验证办法：** mock `wait_for_text_backend` 返回 False，并提供一个合法 PNG，断言流程成功。

### P1-5【证实】GPU 锁里做了联网和长时间压缩
- **位置：**
  - bridge.py:1552-1567：千问调用持有 `GPU_INFERENCE_LOCK` 期间，会执行自动联网（SearXNG 8 秒，加网页读取 3 秒）。
  - 1568：持锁期间查询 sessions（5 秒）。
  - 1582-1592：截断后重试时，压缩最长 900+30 秒，再加 210 秒重跑。
  - 1381-1384：对 DeepSeek 会话执行 `/compact` 也会拿 GPU 锁。
- **影响：** 绘图被阻塞；叠加 P0-1 后全局冻结时间更长。
- **修复建议：** 只在真正访问 llama 时持有锁；DeepSeek 不拿锁；压缩超时缩短到分钟级。
- **验证办法：** 在日志里加锁等待和持锁时长；单元测试断言联网阶段不持有 GPU 锁。

### P1-6【待验证】OpenClaw agent 的工具权限和提示注入
- **位置：**
  - README.md:189-191 的示例配置是 `"profile": "coding"`。
  - agent_rules.md:21 只用文字约束"Do not execute local commands…"。
- **注入来源：**
  - 群聊记录：bridge.py:737-752。
  - 引用文字：2034-2036。
  - 网页正文：979。
  - 图片 OCR 描述：596。
- **影响：** 如果 wechat-public 实际具备执行命令或文件读写工具，任何群成员都可以尝试诱导读取或修改本机内容。
- **修复建议：** 设置明确的 allowlist，只保留 `web_search` 和 `web_fetch`，拒绝 exec、fs、memory 写入；不可信内容用定界符包裹并降低权限。
- **验证办法：** 查看生效的工具列表（注意不要打印凭据），并在离线会话里做红队提示测试，不要发到微信。

---

## P2

1. **【证实】静默时段的私聊会收到"当前模型已上线，如有需求可以现在开始对话"。**
   - 位置：bridge.py:2501-2509、105。
   - 现象：00:00–08:00 回复"已上线"，之后的消息却被静默忽略，08:00 后也不会重放。
   - 建议：改用专门的离线文案。

2. **【证实】群聊改图意图和新的本地改图流程冲突。**
   - 位置：bridge.py:2370-2375；当前配置为 `image_edit_enabled=false`、`local_image_edit_enabled=true`。
   - 现象：local_edit_flow.py:6 只完整匹配"改一下图片"之类的短语，所以"@Bot 帮我改图，把衣服改红"会被回复"本机修图功能已停用"。
   - 另一个问题：local_edit_flow.py:33 在 5 秒等图窗口内，会吞掉同一发送者的任何非图片消息，包括新的 @ 请求。

3. **【待验证】Hook 发送是异步的，日志里是 `queued`，桥接只用 12 秒（文字）或 15 秒（图片）在 DB 里核验。**
   - 位置：bridge.py:1687-1695、2240-2249。
   - 风险：慢的时候被误判为失败，于是出现"成功的回复加失败提示"同时发出，或者私聊分段中途停止（2709）。

4. **【证实】截断重试会把同一条 prompt 再提交到同一会话，用户这一轮在上下文里重复出现。**
   - 位置：bridge.py:1585。

5. **【证实】会话隔离的语义有偏差。**
   - 群聊共用一个会话和一个搜索话题文件（896-913），B 的短追问可能沿用 A 的话题。
   - 隔离提示里写的是"与当前用户单独对话"（135-139），在群聊里不成立。
   - 群总结把 `sender_username`（wxid）直接写进 prompt（746），选 DeepSeek 时会发到云端，模型也可能把它输出到群里。

6. **【证实】隐私残留。**
   - bridge.log 不轮转（2801 行；当前已超过 5 万行），而且记录请求前 80 个字（2463/2480/2491）和搜索 query（963/1023）。
   - `search-topic-*.json` 从不清理（986-988）。
   - state.json 保存了改图消息的完整 dict（local_edit_flow.py:16、31）。
   - Hook 解码失败时，已写入的临时图片不会删除（vision_media.py:59-65）。
   - DeepSeek 识图会把用户图片发到云端（vision_client.py:31-36），对用户需要说明。

7. **【待验证·高概率】在 PowerShell 7 下，`ConvertFrom-Json` 会把 ISO 日期字符串自动转换成 DateTime。**
   - 影响位置：master_switch.psm1:18-21 的启动标识比较总是不等，导致 manage_bot_stack 的 Stop 拒绝执行、Status 显示未禁用；startup_backend.ps1:63 可能解析失败，进而误删 GPU 锁。
   - 范围：现有启动器都固定用 5.1（BotSwitch.cs:192-193、start_watchdog.ps1:18），所以只影响手动用 pwsh 运行的情况。
   - 验证：用 `pwsh` 跑 tests/test_master_switch_state.ps1。

8. **【证实】锁文件的 PID 复用问题。**
   - watchdog.lock（start_watchdog.ps1:22-32、wait_for_ai_wechat.ps1:99-110）只检查"该 PID 是否存在"。
   - `Stop-Process -Force` 或重启后锁文件会残留；如果 PID 碰巧被其他进程复用，watchdog 和桥接就一直不会启动。
   - GPU 锁也有类似问题（startup_backend.ps1:64-65，最长影响 20 分钟）。
   - 建议：同时校验进程名和命令行。

9. **【证实】watchdog 主循环没有 catch（wait_for_ai_wechat.ps1:121-193），任何异常都会结束监管。**
   - 每分钟一次的计划任务能把它拉回来。
   - Hook 掉线时强杀桥接，内存里的群队列和绘图队列（bridge.py:672、image_generation.py:115）会被静默丢弃，不会通知用户。

10. **【证实】manage_bot_stack 的 Stop 不会停止 generate.py 和 sd-cli（manage_bot_stack.ps1:171-192）。** 关掉 Bot 后 GPU 仍然被占用到这次生图结束。

11. **【证实·低风险】URL 安全校验有缺口。**
    - URL 校验存在 DNS rebinding 的时间差问题（bridge.py:233-245、drawing_reference.py:50-62）。
    - 表情 CDN 下载没有校验重定向目标（vision_media.py:118-122）。

12. **【证实】其他耗时与代码卫生问题。**
    - 每条私聊文字如果是批次里最后一条，会持锁空等最多 3 秒来匹配表情包（bridge.py:2543、1064-1071），并且阻塞全局。
    - `prepare_vision_image` 的 docstring 写的是三帧，实际只取第 0 帧（vision_media.py:215-226）；GIF 第一帧可能是空白。
    - `search_web`（Bing）是死代码（819 行）。
    - 旧的 ComfyUI 改图路径仍然接在主流程里（1801-2001、2376-2428）。

---

## 过时文档（以代码为准）
- **AGENT_HANDOFF.md**
  - "60 项测试""~1200 行"（现在约 2958 行）。
  - "轮询线程只入队，不等待模型"，与 P0-1 不符。
  - "修图停用"，现在新的本地改图已启用。
- **总结.md**
  - "DeepSeek 模式提示不支持图片识别""图片识别仍只支持本地 Qwen"。
  - "Think 模式不会阻塞其他群或新消息"。
  - "60 项通过"。
- **README.md**
  - 前置条件和部署步骤仍然要求 Docker Desktop。
  - 10-04 段落写着"修图已停用"。
  - "70 项测试"。
  - "DeepSeek V4.1 Flash 高准确识图"，但配置标识只是 `deepseek-flash`。
- **BOT_STATUS 10-09**
  - "各会话有独立处理队列"：私聊其实是在轮询线程里直接处理的，群队列也被全局锁串行化了。
- **BOT_DRAWING 10-06**
  - 默认 512×512、20 步：现在配置是 768×768、28 步。
  - "数据库锁不在整个绘图期间持有"：只对绘图线程成立，其他线程会持有 DB 锁等 GPU。
- **代码注释**
  - bridge.py:925 写的是 "Docker SearXNG"。
  - DAILY_HELP 里"DeepSeek 高准确识图"的说法未经核实。

## 历史问题（不算当前缺陷）
bridge.log 里的 4336 次 poll failed，几乎都发生在 2026-09-19 到 09-20。10 月只有 1 次，就是 P1-1 引用的那一次。

## 建议的下一步（需要你执行）
1. 运行 `.\work\.venv\Scripts\python.exe -m unittest discover -s work -p 'test_*.py'`，确认当前基线。
2. 用 P0-2 里的无害命令确认 WSL 是否经过 shell 解释。
3. 再为 P0-1、P1-1、P1-2 补测试，然后再修。
