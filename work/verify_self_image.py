"""Check the deployed visual/persona chain without sending WeChat messages."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from outputs.WeChatAI import bridge

config = json.loads((ROOT / 'outputs/WeChatAI/config.json').read_text(encoding='utf-8'))
sys.stdout.reconfigure(encoding='utf-8')
(ROOT / 'work/self_image_probe').mkdir(parents=True, exist_ok=True)
key = subprocess.run(['powershell', '-NoProfile', '-Command',
    "[Environment]::GetEnvironmentVariable('DEEPSEEK_API_KEY','User')"],
    capture_output=True, text=True, check=True).stdout.strip()
if key:
    os.environ['DEEPSEEK_API_KEY'] = key
source = Path('D:/AI/QwenImage21/output/deepseek-whale-girl-20261006.png')
image = bridge.prepare_vision_image(source, ROOT / 'work/self_image_probe')
results = []
for alias in ('qwen', 'deepseek'):
    start = time.monotonic()
    try:
        description = bridge.describe_image(image, '客观描述图中人物的可见外观。', config, model_alias=alias)
        reply = bridge.ask_openclaw(config, 'diag-self-image-' + alias + '-20261006', alias, 'off',
            bridge.visual_reply_prompt('图片', description) + '\n用户问：这是谁？', epoch=int(time.time()), auto_network=False)
        results.append({'model': alias, 'description': description, 'reply': str(reply),
                        'seconds': round(time.monotonic() - start, 2)})
    except Exception as exc:
        results.append({'model': alias, 'error_type': type(exc).__name__, 'seconds': round(time.monotonic() - start, 2)})
    print(json.dumps(results[-1], ensure_ascii=False), flush=True)
(ROOT / 'work/self_image_probe/result.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
