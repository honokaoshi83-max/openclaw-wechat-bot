"""Preserve retired local-only source as inert files, outside Bot imports."""
from pathlib import Path
import shutil
import sys
ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
archive=ROOT/'work/retired_local_components';archive.mkdir(exist_ok=True)
files=['image_generation.py','drawing_reference.py','local_edit_flow.py',
       'image_edit_client.py','image_edit_state.py','install_image_edit.ps1',
       'start_image_edit.ps1','stop_image_edit.ps1','stop_llama_for_image_edit.ps1']
for name in files:
    src=ROOT/'outputs/WeChatAI'/name
    if src.exists(): shutil.move(src,archive/(name+'.txt'))
for name in ('test_vision_runtime.ps1',):
    src=ROOT/'outputs/WeChatAI/tests'/name
    if src.exists(): shutil.move(src,archive/(name+'.txt'))

# Migrate stored preferences without exposing message contents or changing epochs.
from outputs.WeChatAI import bridge
path=bridge.STATE_PATH
if path.exists():
    shutil.copy2(path,path.with_name('state.before-deepseek-only.json'))
    state=bridge.load_state(path)
    bridge.migrate_deepseek_sessions(state)
    bridge.save_state(path,state)
    print('Stored sessions migrated:',len(state.get('chats',{})))

p=ROOT/'outputs/WeChatAI/tests/test_backend_autostart.ps1'
s=p.read_text(encoding='utf-8-sig')
lines=s.splitlines()
lines=[line for line in lines if not any(t in line for t in (
    "if ($startupText -notmatch 'llama-server", "if ($startupText -notmatch 'image_edit_gpu", "if ($startupText -notmatch 'skipping llama"))]
s='\n'.join(lines)+'\n'
s+="\nif ($startupText -match 'llama-server|8080' -or $waiterText -match '\\$llamaOnline|8080') { throw 'Local model dependency must be absent' }\n"
p.write_text(s,encoding='utf-8-sig')
p=ROOT/'outputs/WeChatAI/tests/test_manage_bot_stack.ps1'
s=p.read_text(encoding='utf-8-sig').replace("'disabled', 'hook', 'bridge', 'qwen', 'openclaw', 'imageEdit', 'errors'", "'disabled', 'hook', 'bridge', 'openclaw', 'errors'")
start=s.index("    if ($source -notmatch 'llama-server")
end=s.index('\n}',start)
s=s[:start]+'''    if ($source -match 'llama-server|8080|comfyMain' -or $source -notmatch 'OpenClawGateway') {
        throw 'Lifecycle must only manage the DeepSeek/OpenClaw bot services'
    }'''+s[end:]
p.write_text(s,encoding='utf-8-sig')
print('Retired source retained as non-executable .txt in',archive)
