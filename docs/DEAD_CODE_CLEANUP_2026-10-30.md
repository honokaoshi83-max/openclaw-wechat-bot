# 无用代码清理

日期：2026-10-30。

## 方法

对 `outputs/WeChatAI/*.py` 做 AST 扫描（顶层函数、类、常量在 Bot 代码、测试、PowerShell、C# 里的真实引用），并用 pyflakes 3.2.0（装在临时目录，未改项目环境）检查未用导入和局部变量；另查 `config.json` 里没有任何代码读取的配置项。

## 已删除

- `bridge.py`：`search_web`（Bing RSS 搜索）与只被它用的 `xml.etree.ElementTree` 导入；`build_hook_image_request`（发图片，画图功能遗留，无调用方）及其测试；未用的 `uuid` 导入；未用的 `debug_active` 导入。
- `humanize_output.py`：`debug_active`（恒返回 False 的空壳，只有测试在调）、未用的 `time` 导入。对应测试改为只检查 `toggle_debug` 不再写入调试状态，并保留"状态栏不会恢复"的断言。
- `dialogue_style.py`：未用的 `re` 导入。
- `check_persona.py`：一处没有占位符的 f-string。
- `config.json`：无代码读取的 `main_sender_id`、`session_key`。
- 按用户要求删除手动诊断脚本 `diagnose_db_path.py`、`diagnose_window_identity.py`（未被 git 跟踪，副本在备份目录）。

## 未删除（不是无用代码，或需要用户决定）

- `initialize_image_key.py`：图片识别首次初始化必需的手动工具（见 AGENT_HANDOFF.md）。
- OpenClaw 相关代码（`ask_openclaw`、会话压缩等）：仍在使用，第 4 步迁移完成后再删。
- `migrate_deepseek_sessions` 和启动时清理旧改图状态的代码：一次性迁移，已无实际作用但无害，留到清理 OpenClaw 时一起处理。
- `config.json.before-*` 旧配置备份、`work/` 下的备份目录和诊断脚本：是历史记录，没有动。

## 验证

- 备份：`work/backup_dead_code_2026-10-30/`。
- pyflakes 无输出；完整回归 196 项通过（删去 1 项发图片的测试）。
- 11:56 重启桥接，`bridge ready`，启动无报错。
