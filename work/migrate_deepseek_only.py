"""Apply the authorized text/vision and supervisor migration with local backups."""
import ast
import json
import shutil
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKUP = ROOT / 'work' / 'backups' / ('deepseek-only-' + datetime.now().strftime('%Y%m%d-%H%M%S'))

def save(relative, text):
    path = ROOT / relative
    if path.exists():
        target = BACKUP / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
    path.write_text(text, encoding='utf-8')

def replace_symbol(text, name, replacement):
    node = next(n for n in ast.parse(text).body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)
    lines = text.splitlines(keepends=True)
    return ''.join(lines[:node.lineno-1]) + replacement.rstrip() + '\n' + ''.join(lines[node.end_lineno:])

p = ROOT / 'outputs/WeChatAI/bridge.py'
s = p.read_text(encoding='utf-8')
s = s.replace("model_alias='qwen'", "model_alias='deepseek'").replace('get("model", "qwen")', 'get("model", "deepseek")')
s = replace_symbol(s, 'describe_image', '''def describe_image(path, prompt, config, model_alias='deepseek'):
    return _describe_image(path, prompt, config, model_alias='deepseek')''')
s = replace_symbol(s, 'apply_thinking_mode', '''def apply_thinking_mode(chat: dict, thinking_level: str) -> str:
    return "当前仅使用 DeepSeek，已移除本地模型模式指令。"''')
s = s.replace('        ("\\u5207\\u6362\\u5230\\u5343\\u95ee", "qwen"),\n', '').replace('        ("\\u5207\\u6362\\u5343\\u95ee", "qwen"),\n', '')
s = s.replace('    if command in ("/qwen", "/deepseek"):', '    if command == "/deepseek":')
s = s.replace('    if command == "/fast":\n        return ("thinking", "off")\n    if command == "/think":\n        return ("thinking", "high")\n', '')
s = s.replace('    if model_alias == "qwen":\n        mode = "思考模式" if thinking_level == "high" else "快速模式"\n    else:\n        mode = "DeepSeek"', '    mode = "DeepSeek"')
s = s.replace('    with GPU_INFERENCE_LOCK:\n        return _compact_openclaw_session(config, peer, epoch, timeout)', '    return _compact_openclaw_session(config, peer, epoch, timeout)')
s = replace_symbol(s, 'maybe_auto_compact_before_turn', '''def maybe_auto_compact_before_turn(config: dict, peer: str, chat: dict) -> None:
    usage = get_openclaw_session_usage(config, peer, int(chat.get("session_epoch", 0)))
    if not usage:
        return
    context_tokens = int(usage.get("contextTokens") or config.get("context_tokens", 131072))
    total_tokens = int(usage.get("totalTokens") or 0)
    if not should_compact_before_turn(total_tokens, context_tokens, float(config.get("auto_compact_ratio", .70))):
        return
    try:
        info = compact_openclaw_session(config, peer, int(chat.get("session_epoch", 0)))
        LOG.info("pre-turn automatic compaction peer=%s tokens=%s->%s", peer, total_tokens, info.get("tokensAfter"))
    except Exception:
        LOG.warning("pre-turn automatic compaction failed peer=%s", peer, exc_info=True)''')
s = s.replace('"--model", config["models"][model_alias],', '"--model", config["models"]["deepseek"],')
s = s.replace('    if model_alias == "qwen":\n        command.extend(["--thinking", thinking_level])\n', '')
s = replace_symbol(s, 'ask_openclaw', '''def ask_openclaw(config: dict, peer: str, model_alias: str,
                 thinking_level: str, prompt: str, epoch: int = 0,
                 auto_network: bool = True) -> str:
    return _ask_openclaw(config, peer, "deepseek", "off", prompt, epoch, auto_network)''')
s = s.replace('    if model_alias == "qwen" and truncated(data):', '    if truncated(data):').replace('truncated Qwen reply;', 'truncated DeepSeek reply;').replace('Qwen retry failed after compaction', 'DeepSeek retry failed after compaction').replace('Qwen output remained truncated', 'DeepSeek output remained truncated')
s = s.replace('qwen已支持图像识别、表情包识别、GIF识别（此模式为低精确识图）。（0.2版本更新）\nDeepSeek已支持识图（此模式下为高准确识图）。（0.4版本更新）', '当前统一使用 DeepSeek API，支持文字对话和现有识图流程。')
for line in ['切换千问模型：切换千问\n', '本地模型进入思考模式：/think\n', '本地模型进入快速模式：/fast\n']:
    s = s.replace(line, '')
s = s.replace('            "qwen": "\\u5343\\u95ee\\uff08Qwen\\uff09"', '            "deepseek-only": "DeepSeek"')
node = next(n for n in ast.parse(s).body if isinstance(n, ast.FunctionDef) and n.name == 'save_state')
lines = s.splitlines(keepends=True)
migration = '''\n\ndef migrate_deepseek_sessions(state: dict) -> None:
    """Keep conversation epochs/watermarks but retire saved local-model choices."""
    for chat in state.get("chats", {}).values():
        chat["model"] = "deepseek"
        chat["thinking"] = "off"
    if "model" in state:
        state["model"] = "deepseek"
\n'''
s = ''.join(lines[:node.end_lineno]) + migration + ''.join(lines[node.end_lineno:])
s = s.replace('    total = 0\n    own_user = state["own_user"]', '    migrate_deepseek_sessions(state)\n    total = 0\n    own_user = state["own_user"]')
s = s.replace('    for chat in state.get("chats", {}).values():\n        chat.setdefault("humanized", True)', '    migrate_deepseek_sessions(state)\n    for chat in state.get("chats", {}).values():\n        chat.setdefault("humanized", True)')
save('outputs/WeChatAI/bridge.py', s)

p = ROOT / 'outputs/WeChatAI/vision_client.py'
s = p.read_text(encoding='utf-8').replace('model_alias: str = "qwen"', 'model_alias: str = "deepseek"')
start = s.index('    if model_alias == "deepseek":')
end = s.index('    payload = ', start)
s = s[:start] + '''    model = str(config.get("deepseek_vision_model", "deepseek-flash"))
    endpoint = str(config.get("deepseek_vision_url", "https://api.deepseek.com/chat/completions"))
    api_key = os.environ.get(str(config.get("deepseek_api_key_env", "DEEPSEEK_API_KEY")), "").strip()
    if not api_key:
        raise VisionError("DeepSeek 图片接口未配置 DEEPSEEK_API_KEY")
''' + s[end:]
s = s.replace('    if model_alias == "qwen":\n        # Keep the limited visual token budget for visible facts, not reasoning.\n        payload["chat_template_kwargs"] = {"enable_thinking": False}\n', '').replace('本地识别', '识别').replace('本地视觉服务', '视觉服务')
save('outputs/WeChatAI/vision_client.py', s)

p = ROOT / 'outputs/WeChatAI/config.json'
d = json.loads(p.read_text(encoding='utf-8'))
d['models'] = {'deepseek': d['models']['deepseek']}
d['initial_model'] = 'deepseek'
for k in ('vision_model', 'vision_url', 'qwen_context_tokens'):
    d.pop(k, None)
save('outputs/WeChatAI/config.json', json.dumps(d, ensure_ascii=False, indent=2)+'\n')

p = ROOT / 'outputs/WeChatAI/startup_backend.ps1'
s = p.read_text(encoding='utf-8')
start = s.index('# --- llama.cpp')
end = s.index("if (Test-BotDisabledThisBoot", start)
# The first intentional-stop guard after the complete llama block precedes Gateway.
s = s[:start] + '# DeepSeek-only: no local model service is started.\n\n' + s[end:]
s = '\n'.join(line for line in s.split('\n') if not line.startswith(('$llamaExe =', '$mmproj =', '$imageEditLock =')))
save('outputs/WeChatAI/startup_backend.ps1', s)

p = ROOT / 'outputs/WeChatAI/wait_for_ai_wechat.ps1'
s = p.read_text(encoding='utf-8').replace('        $llamaOnline = Test-PortListening 8080\n', '')
s = s.replace('if ((-not $llamaOnline) -or (-not $gatewayOnline))', 'if (-not $gatewayOnline)').replace('backend down (llama=$llamaOnline gateway=$gatewayOnline)', 'backend down (gateway=$gatewayOnline)').replace(' llama=$llamaOnline', '')
save('outputs/WeChatAI/wait_for_ai_wechat.ps1', s)
print('backup:', BACKUP)
