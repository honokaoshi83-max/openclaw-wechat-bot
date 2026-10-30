> 2026-10-29 更新：当前已迁移为仅 DeepSeek API，移除本地模型、生图、改图和相关模式命令。以下为历史记录，当前以 docs/BOT_DEEPSEEK_ONLY_2026-10-29.md 为准。

# OpenClaw 微信 AI 项目当前进度

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


> 更新时间：2026-09-19（Asia/Tokyo）  
> 当前结论：文字回复、模型切换、本地视觉识别、后台 Hook 发送、双开微信均已完成真实测试。

## 1. 项目目标

将微信小号“大肥鱼”作为独立 AI 账号。用户向该账号发送消息后，由本机 OpenClaw、llama.cpp 和微信 Hook 完成读取、推理与回复。

当前方案完全在本机运行本地 Qwen；DeepSeek 仍通过 OpenClaw 中配置的 DeepSeek provider。微信消息读取、图片解密和回复发送均在本机完成。

## 2. 当前运行架构

```text
主微信账号
  ↓ 私聊消息
AI 微信账号“大肥鱼”
  ↓ 本地微信数据库读取
WeChatAI bridge.py
  ├─ 文本 → OpenClaw → DeepSeek 或本地 Qwen
  └─ 图片 → WeChat Hook /Decode_Pic → llama.cpp Qwen 视觉 → OpenClaw 人设
  ↓
WeChat Hook /SendTextMsg
  ↓
AI 微信账号发送回复
```

### 端口分配

| 端口 | 用途 | 当前状态 |
|---|---|---|
| `30001` | AI 微信账号“大肥鱼”的 Hook | 已监听 |
| `30002` | 个人主微信账号的 Hook | 已监听 |
| `8080` | llama.cpp OpenAI 兼容接口及本地视觉模型 | 已监听 |
| `18789` | WSL 中的 OpenClaw Gateway | 由 systemd 用户服务管理 |

AI Hook 地址固定为 `http://127.0.0.1:30001`。不要将 AI 账号登录到 `30002`。

## 3. 账号与模型

- AI 微信账号目录：`<AI账号目录>`
- AI 微信账号内部 wxid：`<AI账号wxid>`
- AI 微信昵称：`大肥鱼`
- 当前主测试联系人：`<主账号wxid>`
- 默认模型：DeepSeek
- 当前测试会话模型：Qwen
- 当前测试会话模式：思考模式
- 本地 Qwen 模型：`HauhauCS/Qwen3.5-9B-Uncensored-HauhauCS-Aggressive:Q4_K_M`
- 本地视觉投影模型：`C:\llama\models\mmproj-Qwen3.5-9B-Uncensored-HauhauCS-Aggressive-BF16.gguf`

## 4. 已完成功能

### 微信桥接

- 从微信本地数据库读取新私聊消息。
- 通过微信 Hook 后台发送回复。
- 微信最小化时可以工作。
- Hook 模式不激活微信窗口、不抢鼠标。
- 已移除发送者白名单限制，可处理其他普通私聊联系人。
- 离线期间不读取或逐条回复旧内容；上线后统一回复：
  `当前模型已上线，如有需求可以现在开始对话`
- 消息状态和会话模型按联系人独立保存。

### 模型指令

| 指令 | 功能 |
|---|---|
| `/deepseek` | 切换到 DeepSeek |
| `/qwen` | 切换到本地 Qwen |
| `/fast` | Qwen 快速模式 |
| `/think` | Qwen 思考模式 |

DeepSeek 模式不处理图片，收到图片时回复切换 Qwen 的提示。

### 回复逻辑

- 回复末尾显示模式、输出文字数量和耗时。
- 拟人化分段常驻，但不强制固定四段。
- 短消息通常只回复一次或 1～2 句。
- 标点和成对引号不会被错误拆成独立消息。
- 回复提示词要求先判断用户意图，避免连续自言自语和模拟双方对话。
- 会过滤 OpenClaw 工作区注释、项目路径等内部上下文泄漏。

### 每日帮助

每天按东京时间，在每个联系人当天第一次对话时额外发送：

```text
时间：当天日期
大肥鱼0.1版本

使用帮助
切换deepseek模型：/deepseek
切换本地模型：/qwen
本地模型进入思考模式：/think
本地模型进入快速模式：/fast
```

### 图片识别

- 仅 Qwen 模式启用本地图片识别。
- 图片由 AI 账号 Hook 的 `/Decode_Pic` 在后台解密。
- 解密后的图片发送给本地 llama.cpp Qwen 视觉接口。
- 视觉描述再经过 OpenClaw 主人设生成最终回复。
- 临时图片在处理后自动删除。
- 图片解密失败会快速返回提示，不再阻塞轮询数分钟。
- 支持普通 JPEG/PNG；Hook 解出的 `wxgf` 会尝试通过 ffmpeg 转换为 JPEG。

## 5. 图片功能修复记录

原 `D:\Weixin\version.dll` 的 `/Decode_Pic` 会返回 `ret=0`，但不生成目标图片。已完成以下修复：

1. 安装 Visual Studio C++ x64 编译工具和 Windows 11 SDK。
2. 使用修正版 `WechatHook4.1.10.27` 源码。
3. 将项目工具集从 `v143` 重定向到本机的 `v145`。
4. Release x64 编译成功，0 个警告、0 个错误。
5. 安装新 DLL 到 `D:\Weixin\version.dll`。
6. 使用真实微信 `.dat` 图片验证 `/Decode_Pic`。

当前 DLL SHA-256：

```text
862A59CC4B407C47F7171BD3A5705734F4D88B93A305531E9FBCCC882FE4A7BC
```

旧 DLL 备份：

```text
D:\Weixin\version.dll.backup-20260919-211118
```

真实解码测试结果：输出标准 JPEG，大小 `89,818` 字节，图片可正常打开。

## 6. 最终验收证据

2026-09-19 21:17 的主账号真实图片测试：

- 桥接识别到 `kind=image`。
- Hook 解码成功。
- 本地 Qwen 视觉模型成功理解图片。
- OpenClaw 生成人设回复。
- Hook 连续发送 3 段回复。
- 总回复 72 字。
- 微信数据库确认发送成功。
- 图片临时目录处理后为空。

关键日志：

```text
2026-09-19 21:17:55,889 INFO incoming peer=<主账号wxid> seq=1789820274000 kind=image
2026-09-19 21:18:21,380 INFO WeChat Hook send peer=<主账号wxid> response={'queued': True, 'ret': 0, 'retmsg': 'queued'}
2026-09-19 21:18:23,520 INFO WeChat Hook send peer=<主账号wxid> response={'queued': True, 'ret': 0, 'retmsg': 'queued'}
2026-09-19 21:18:25,633 INFO WeChat Hook send peer=<主账号wxid> response={'queued': True, 'ret': 0, 'retmsg': 'queued'}
2026-09-19 21:18:26,699 INFO reply verified peer=<主账号wxid> seq=1789820274000 parts=3 chars=72 delivery_seconds=5.34
```

自动检查结果：

- Python 单元测试：39 项通过。
- Python 语法编译检查：通过。
- 后端自启契约检查：通过。
- 修正版 Hook Release x64 编译：通过。
- 真实图片解码：通过。
- 真实微信图片回复：通过。

## 7. 启动与日常使用

### 后端启动

```powershell
cd "C:\Users\12280.DESKTOP-78TJQHB\Documents\Codex\2026-09-18\https-chatgpt-com-share-6aad3a2a-23d4\outputs\WeChatAI"
.\startup_backend.ps1
```

该脚本负责：

- 启动 llama.cpp。
- 确认 OpenClaw Gateway systemd 服务。
- 启动 AI 微信守护程序。
- 不自动启动微信。

### 双开微信

微信必须在两个实例都关闭时同时启动：

```powershell
.\start_dual_wechat.ps1 -Restart
```

登录规则：

1. 第一个窗口登录 AI 账号“大肥鱼”，对应端口 `30001`。
2. 第二个窗口登录个人主账号，对应端口 `30002`。

如果只先登录一个账号、再顺序启动第二实例，微信单实例机制可能拦截第二次启动。

### 桥接手动控制

```powershell
.\start.ps1
.\stop.ps1          # 默认同时停止守护程序
.\start_watchdog.ps1
.\stop_watchdog.ps1
```

守护程序必须通过 `start_watchdog.ps1`（WMI 脱离会话）或计划任务启动；在终端里直接运行
`wait_for_ai_wechat.ps1` 会让它随该终端/agent 会话一起被杀掉。

## 8. 关键文件

| 文件 | 作用 |
|---|---|
| `outputs/WeChatAI/bridge.py` | 主消息轮询、模型指令、回复和图片流程 |
| `outputs/WeChatAI/vision_media.py` | 图片缓存定位、Hook 解码、格式转换和清理 |
| `outputs/WeChatAI/vision_client.py` | llama.cpp OpenAI 兼容视觉请求 |
| `outputs/WeChatAI/config.json` | 账号、模型、端口和超时配置 |
| `outputs/WeChatAI/start_dual_wechat.ps1` | 同时启动两个微信实例 |
| `outputs/WeChatAI/startup_backend.ps1` | llama.cpp、OpenClaw、守护程序启动入口 |
| `outputs/WeChatAI/wait_for_ai_wechat.ps1` | 守护程序：Hook 端口、llama.cpp、Gateway 与桥接的看护 |
| `outputs/WeChatAI/start_watchdog.ps1` | 脱离会话启动守护程序（WMI） |
| `outputs/WeChatAI/stop_watchdog.ps1` | 停止守护程序 |
| `outputs/WeChatAI/start.ps1` | 启动桥接（stdout/stderr 重定向到 `bridge.out.log` / `bridge.err.log`） |
| `outputs/WeChatAI/stop.ps1` | 停止桥接（默认同时停止守护程序） |
| `work/wechat_ai_bridge_state/state.json` | 会话状态和模型状态 |
| `work/wechat_ai_bridge_state/bridge.log` | 桥接运行日志 |
| `work/wechat_ai_bridge_state/watchdog.log` | 守护程序日志（自愈、心跳） |
| `work/wechat_ai_bridge_state/bridge.fault.log` | 原生崩溃栈（faulthandler） |
| `work/wechat_ai_bridge_state/startup.log` | 后端自检和守护程序启动日志 |
| `work/WechatHook4.1.10.27-fixed` | 当前修正版 Hook 源码和编译输出 |
| `work/test_bridge.py` | 桥接单元测试 |

## 9. 故障排查

### 文字和图片都不回复

检查端口：

```powershell
Get-NetTCPConnection -State Listen -LocalPort 30001,30002,8080
```

查看日志：

```powershell
Get-Content "..\..\work\wechat_ai_bridge_state\bridge.log" -Tail 50
Get-Content "..\..\work\wechat_ai_bridge_state\startup.log" -Tail 50
```

### 文字正常、图片无回复

确认：

1. 当前会话已经使用 `/qwen`。
2. `8080` 正在监听。
3. AI 账号位于 `30001`。
4. `D:\Weixin\version.dll` 的 SHA-256 与本文一致。

### 双开失败

完全退出全部微信实例，然后运行：

```powershell
.\start_dual_wechat.ps1 -Restart
```

不要在一个微信已经完全登录后再单独启动第二个实例。

### 回滚 Hook

1. 退出全部微信实例。
2. 将 `D:\Weixin\version.dll.backup-20260919-211118` 复制回 `D:\Weixin\version.dll`。
3. 重新双开微信。

## 10. 当前限制和后续方向

- 目前只处理普通私聊；群聊处理尚未正式启用和验收。
- DeepSeek 不进行图像识别。
- 图片处理包含解码、视觉推理和人设回复，速度会慢于纯文字。本次真实测试约 31 秒完成。
- `GetSelfProfile` 的 `wxid` 字段在当前 Hook 中返回微信号/别名，但昵称足以用于人工核对；桥接仍以固定 AI 端口和本地数据库账号校验为准。
- 微信本体仍由用户手动双开和登录；OpenClaw、llama.cpp 与守护程序可自动启动。
- 后续若继续开发，优先增加图片阶段耗时日志、群聊白名单/触发条件以及图片回复长度控制。

## 11. 给后续 Codex 的接手说明

继续工作前先做以下只读检查：

1. 阅读本文和 `outputs/WeChatAI/README.md`。
2. 检查 `30001`、`30002`、`8080` 是否监听。
3. 查看 `bridge.log` 与 `startup.log` 尾部。
4. 确认 `config.json` 中 `hook_url` 仍为 `http://127.0.0.1:30001`。
5. 不要交换主账号与 AI 账号端口。
6. 修改后运行 `python -m unittest work.test_bridge` 和 `outputs/WeChatAI/tests/test_backend_autostart.ps1`。
7. 任何微信 UI、图片识别和真实送达结论都必须用实际日志或人工测试确认。


## 2026-09-19 群聊 @ 与最近消息总结

- 已加入群聊识别：仅处理 `@chatroom` 会话。
- 普通群消息忽略，必须先 `@大肥鱼` 或 `@DSH20260918`。
- 触发后读取当前群最近 70 条消息（原为 200，2026-09-19 缩短），排除 Bot 自己的消息，交给当前模型总结或回答。
- 回复通过 Hook `/SendTextMsg_NoSrc` 携带 `atlist`，会 @ 触发请求的群成员。
- 群聊默认单条回复，不发送每日帮助提示，不启用拟人化多段分段。
- 配置项：`group_mention_names`、`group_history_limit`。
- 已重启桥接程序；启动日志显示 `bridge ready direct_chats=3`、`online recovery complete notices=0`。
- 尚未完成真实微信群验收；需要在 AI 微信所在群发送 `@大肥鱼 总结一下最近群聊` 验证 Hook 的群 @ 显示和上下文读取。
- 总结历史的触发条件已收窄为包含“总结”和“对话”的表达（如“总结对话”“总结一下对话”“帮我总结一下对话”）；“总结群聊”等不再读取群历史。
- 2026-09-19 已补齐 Hook `/SendTextMsg_NoSrc` 接口，将 `atlist` 写入微信原生文本消息结构；重新编译并安装 `D:\Weixin\version.dll`。
- 旧 DLL 备份：`D:\Weixin\version.dll.backup-20260919-232133`。
- 人工验收通过：AI 账号单独登录电脑微信后，群内 `@大肥鱼` 能触发回复，Bot 回复可正常 @ 对应群成员。
- 群聊处理已改为每群独立 FIFO 工作队列：轮询线程只收取并入队，同群保持顺序，不同群可并行，模型生成不会再阻塞微信消息轮询。
- 引用消息只识别外层正文开头的 `@大肥鱼`；不再扫描 `<refermsg>` 或其他 XML 中的旧 @ 内容。
- 自动测试共 45 项通过；实测队列任务 `seq=1789828326000` 已生成并由 Hook 成功发送，日志记录 `group reply verified`。

## 2026-09-19 23:36 桥接无声消失：根因与守护程序

现象：think 模式下群里发过 `@大肥鱼 总结一下对话` 后，桥接不再回复；`bridge.log` 停在
`23:36:35` 的 `/fast` 一行，没有任何异常栈，`bridge.pid` 里的 44020 已不存在，Windows 事件日志
里也没有 `python.exe` 的 Application Error / WER 记录。

根因：桥接是上一轮 Codex 会话通过 `start.ps1`（`Start-Process`）启动的，属于 Codex Desktop
app-server 进程树。Codex 日志数据库（`~/.codex/logs_2.sqlite`）显示 app-server
`pid:16036` 的存活区间是 `20:24:21 -> 23:36:49`，它退出时 Windows 连带结束了整棵子进程树，
桥接被 `TerminateProcess` 静默杀死（llama.cpp 和当时的守护程序同样消失，只有跑在 WSL
systemd 里的 OpenClaw Gateway 存活）。`23:36:45` 的 `state.json` 写入证明死亡发生在它之后几秒。
think 模式那条群消息只是当时正在处理中的任务，不是死因。

修复：

- `start_watchdog.ps1` 通过 WMI `Win32_Process.Create` 启动守护程序，父进程是 `WmiPrvSE.exe`，
  不再位于任何 agent 会话的作业对象中。
- `wait_for_ai_wechat.ps1` 升级为守护程序：每 10 秒检查 Hook 端口（在线时保证桥接运行、离线时
  暂停桥接），并检查 llama.cpp `8080`、OpenClaw Gateway `18789`，掉线即调用
  `startup_backend.ps1` 拉起；带单实例锁文件和 `watchdog.log`。
- 计划任务 `Codex-WeChat-AI-Bridge` 增加每 1 分钟重复触发（`TimeTrigger` + `Repetition`），
  由任务计划程序（而非 agent）重跑 `startup_backend.ps1` 自检，所以任何组件被杀都会自动恢复。
- `start.ps1` 把 stdout/stderr 重定向到 `bridge.out.log` / `bridge.err.log`；`bridge.py` 启用
  `faulthandler`（`bridge.fault.log`）并接管 `sys.excepthook` / `threading.excepthook`。
- `stop.ps1` 默认先停守护程序，避免“停掉又被 10 秒后拉起来”；由守护程序自己调用时使用
  `-KeepWatchdog`。
- 端口探测改用真实 TCP 连接：WSL 转发的 `18789` 不会出现在 `Get-NetTCPConnection` 里；
  读取 `config.json` 时必须显式 `-Encoding UTF8`，否则 Windows PowerShell 5.1 按 ANSI 解码会
  解析失败。

已验证：手动 `Stop-Process` 杀掉桥接（模拟崩溃、保留过期 pid 文件）后，守护程序在下一次轮询
重新拉起（`watchdog.log`: `bridge: Bridge started: PID 53236`，`bridge.log`:
`bridge ready direct_chats=4`）；计划任务每分钟自检记录 `NextRunTime` 正常推进。

## 2026-09-19 群聊总结缩短到 70 条

- `group_history_limit` 从 200 改为 70，`bridge.py` 新增 `GROUP_SUMMARY_HISTORY_LIMIT = 70`。
- 群聊转写超过 `GROUP_SUMMARY_MAX_CHARS = 12000` 字符时只保留最新部分并加“（较早消息已省略）”，
  避免整段 prompt 作为 `wsl.exe --message` 参数超出 Windows 命令行长度上限。
- 群聊任务失败时会 @ 提问者回复“AI 暂时无法回复，请稍后重发。”，按群 60 秒冷却。

## 2026-09-20 不再闪出命令符窗口

现象：改完守护之后，每次对话都会有一个命令符窗口闪一下。

原因有两个，都属于本次改动引入或放大的：

1. `wsl.exe` 是控制台程序。桥接本身没有控制台窗口（`start.ps1` 用 `-WindowStyle Hidden` +
   重定向启动），所以每次 `subprocess.run(["wsl.exe", ...])` 调用 OpenClaw 时，Windows 都会为它
   **新建一个控制台**并在桌面上闪一下。改动前桥接常常是在终端里手工启动的，它继承了那个终端
   的控制台，子进程复用它，所以看不到闪烁；现在桥接由守护程序以无窗口方式启动，闪烁就暴露出来了。
2. `start.ps1` 现在用 `-RedirectStandardOutput/-RedirectStandardError`，一旦桥接由带控制台的
   父进程（例如手工在 PowerShell 里运行 `start.ps1`）启动，会额外出现属于桥接自己的控制台窗口。

修复：

- `bridge.py` 新增 `openclaw_run_options()`：调用 `wsl.exe` 时带 `CREATE_NO_WINDOW`
  （`0x08000000`），并显式 `stdin=DEVNULL`（没有控制台可继承时必须重定向标准输入）。
- `bridge.py` 新增 `show_console_window(config)`：桥接启动时用 `GetConsoleWindow()` +
  `ShowWindow(hwnd, 0)` 隐藏自己的控制台窗口；`config.json` 新增 `show_console_window`（默认
  `false`），设为 `true` 可保留窗口用于手工调试。
- 单元测试新增 2 项：`CREATE_NO_WINDOW` 必须存在、`show_console_window` 开关行为正确（共 47 项通过）。

## 2026-09-20 仍有黑框闪烁：补上全局子进程默认

第一轮只改了桥接直接调用的 `wsl.exe`，实际测试仍在闪。原因是桥接间接启动的子进程：

- `wechatauto`（`work/wechatauto-replica/wechatauto/db.py`）在定位微信进程和数据库时会执行
  `tasklist`：`db.py` 的两处调用带了 `CREATE_NO_WINDOW`，但**没有重定向 stdin**。桥接本身没有
  控制台，启动器就没有可以继承的标准输入句柄，控制台子系统仍会做一次控制台相关的初始化，
  于是黑框一闪。
- 类似的裸 `subprocess` 调用在库里还有多处（`media.py`、`uia_driver.py`、`diagnose_keys.py` 等）。

修复：

- `bridge.py` 新增 `apply_windowless_spawn()`：在 Windows 上给 `subprocess.run` /
  `subprocess.Popen.__init__` 打默认参数补丁，自动为**所有**子进程加上
  `creationflags |= CREATE_NO_WINDOW` 和 `stdin = DEVNULL`（调用方显式传入的 flags 会保留，
  重复调用不会叠加钩子）。这样桥接进程树下任何一层启动控制台程序都不会再弹窗。
- `db.py` 两处 `tasklist` 调用补上 `stdin=subprocess.DEVNULL`；
  排查方法（下次再遇到闪烁）：对比 `Get-Process conhost,OpenConsole` 的启动时间与桥接子进程。

## 2026-09-20 00:00 群聊回复要等几分钟：会话上下文超预算

现象：群里 `@大肥鱼 测试`（think 模式）等了 2 分 54 秒才收到回复，私聊也从最开始的 3 秒变成
16～88 秒。

排查（全部有实测数据）：

- 直接压测 llama-server（`work/diag_model_speed.py`）：decode **62 tok/s**、prefill **1403 tok/s**，
  短请求 0.4～0.7 秒。**模型本身没有问题。**
- OpenClaw 网关日志（`journalctl --user -u openclaw-gateway.service`）给出了真正的证据：
  - `[context-pressure-diagnostic] admitted provider attempt ... estimatedPromptTokens=31120
    promptBudgetBeforeReserve=24576` —— 群会话的上下文估算 **31k tokens**，超过网关预算
    （`contextTokens 32768` 的 75% = 24576）。
  - `[agent/embedded] context-engine compaction failed` + `[compaction-diag] ... trigger=budget
    outcome=failed detail=Command_reached_its_deadline durationMs=150119` —— **每一条消息都先触发一次
    上下文压缩，而这次压缩在 150 秒的内部截止时间上失败**，然后才真正回答。这就是“几分钟”的来源。
  - 会话记录显示 `inputTokens 16369 / totalTokens 26898`，`sessionStartedAt` 起就没有清过
    （群里的写小说、被要求开喷等长内容都堆在会话里）。
- 对比：同一时间私聊会话约 26k、压缩能过，单轮 16 秒；群会话 31k 压缩失败，单轮 174 秒。

修复：

- 立即处理：直接调用网关把两个会话压缩掉，**同一个压缩在带外运行只要 4.2 秒**：
  - 群 `...2c44cb49b324...`：`tokensBefore 26944 → tokensAfter 10400`
  - 主私聊 `...0b4d60ea...`：`tokensBefore 15811 → tokensAfter 1863`
  压缩后会话回到新会话基线（~10.7k），不再触发每轮预算压缩。
- 桥接新增命令（私聊和群聊都支持）：
  - `/compact`：调用 `openclaw sessions compact` 压缩当前会话，回复里带 `tokensBefore → tokensAfter`。
  - `/reset`：把该聊天的 `session_epoch` +1，下一条消息使用全新 OpenClaw 会话（上下文清空）。
  - `/help` 已列出这两个命令。
- 桥接新增自动兜底：`maybe_auto_compact()` —— 单轮生成超过
  `COMPACT_AFTER_SECONDS = 90` 秒时，自动在后台压缩该会话（日志记录 `automatic session compaction
  peer=... tokens=...->...`），避免再次退化成“每轮都卡在失败的压缩上”。
- 单元测试新增 5 项（命令识别、epoch 轮换、压缩回复格式、自动压缩阈值），共 **53 项通过**。

结论给用户：**本地模型的 think 模式本身就慢**（实测同一条“测试”私聊 think 88 秒、fast 3 秒），
叠加会话超预算后变成几分钟。日常用 `/fast`；会话变慢时发一条 `/compact` 立刻恢复。

## 2026-09-20 00:27 “AI 暂时无法回复”：签名与调用点不一致

改动 `epoch` 参数时只更新了调用点，漏改 `ask_openclaw()` 的函数签名，运行时报
`TypeError: ask_openclaw() got an unexpected keyword argument 'epoch'`，被兜底分支吞掉后
统一回了“AI 暂时无法回复，请稍后重发。”。已修正签名，并补两项防回归测试：

- `test_ask_openclaw_accepts_a_session_epoch`：签名必须包含 `epoch`（默认 0）。
- `test_every_ask_openclaw_call_site_passes_only_supported_keywords`：用 `ast` 扫描
  `bridge.py` 里所有 `ask_openclaw(...)` 调用点，关键字必须在真实签名内（同时校验调用点数量）。

另加 `work/verify_openclaw_turn.py`：按桥接的真实调用方式（相同关键字）跑一轮 qwen 和
deepseek，用于改动后自检。实测 qwen/off 1.36 秒、deepseek/off 2.66 秒返回。
测试总数 **56 项通过**。

## 2026-09-20 00:31 /help 显示 “year年month月day日”

现象：`/help`（私聊和群聊都一样）第一行显示成 `时间：year年month月day日`。

根因：`DAILY_HELP_TEMPLATE` 是 `str.format` 模板，只有 `claim_daily_help_notice()` 会渲染它；
`/help` 分支（`process_group_task` 和 `process_new_messages` 各一处）直接把**原始模板字符串**
当回复发出去，占位符 `{year}/{month}/{day}` 原样漏出。每日公告那条路径是正常的，所以只有
主动发 `/help` 才会看到。

修复：

- 新增 `render_daily_help(now=None)`：唯一的渲染入口，按东京时区填充年月日；
  `claim_daily_help_notice()` 也改为调用它（两者输出保证一致）。
- 两处 `/help` 分支改用 `render_daily_help()`。
- 防回归测试 4 项：
  渲染结果以 `时间：2026年9月20日` 开头且不含占位符；
  跨东京午夜日期正确切换；
  `/help` 与每日公告文本一致；
  用 `ast` 扫描 `bridge.py`，禁止再出现 `x = DAILY_HELP_TEMPLATE` 这类直接返回原始模板的写法。
- 测试总数 **60 项通过**。

## 2026-09-20 00:35 新增 agent 交接文档

- 新增 `AGENT_HANDOFF.md`：面向下一个 AI agent 的交接说明（架构 5 条、命令清单、运行期文件、
  9 个已踩过的坑、修改守则、测试与自检命令、症状→排查路径、需要人工完成的事项）。
  文档里的每条命令与阈值都在写完当天实测校验过（60 项测试、三个端口、守护心跳、
  `verify_openclaw_turn.py` 真实链路、模型速度基线、网关日志与会话列表命令）。
