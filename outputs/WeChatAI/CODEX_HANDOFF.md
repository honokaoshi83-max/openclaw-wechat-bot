# 微信 AI 小号桥接：Codex 交接与 Llama 重构说明

更新时间：2026-09-19

这份文件是后续 Codex 继续开发、排查和重构本项目的入口。它记录已经验证的事实、当前文件结构、运行边界，以及把底层模型替换为 Llama 时需要保持的行为。

## 1. 项目目标

Windows 微信中的 AI 小号作为独立 AI 账号：普通微信联系人给 AI 小号发文字后，由本机 OpenClaw 调用本地或远程模型生成回复，再通过 AI 小号发送回去。

当前桥接使用 `wechatauto-replica` 读取微信本地数据库，并操作与 AI 账号匹配的 Windows 微信窗口发送消息。

最近行为调整：短寒暄、称呼、简单确认和单独的“测试”会走紧凑回复路径，向模型追加 1～2 句限制，并在发送前硬限制为最多 2 句、60 个字符，合并为单条消息；任务型或较长输入仍使用拟人化分段。

内部上下文防泄漏：微信智能体的记忆搜索只允许 `memory` 文件源，并关闭跨会话摘要召回；桥接发送前还会过滤 `project: path`、`observed` 和 `assistantTexts` 等内部标记。会话前缀已切换到 `wechat-ai-public-bluewhale-full-v3`，旧 `full-v2` 会话保留用于回滚。

## 2. 已验证的账号和运行环境

- AI 微信账号：`<AI账号wxid>`
- 当前已验证主账号：`<主账号wxid>`
- 微信数据库目录：`D:\jilu\xwechat_files`
- AI 账号目录：`<AI账号目录>`
- Python 虚拟环境：`work/.venv`
- 微信组件源码：`work/wechatauto-replica`
- 组件固定提交：`f4fdf73621f667bb68df221c4301be7222770bf6`
- OpenClaw WSL 发行版：`OpenClawGateway`
- OpenClaw agent：`wechat-public`
- agent 工具配置：`minimal`
- 桥接脚本：`outputs/WeChatAI/bridge.py`

本机需要保持 Windows 用户会话、AI 微信小号登录，并且发送时桌面不能锁定。桥接发送会短暂操作微信窗口。

## 3. 当前模型配置

`outputs/WeChatAI/config.json` 当前内容的关键字段：

```json
{
  "agent": "wechat-public",
  "session_key_prefix": "wechat-ai-public-bluewhale-full-v3",
  "models": {
    "deepseek": "deepseek/deepseek-v4-pro",
    "qwen": "ollama/fredrezones55/Qwen3.5-Uncensored-HauhauCS-Aggressive:9b"
  },
  "initial_model": "deepseek"
}
```

最近主账号的 `work/wechat_ai_bridge_state/state.json` 状态是：

```json
{
  "model": "qwen"
}
```

实际 OpenClaw 会话检查确认当前运行模型为：

```text
modelProvider: ollama
model: fredrezones55/Qwen3.5-Uncensored-HauhauCS-Aggressive:9b
contextTokens: 65536
```

最近轨迹同时出现过：

```text
deepseek/deepseek-v4-pro
ollama/fredrezones55/Qwen3.5-Uncensored-HauhauCS-Aggressive:9b
```

因此微信回复中出现“我是 DeepSeek”不能作为实际路由证据。当前实现对同一联系人复用同一个会话键，切换模型后仍可能读取旧模型的对话历史；模型的自我身份描述可能因此错误。判断真实模型应以 OpenClaw trajectory 的 `model.completed`、`modelProvider` 和桥接日志为准。

## 4. 当前已实现功能

### 微信消息范围

- 普通私聊文本消息会触发回复。
- 群聊、公众号、微信系统会话、非文本消息不会触发回复。
- 目前代码默认处理所有符合条件的普通私聊；`config.json` 中仍保留主账号字段，继续重构时要确认是否保留白名单限制。
- 双开微信时，根据进程读取到的账号 ID 选择 AI 小号窗口，找不到唯一匹配窗口时不发送，避免误用主号。

### 模型切换

- `/qwen`：当前联系人切换到 Qwen。
- `/deepseek`：当前联系人切换到 DeepSeek。
- 模型选择保存在 `state.json` 的每个联系人记录中。
- 新联系人默认使用 `initial_model`，当前是 DeepSeek。
- 当前 `session_key()` 只按联系人生成，没有把模型别名纳入会话键。

### 离线消息

程序从离线或微信窗口不可用状态恢复时：

- 不读取离线期间消息正文。
- 不执行离线期间的 `/qwen` 或 `/deepseek`。
- 同一联系人无论积压多少条消息，只发送一次：

```text
当前模型已上线，如有需求可以现在开始对话
```

- 之后新收到的消息才按正常流程处理。

### 联系人检索和发送

- 优先从本地联系人数据库读取目标备注或昵称。
- 名称唯一对应目标账号时走快速搜索路径。
- 同名联系人回退到原来的 `wxid` 定位流程。
- 发送后使用数据库记录核验目标会话是否收到回复。

### 启动和停止

- 启动：`outputs/WeChatAI/start.ps1`
- 停止：`outputs/WeChatAI/stop.ps1`
- 开机自启：`outputs/WeChatAI/enable_autostart.ps1`
- 取消自启：`outputs/WeChatAI/disable_autostart.ps1`
- 任务计划名称：`Codex-WeChat-AI-Bridge`
- 日志：`work/wechat_ai_bridge_state/bridge.log`

## 5. 关键代码入口

`outputs/WeChatAI/bridge.py`：

- `classify_message()`：识别普通文本和模型命令。
- `plan_offline_batch()`：只推进离线水位，不读取内容。
- `process_offline_messages()`：发送统一上线提示。
- `process_new_messages()`：处理新消息、切换模型、调用 OpenClaw、发送回复。
- `session_key()`：生成 OpenClaw 会话键；模型隔离重构的首要修改点。
- `ask_openclaw()`：调用 `openclaw agent`，传入 `--session-key`、`--model` 和 agent。
- `select_ai_window()` / `find_ai_window()`：双开微信窗口选择。
- `send_wechat()`：通过 `wechatauto-replica` 发送并核验。

## 6. Llama 重构目标

重构底层时，优先把“模型提供者”从微信桥接逻辑中抽出来。微信层只负责事件、状态和发送；模型层负责模型别名解析、会话、生成和错误返回。

建议目标接口：

```text
ModelRouter.generate(
    peer_id,
    model_alias,
    prompt,
) -> {
    text,
    provider,
    model_id,
    session_id,
    latency_ms,
}
```

Llama 迁移至少需要明确以下配置：

- Llama 的实际模型文件或 Ollama 模型标签。
- 推理后端：Ollama、llama.cpp、vLLM 或其他后端。
- API 地址和鉴权方式（若使用本地 Ollama，通常为 `http://127.0.0.1:11434`）。
- 上下文长度。当前 Qwen 已验证为 `65536`，Llama 需要按显存和模型能力重新验证。
- 输出上限、温度、停止词和是否启用工具。
- 是否保持 DeepSeek/Qwen 的独立历史，还是让 Llama 从干净会话开始。

## 7. 模型切换的重构要求

必须避免模型切换后共享不同模型的身份历史。推荐把模型别名纳入会话隔离：

```text
session key = prefix + peer_id_hash + ":" + model_alias
```

这样 `/qwen` 和 `/deepseek` 各自使用独立上下文。迁移到 Llama 后，建议使用：

```text
wechat-ai-public:<peer-hash>:llama
```

如果模型切换需要完全清空上下文，则增加会话代数或切换时间戳，而不是删除运行数据。旧 `state.json` 和 OpenClaw 会话应保留，方便回滚和核对。

同时建议增加一个程序处理的 `/model` 命令，直接返回配置和实际路由信息，例如：

```text
当前模型：llama
Provider：ollama
模型：<实际 Llama 标签>
```

这条信息必须来自桥接状态或 OpenClaw 回执，不要让模型自行回答自己的身份。

## 8. Llama 迁移验收清单

迁移前：

- 备份 `outputs/WeChatAI/config.json`、`bridge.py` 和 `work/wechat_ai_bridge_state/state.json`。
- 保留当前 Qwen 配置作为回滚项。
- 记录当前 OpenClaw 配置中的 provider、模型标签、`contextTokens` 和 `params.num_ctx`。

迁移后必须逐项验证：

- OpenClaw 配置校验通过。
- 直接调用 Llama 能返回非空文本。
- OpenClaw trajectory 显示正确 provider 和模型 ID。
- `ollama ps` 或对应后端状态显示实际加载的是 Llama。
- 新联系人默认模型行为符合预期。
- `/llama` 切换只影响当前联系人。
- Llama 与其他模型的会话历史已隔离。
- 普通私聊能收到回复，发送核验通过。
- 双开微信仍只操作 AI 小号窗口。
- 离线积压内容仍不会送入模型，且只发送一次上线提示。
- 模型失败时能给出可见错误，并不会推进错误的重复处理状态。
- 不修改微信原始数据库，不删除现有会话和日志。

## 9. 当前已知问题和风险

1. 模型自报身份不可信。当前已证实 Qwen 的实际执行日志正确，但回复文字仍可能说自己是 DeepSeek。
2. 同一联系人目前共用模型会话键，模型切换会带入旧历史。Llama 重构应优先修复。
3. OpenClaw agent 的默认模型仍可能显示 DeepSeek；桥接每次调用显式传 `--model`，所以默认值不等于本次实际模型，但后续应统一清理命名，减少误判。
4. Windows 微信窗口和桌面状态是发送链路的运行前提。锁屏、退出微信或账号切换可能导致发送失败。
5. 本地数据库密钥缓存、消息数据库和日志可能包含私人聊天内容，不应提交到公开仓库或分享。

## 10. 建议的重构顺序

1. 先复制现有配置和状态，确认当前桥接可回滚。
2. 抽出 `ModelRouter`，保留现有 `ask_openclaw()` 作为适配器。
3. 把会话键改为“联系人 + 模型”隔离。
4. 增加 `/llama` 和 `/model`，并让 `/model` 返回程序状态。
5. 接入 Llama 后完成配置校验、直接推理、后端运行状态和微信闭环测试。
6. 最后再决定是否移除 DeepSeek/Qwen 旧配置；在 Llama 通过完整验收前保留回滚配置。

## 11. 继续工作时的第一步

Codex 接手后先检查：

```text
outputs/WeChatAI/config.json
outputs/WeChatAI/bridge.py
work/wechat_ai_bridge_state/state.json
work/wechat_ai_bridge_state/bridge.log
```

然后确认当前 bridge 进程和 OpenClaw 会话状态，再开始改模型适配层。不要直接删除状态目录、微信数据库缓存或旧 OpenClaw 会话。
