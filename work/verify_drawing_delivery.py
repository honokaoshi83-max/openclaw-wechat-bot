"""Send an existing local test image to the bot's own File Transfer Assistant."""
import logging
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from outputs.WeChatAI import bridge
from outputs.WeChatAI.image_generation import validate_png
from wechatauto.db import WeChatDB
import json
logging.basicConfig(level=logging.INFO)
cfg=json.loads(bridge.CONFIG_PATH.read_text(encoding='utf-8'))
db=WeChatDB(db_dir=bridge.resolve_db_dir(cfg),account=cfg['ai_account_dir'],
           workdir=str(bridge.RUNTIME_DIR/'drawing_delivery_check'))
image=Path(r'D:\AI\QwenImage21\output\deepseek-whale-girl-20261006.png')
validate_png(image)
bridge.send_generated_image(db,cfg,'filehelper',image)
print('FILEHELPER_IMAGE_DELIVERY_VERIFIED')
