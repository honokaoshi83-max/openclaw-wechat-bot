"""One-time local WeChat image-key initialization.

Run while the AI WeChat account is logged in, then open any image in that
account when prompted. The detected key is stored in the bridge DB cache and
normal image replies can decrypt in the background without UI automation.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT / "work" / "wechatauto-replica"))

from bridge import resolve_db_dir
from wechatauto.db import WeChatDB
from wechatauto.media import MediaDownloader


def main() -> int:
    config = json.loads((HERE / "config.json").read_text(encoding="utf-8"))
    cache = ROOT / "work" / "wechat_ai_bridge_state" / "db_cache"
    db = WeChatDB(
        db_dir=resolve_db_dir(config),
        account=config["ai_account_dir"],
        workdir=str(cache),
    )
    downloader = MediaDownloader(db, save_dir=str(ROOT / "work" / "wechat_ai_bridge_state" / "vision_tmp"))
    print("请在 AI 微信中打开任意一张图片查看大图。程序将在 120 秒内自动采集密钥。")
    result = downloader.detect_image_key(refresh=True)
    if not result:
        print("未采集到图片密钥。请确认打开的是 AI 账号中的图片，然后重新运行。")
        return 1
    key_file = cache / "image_keys.json"
    print(f"图片密钥已保存：{key_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
