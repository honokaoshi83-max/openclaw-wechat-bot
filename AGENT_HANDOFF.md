> 2026-10-29 更新：当前已迁移为仅 DeepSeek API，移除本地模型、生图、改图和相关模式命令。以下为历史记录，当前以 docs/BOT_DEEPSEEK_ONLY_2026-10-29.md 为准。

# AGENT_HANDOFF.md — 微信 AI Bot（OpenClaw + llama.cpp）交接说明

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


> 读者是**下一个接手这个项目的 AI agent**（Codex / DSH 等）。
> 目标：读完这一份就能安全地排查和修改，不重复踩已经踩过的坑。
> 人类向的概览见 `总结.md`；历史全过程见 `OPENCLAW_WECHAT_PROJECT_STATUS.md`（很长，只在需要考古时看）。

最后更新：2026-09-20 00:35（Asia/Tokyo）

---

## 0. 30 秒上手

```powershell
cd "C:\Users\12280.DESKTOP-78TJQHB\Documents\Codex\2026-09-18\https-chatgpt-com-share-6aad3a2a-23d4"

# 1) 现在活着吗？
Get-Content .\work\wechat_ai_bridge_state\watchdog.log -Tail 5
Get-Content .\work\wechat_ai_bridge_state\bridge.log   -Tail 20
foreach ($p in 30001,8080,18789) {   # 注意：Get-NetTCPConnection 看不到 WSL 的 18789
    $c = New-Object System.Net.Sockets.TcpClient
    try { $c.Connect('127.0.0.1', $p); "OK   $p" } catch { "DOWN $p" } finally { $c.Close() }
}

# 2) 改完代码必须跑
.\work\.venv\Scripts\python.exe -m unittest discover -s work -p 'test_*.py'   # 60 项
.\work\.venv\Scripts\python.exe work\verify_openclaw_turn.py                  # 真实链路自检
```

判定「一切正常」：守护心跳全是 `True` + `bridge.log` 里有 `bridge ready direct_chats=4`
+ 上面三个端口 OK + 私聊 fast 模式 1～3 秒有回复。

**绝对不要**在这个 agent 会话里直接启动长期服务（原因见 §4 头号坑）。

---

## 1. 这个项目在干什么

把微信小号「大肥鱼」做成一个 AI 账号：群里 `@大肥鱼 ...` 或私聊它，由本地
llama.cpp（Qwen3.5-9B）或 DeepSeek 生成回复，用微信 Hook 的原生接口发出去。

| 组件 | 位置 | 说明 |
|---|---|---|
| 微信 AI 小号 | `D:\Weixin\Weixin.exe` | Hook 端口 **30001**，账号 `<AI账号wxid>` |
| 微信主号 | 同一安装，端口 **30002** | 只用于人工登录/双开 |
| Hook DLL | `D:\Weixin\version.dll` | 自编译，含自定义 `/SendTextMsg_NoSrc`；源码 `work\WechatHook4.1.10.27-fixed`；SHA256 `9AA49BCE9E016F8E514FAB70DE81BD9C1D5DE0334BBBDC588BD38B65254FF690` |
| 桥接 | `outputs\WeChatAI\bridge.py` | 轮询数据库、调模型、发消息（核心，~1200 行） |
| 配置 | `outputs\WeChatAI\config.json` | 账号、模型、端口、阈值 |
| 守护 | `outputs\WeChatAI\wait_for_ai_wechat.ps1` | 看护 Hook 端口 / 桥接 / 8080 / 18789 |
| OpenClaw Gateway | WSL 发行版 `OpenClawGateway`，端口 **18789** | systemd 用户服务 `openclaw-gateway.service`，版本 2026.9.4 |
| 本地模型 | `C:\llama\llama-b11046-bin-win-vulkan-x64\llama-server.exe` | `127.0.0.1:8080`，Vulkan，`-c 32768`，`--parallel 1` |
| 视觉编码器 | `C:\llama\models\mmproj-...-BF16.gguf` | 图片识别用 |
| 微信库 | `work\wechatauto-replica`（v1.2.2.4，commit `f4fdf736`，**有本地改动**） | SQLCipher 解密读库 |
| Python 环境 | `work\.venv`（3.12.10） | 所有脚本用它 |

---

## 2. 运行架构（必须理解的 5 条）

1. **发送链路**：`bridge.py` → Hook `POST http://127.0.0.1:30001/SendTextMsg`（群 @ 用
   `/SendTextMsg_NoSrc` + `atlist`）→ 再从数据库核验消息真的发出去了（`reply verified`）。
2. **接收链路**：不监听事件，**每 2 秒轮询**每个会话的 `sort_seq` 水位（`state.json` 的 `last_seq`）。
3. **群队列**：`GroupTaskDispatcher` 给每个群一个 FIFO 线程；轮询线程只入队，不等待模型。
4. **模型调用**：`ask_openclaw()` → `subprocess` 调 `wsl.exe ... openclaw agent --json`。
   会话键 = `f"{session_key_prefix}:{sha256(peer)[:24]}"`，加 `epoch` 后是
   `sha256(f"{peer}#{epoch}")`（`/reset` 就是 epoch+1）。
5. **守护**：`wait_for_ai_wechat.ps1` 每 10 秒检查；Hook 端口掉线就把桥接停住，恢复后再拉起；
   8080/18789 掉线就调 `startup_backend.ps1`。计划任务 `Codex-WeChat-AI-Bridge` **每分钟**重跑
   `startup_backend.ps1` 做自检。

---

## 3. 命令与状态清单

### 启停

```powershell
cd outputs\WeChatAI
.\start.ps1            # 起桥接（stdout/stderr 重定向到 bridge.out/err.log）
.\stop.ps1             # 停桥接 + 停守护（否则守护 10 秒后把它拉回来）
.\stop.ps1 -KeepWatchdog   # 只停桥接（守护脚本内部用）
.\start_watchdog.ps1   # 脱离会话启动守护（WMI）
.\stop_watchdog.ps1    # 停守护
.\enable_autostart.ps1 # 注册/重建计划任务（登录触发 + 每 1 分钟自检）
.\disable_autostart.ps1
```

### 微信里可用的命令（私聊 + 群聊）

`/qwen` `/deepseek` `/think` `/fast` `/help` `/compact` `/reset`
（`/compact` 压缩会话上下文并回报 tokens；`/reset` 换新会话）

### 运行期文件（`work\wechat_ai_bridge_state\`）

| 文件 | 用途 |
|---|---|
| `bridge.log` | 主日志（incoming / generation / reply verified / group reply verified） |
| `watchdog.log` | 守护：启停、心跳、自愈 |
| `startup.log` | 后端自检（llama / Gateway / 守护） |
| `bridge.err.log` / `bridge.fault.log` | 未捕获异常栈 / 原生崩溃栈 |
| `state.json` | 每个会话的水位、模型、thinking、`session_epoch`、每日公告日期 |
| `bridge.pid` / `watchdog.lock` | 进程 PID（守护据此判断存活） |
| `db_cache/`, `group_db_*/` | 解密后的数据库缓存（可删，重建需几十秒） |

### 健康判断标准

- `watchdog.log` 每 5 分钟一条 `heartbeat hook=True bridge=True llama=True gateway=True`
- `bridge.log` 出现 `bridge ready direct_chats=N`
- 私聊 fast 模式：**1～3 秒**；think 模式：**几十秒到几分钟**（正常，见 §4）
- 群聊单条：上下文正常时 **< 10 秒**

---

## 4. 已知坑（都是真金白银换来的，别再踩）

**① 绝对不要从 agent 会话里启动长期服务。**
2026-09-19 23:36 桥接无声消失、没有任何栈：它是 Codex Desktop app-server 的子进程，app-server
退出时整棵进程树被 `TerminateProcess`。**必须**用计划任务或 `start_watchdog.ps1`（WMI）。
判断方法：看父进程是不是 `WmiPrvSE.exe` / `svchost.exe`，而不是 `powershell.exe`/`python.exe`。

**② 控制台黑框闪烁。**
`wsl.exe`、`tasklist` 等都是控制台程序，无控制台的宿主启动它们会新建控制台。
`bridge.py` 的 `apply_windowless_spawn()` 给所有 `subprocess` 默认加
`CREATE_NO_WINDOW` + `stdin=DEVNULL`，`show_console_window=false` 隐藏桥接自身窗口。
**新增任何 `subprocess` 调用都不要去掉这两个参数。**

**③ 上下文超预算 = 每条消息等几分钟。**
网关预算 = `contextTokens(32768)` 的 75% = **24576**。会话超过后，**每轮**先跑一次压缩，
压缩有 150 秒内部截止时间，失败就白等 150 秒，然后再回答。日志特征：

```
[context-pressure-diagnostic] estimatedPromptTokens=31120 promptBudgetBeforeReserve=24576
[compaction-diag] trigger=budget outcome=failed detail=Command_reached_its_deadline durationMs=150119
```

处置：微信里发 `/compact`（带外压缩只要 4 秒），或 `/reset`。桥接在单轮 >90 秒时会自动压缩
（`automatic session compaction peer=... tokens=...->...`）。
查看会话：`wsl.exe -d OpenClawGateway -u openclaw -- openclaw sessions list --agent wechat-public --json --limit all`

**④ 本地模型 think 模式天生慢。**
实测同一条「测试」：think **88 秒**、fast **3 秒**。模型本身不慢
（decode 62 tok/s、prefill 1403 tok/s，`work\diag_model_speed.py` 可复测）。
用户抱怨慢时，先问是 think 还是 fast。

**⑤ `str.format` 模板必须渲染后再发。**
`DAILY_HELP_TEMPLATE` 里有 `{year}/{month}/{day}`。曾经 `/help` 分支直接返回裸模板，
用户看到字面的 `year年month月day日`。**统一用 `render_daily_help()`**；测试里有 `ast` 检查禁止
`x = DAILY_HELP_TEMPLATE`。

**⑥ 改函数签名要同步所有调用点。**
曾经给 `ask_openclaw()` 加 `epoch` 时只改了调用点没改签名，运行时报
`TypeError: ask_openclaw() got an unexpected keyword argument 'epoch'`，被兜底 `except` 吞掉，
用户看到的是「AI 暂时无法回复」。测试里有 `ast` 调用点校验 + `work/verify_openclaw_turn.py` 真实链路自检。

**⑦ PowerShell 细节。**
计划任务用 Windows PowerShell 5.1：读 UTF-8 无 BOM 的 `config.json` 必须写
`-Encoding UTF8`（否则按 ANSI 解码，中文项解析失败）；`"$user: $task"` 这种写法会因为
`$user:` 被当成驱动器报语法错，要写 `"$user : $task"`。

**⑧ 端口探测不能用 `Get-NetTCPConnection` 查 WSL 端口。**
WSL 转发的 `18789` 不在其列表里，`wait_for_ai_wechat.ps1` / `startup_backend.ps1` 用的是真实
TCP 连接（`Test-PortListening`）。

**⑨ `wsl.exe` 跨发行版调 Windows 侧 exe 时参数是 Linux 语法。**
例如要以 Windows 用户跑 PowerShell，用 `wsl.exe -d <distro> -- /mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe -Command ...`。

---

## 5. 修改守则

1. **先读** `outputs\WeChatAI\README.md`（人类向功能说明）+ 本文件的 §4。
2. **只改桥接逻辑**就动 `bridge.py`；改完**必须**跑单元测试 + `verify_openclaw_turn.py`。
3. 改完 `bridge.py` 要**重启桥接**才生效：杀 PID（守护 10 秒内自动拉起），或 `stop.ps1` + `start.ps1`。
   ```powershell
   $p=[int](Get-Content work\wechat_ai_bridge_state\bridge.pid); Stop-Process -Id $p -Force
   Start-Sleep -Seconds 20; Get-Content work\wechat_ai_bridge_state\bridge.log -Tail 3
   ```
4. **重启前先看有没有在飞的任务**：`Get-CimInstance Win32_Process | Where-Object { $_.ParentProcessId -eq $p -and $_.Name -eq 'wsl.exe' }`，
   有的话等它结束，否则用户会收到一次失败提示。
5. 改 `work\wechatauto-replica` 前记住它**有本地改动**（`db.py` 的 `stdin=DEVNULL`），
   上游固定 commit `f4fdf73621f667bb68df221c4301be7222770bf6`，升级要重新打这个补丁。
6. 不要提交/分享 `work\wechat_ai_bridge_state`（含数据库密钥缓存）和 `state.json`（含 wxid）。
7. 结论必须有日志或实测支撑；**不要凭猜**说「应该好了」。本项目的每个坑都是靠日志/实测定位的。

---

## 6. 测试与自检

| 命令 | 作用 | 期望 |
|---|---|---|
| `.\work\.venv\Scripts\python.exe -m unittest discover -s work -p 'test_*.py'` | 单元测试 | `Ran 60 tests ... OK` |
| `.\work\.venv\Scripts\python.exe work\verify_openclaw_turn.py` | 真实打一轮 qwen/deepseek | 各返回 `收到`，1～3 秒 |
| `.\work\.venv\Scripts\python.exe work\diag_model_speed.py` | llama-server 速度基线 | decode ~62 tok/s（首条含 prefill，看第 2 条更准） |
| `wsl.exe -d OpenClawGateway -u openclaw -- journalctl --user -u openclaw-gateway.service -n 50` | 网关日志（压缩/上下文/模型调用） | 无 `compaction failed` |

测试覆盖的关键不变式（改代码时别破坏）：
群 @ 解析与 `<refermsg>` 防误触发、群队列顺序与并行、70 条总结上限与转写截断、
`/compact` `/reset` 语义、会话 key/epoch、help 日期渲染、`CREATE_NO_WINDOW` 默认、
`ask_openclaw` 调用点与签名一致。

---

## 7. 常见症状 → 排查路径

| 症状 | 先看 | 大概率原因 |
|---|---|---|
| 完全不回复 | `watchdog.log` 是否有重启；`bridge.log` 是否还在写 | 进程被杀（§4①）/ 微信没登录（Hook 端口掉线，守护会主动停桥接） |
| 回「AI 暂时无法回复」 | `bridge.log` 里 `agent failed` 的栈 | 代码异常（§4⑥）或 OpenClaw 报错 |
| 回复要几分钟 | 网关 journal 的 `compaction`、`estimatedPromptTokens` | 上下文超预算（§4③） |
| 私聊慢、群聊更慢 | 当前是 think 还是 fast（`state.json`） | think 模式本身慢（§4④） |
| 群聊不回复但私聊正常 | `bridge.log` 有无 `incoming group=... mention` | @ 名称不匹配（`group_mention_names`）/ 引用消息 XML 被正确忽略 |
| 图片不识别 | `8080` 是否在线；`bridge.log` 的 `image processing failed` | 未用 `/qwen`；图片 AES 密钥未初始化（`initialize_image_key.py`） |
| 命令符黑框 | 桥接进程树里是否出现新的 `conhost.exe` | 有子进程没走 `apply_windowless_spawn()`（§4②） |
| `/help` 日期不对 | `render_daily_help()` 是否被绕过 | 裸模板泄漏（§4⑤） |

---

## 8. 需要人来做的事（agent 做不了）

- **微信必须人工登录**（AI 小号 + 主号双开），Hook 端口才会出现。双开脚本：
  `outputs\WeChatAI\start_dual_wechat.ps1 -Restart`。
- **发送权限**：`send_mode=hook` 依赖微信进程在跑；换微信版本后要重编译 Hook（`install_hook.ps1`）。
- **图片识别首次初始化**：`work\.venv\Scripts\python.exe outputs\WeChatAI\initialize_image_key.py`，
  然后人工在微信里点开一张图片。
- **不要在未经用户同意的情况下**替用户向群里发消息或改动 `state.json` 的 `last_seq`。
