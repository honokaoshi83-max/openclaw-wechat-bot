"""Remove local inference/generation entrypoints after user scope confirmation."""
import ast
import json
import shutil
from pathlib import Path
from datetime import datetime
ROOT = Path(__file__).resolve().parent.parent
BACKUP = ROOT/'work/backups'/('retire-local-'+datetime.now().strftime('%Y%m%d-%H%M%S'))

def write(rel, text):
    p=ROOT/rel
    if p.exists():
        b=BACKUP/rel; b.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(p,b)
    p.write_text(text, encoding='utf-8')

p=ROOT/'outputs/WeChatAI/bridge.py'; s=p.read_text(encoding='utf-8')
tree=ast.parse(s); ranges=[]
retired={'handle_local_edit','apply_thinking_mode','process_group_image_edit_task',
         'prune_image_edit_outbox','translate_image_edit_instruction','run_local_image_edit',
         'queue_drawing','send_generated_image','process_drawing_task','send_wechat_image_hook'}
for node in ast.walk(tree):
    segment=ast.get_source_segment(s,node) or ''
    remove=False
    if isinstance(node, ast.FunctionDef) and node.name in retired: remove=True
    elif isinstance(node, ast.Try) and node in tree.body and any(t in segment for t in ('from .image_generation', 'from .image_edit_', 'from .local_edit_flow')): remove=True
    elif isinstance(node, ast.If):
        test=ast.get_source_segment(s,node.test) or ''
        remove=any(t in test for t in ('handle_local_edit(', 'drawing_prompt is not None', '"image_edit_enabled"', "'local_image_edit_enabled'", '"image_edit_task"', 'args.draw_self_test', 'DRAWING_ACTIVE.is_set()', 'kind == "draw"', "kind == 'draw'", 'action[0] == "thinking"', 'kind == "thinking"'))
        # A branch is removed only if it is an independent if; thinking elifs
        # are pruned separately below to preserve following command branches.
        if 'thinking' in test: remove=False
    elif isinstance(node, ast.Assign):
        targets=[t.id for t in node.targets if isinstance(t,ast.Name)]
        remove=bool(set(targets)&{'drawing_prompt','edit_sessions','drawing_dispatcher','IMAGE_EDIT_LOCK'})
    elif isinstance(node,ast.Expr) and isinstance(node.value,ast.Call):
        remove=bool('drawing_dispatcher.shutdown()' in segment or "parser.add_argument('--draw-self-test'" in segment)
    if remove: ranges.append((node.lineno,node.end_lineno))
lines=s.splitlines(keepends=True)
for a,b in sorted(ranges,reverse=True):
    # Only keep ranges not contained by another removed node.
    if any(x<=a and y>=b and (x,y)!=(a,b) for x,y in ranges): continue
    lines[a-1:b]=[]
s=''.join(lines)
# Strip complete elif command branches by original source indentation.
import re
s=re.sub(r'(?m)^    elif action and action\[0\] == "thinking":\n(?:        .*\n)+', '', s)
s=re.sub(r'(?m)^            elif kind == "thinking":\n(?:                .*\n)+', '', s)
s=s.replace('drawing_dispatcher: DrawingDispatcher | None = None','drawing_dispatcher=None')
s=s.replace('                            drawing_dispatcher=drawing_dispatcher,\n','')
s=s.replace('"model", "thinking", "help"', '"model", "help"')
s=s.replace('本地生图：画一张画面描述（兼容/draw；群聊需@机器人）\n','')
s=s.replace('    LOG.info("bridge ready direct_chats=%s deepseek_key=%s image_edit_enabled=%s", len(state["chats"]),\n             bool(os.environ.get(str(config.get("deepseek_api_key_env", "DEEPSEEK_API_KEY")), "").strip()),\n             bool(config.get("image_edit_enabled")))', '    LOG.info("bridge ready direct_chats=%s provider=deepseek", len(state["chats"]))')
# No stale edit task should survive a restart, and no local provider may remain selected.
s=s.replace('    for chat in state.get("chats", {}).values():\n        chat["model"]', '    state.pop("local_image_edit_pending", None)\n    state.pop("image_edit_pending", None)\n    for chat in state.get("chats", {}).values():\n        chat["model"]')
ast.parse(s)
write('outputs/WeChatAI/bridge.py', s)

p=ROOT/'outputs/WeChatAI/config.json'; d=json.loads(p.read_text(encoding='utf-8'))
for k in list(d):
    if k.startswith(('image_generation_', 'image_edit_', 'local_image_edit_')): d.pop(k)
write('outputs/WeChatAI/config.json',json.dumps(d,ensure_ascii=False,indent=2)+'\n')

p=ROOT/'outputs/WeChatAI/manage_bot_stack.ps1'; s=p.read_text(encoding='utf-8')
s=s.replace('        qwen = Test-LocalPort 8080\n','').replace('        imageEdit = Test-LocalPort 8188\n','')
start=s.index("    $llamaPath =")
end=s.index("    if ($PSCmdlet.ShouldProcess('OpenClawGateway",start)
s=s[:start]+s[end:]
write('outputs/WeChatAI/manage_bot_stack.ps1',s)

p=ROOT/'outputs/WeChatAI/controller/BotSwitch.cs';s=p.read_text(encoding='utf-8')
s='\n'.join(line for line in s.split('\n') if not any(t in line for t in ('public bool Qwen;', 'public bool ImageEdit;', 'result.Qwen =', 'result.ImageEdit =', 'private readonly Label qwenStatus;', 'private readonly Label imageEditStatus;', 'qwenStatus =', 'imageEditStatus =', 'qwenStatus.Text', 'imageEditStatus.Text')))
s=s.replace('Hook || Bridge || Qwen || OpenClaw || ImageEdit', 'Bridge || OpenClaw')
s=s.replace('status.Hook && status.Bridge && status.Qwen && status.OpenClaw', 'status.Hook && status.Bridge && status.OpenClaw')
s=s.replace('status.Qwen && status.OpenClaw && !status.Hook', 'status.OpenClaw && !status.Hook')
s=s.replace('AddStatusRow("OpenClaw", 291)', 'AddStatusRow("OpenClaw / DeepSeek", 263)')
write('outputs/WeChatAI/controller/BotSwitch.cs',s)
print('Backup:',BACKUP)
