"""Small source integrations, kept as a reproducible migration record."""
import ast
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent
p=ROOT/'outputs/WeChatAI/bridge.py'
s=p.read_text(encoding='utf-8')
raw=s.encode('utf-8');lines=raw.splitlines(keepends=True);offsets=[0]
for line in lines:offsets.append(offsets[-1]+len(line))
insertions=[]
for node in ast.walk(ast.parse(s)):
    if isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id in ('format_timed_reply','prepare_reply_parts'):
        if not any(k.arg=='config' for k in node.keywords):
            pos=offsets[node.end_lineno-1]+node.end_col_offset-1
            # Calls may have a trailing comma before their closing parenthesis.
            prefix=raw[:pos].rstrip()
            addition=b' config=config, chat=chat' if prefix.endswith(b',') else b', config=config, chat=chat'
            insertions.append((pos,addition))
for pos,addition in sorted(insertions,reverse=True):raw=raw[:pos]+addition+raw[pos:]
s=raw.decode('utf-8').replace('"model", "help",','"model", "debug", "help",')
p.write_text(s,encoding='utf-8')
p=ROOT/'outputs/WeChatAI/config.json';d=json.loads(p.read_text(encoding='utf-8'))
d.update(conversation_queues_enabled=True,session_queue_max_pending=128,debug_status_bar=False,
         daily_announcement=False,humanize_plain_text_enabled=True)
p.write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
p=ROOT/'outputs/WeChatAI/persona.md';s=p.read_text(encoding='utf-8')
s=s.replace('切换千问或 DeepSeek 不改变你是谁。','使用 DeepSeek 不改变你的角色身份。')
s+='\n\n## 微信自然输出\n\n回复使用普通文本，不使用 Markdown 标题、加粗、代码围栏或表格。需要给代码或公式时保证原意完整。不要用“作为 AI”“作为一个人工智能”“很高兴为您服务”等套话开头；被直接问身份、能力、隐私或限制时如实回答，不冒充真人。表情的画面和配字只是线索，其本次语气由上下文决定，不机械逐项报出视觉分析。\n'
p.write_text(s,encoding='utf-8')
