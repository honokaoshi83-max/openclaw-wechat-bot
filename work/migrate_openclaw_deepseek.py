"""Run inside OpenClawGateway: remove local providers without printing secrets."""
import json
import os
import shutil
import time
from pathlib import Path

root = Path('/home/openclaw/.openclaw')
stamp = str(int(time.time()))
model = 'deepseek/deepseek-flash'
backup = root/'backups'/('deepseek-only-'+stamp)
backup.mkdir(parents=True, mode=0o700)
path=root/'openclaw.json'
shutil.copy2(path, backup/'openclaw.json')
d=json.loads(path.read_text())
providers=d.setdefault('models',{}).setdefault('providers',{})
for name in list(providers):
    if name != 'deepseek': del providers[name]
defaults=d.setdefault('agents',{}).setdefault('defaults',{})
defaults['model']={'primary':model}
defaults['models']={model:{}}
for entry in d['agents'].get('entries',{}).values():
    if 'model' in entry: entry['model']={'primary':model}
def rewrite(obj):
    if isinstance(obj,dict):
        for key,val in list(obj.items()):
            if key in ('modelProvider','providerOverride') and val in ('llama-cpp','ollama'):
                obj[key]='deepseek'
            elif key in ('model','modelOverride') and isinstance(val,str) and any(x in val.lower() for x in ('qwen','llama-cpp/','ollama/')):
                obj[key]=model if '/' in val else 'deepseek-flash'
            elif key in ('fallbacks','imageModelFallbacks') and isinstance(val,list):
                obj[key]=[x for x in val if not isinstance(x,str) or not any(y in x.lower() for y in ('qwen','llama','ollama'))]
            else: rewrite(val)
    elif isinstance(obj,list):
        for item in obj: rewrite(item)
rewrite(d)
temp=path.with_suffix('.tmp')
temp.write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n'); os.chmod(temp,0o600); temp.replace(path)
# Recent OpenClaw uses sessions in either agents or state. Read only session
# indexes/model catalogs, never conversation transcripts.
changed=0
for parent in (root/'agents',root/'state'):
    if not parent.exists(): continue
    for p in parent.rglob('*.json'):
        if p.name not in ('sessions.json','models.json'): continue
        try:
            item=json.loads(p.read_text())
            before=json.dumps(item)
            if p.name=='models.json' and isinstance(item,dict) and 'providers' in item:
                item['providers']={k:v for k,v in item['providers'].items() if k=='deepseek'}
            rewrite(item)
            after=json.dumps(item,ensure_ascii=False,indent=2)+'\n'
            if before != json.dumps(item):
                b=backup/p.relative_to(root); b.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,b)
                tmp=p.with_suffix('.tmp');tmp.write_text(after);os.chmod(tmp,0o600);tmp.replace(p);changed+=1
        except (ValueError,OSError): continue
print('OpenClaw provider keys:',list(providers))
print('Session/catalog indexes migrated:',changed)
print('Protected backup:',backup)
