import ast,json
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent
p=ROOT/'outputs/WeChatAI/bridge.py';s=p.read_text(encoding='utf-8')
raw=s.encode();lines=raw.splitlines(keepends=True);offset=[0]
for line in lines:offset.append(offset[-1]+len(line))
changes=[]
for func in ast.parse(s).body:
    if not isinstance(func,ast.FunctionDef):continue
    source={'summarize_quoted_visual_reply':'source','summarize_quoted_visual_search':'source',
            'process_group_task':'visual_msg','process_new_messages':'msg'}.get(func.name)
    if not source:continue
    for node in ast.walk(func):
        if isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id=='describe_image':
            a=offset[node.func.lineno-1]+node.func.col_offset;b=offset[node.func.end_lineno-1]+node.func.end_col_offset
            changes.append((a,b,('describe_media(db, peer, '+source+', image_path, ').encode()))
            # Replace function and its existing opening parenthesis together.
            changes[-1]=(a,b+1,changes[-1][2])
for a,b,value in sorted(changes,reverse=True):raw=raw[:a]+value+raw[b:]
p.write_text(raw.decode(),encoding='utf-8')
p=ROOT/'outputs/WeChatAI/config.json';d=json.loads(p.read_text(encoding='utf-8'))
d.update(sticker_emotion_enabled=True,sticker_reply_enabled=False,
         sticker_library_path='work/wechat_ai_bridge_state/sticker_library.sqlite3',
         sticker_vision_max_tokens=512,humanize_control_timeout_seconds=15)
p.write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
