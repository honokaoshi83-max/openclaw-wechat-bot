import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "work" / "wechatauto-replica"))

from wechatauto.db import WeChatDB
from wechatauto.media import MediaDownloader

config = json.loads((ROOT / "outputs" / "WeChatAI" / "config.json").read_text(encoding="utf-8"))
db = WeChatDB(
    db_dir=r"D:\jilu\xwechat_files",
    account=config["ai_account_dir"],
    workdir=str(ROOT / "work" / "image_hook_diag_cache"),
)
peer = config["main_peer"]
messages = db.get_messages(peer, limit=50)
print([(m.get("local_id"), m.get("sort_seq"), m.get("type"), m.get("local_type")) for m in messages[:20]])
images = [m for m in messages if str(m.get("type") or "") == "图片" or int(m.get("local_type") or 0) == 3]
if not images:
    raise SystemExit("no image message found")
msg = images[0]
md = MediaDownloader(db, save_dir=str(ROOT / "work" / "image_hook_diag_out"))
row = db.get_message_row(peer, int(msg["local_id"]), local_type=3)
digest = md._img_md5(row)
dat_path = md._find_dat(peer, digest, row["create_time"])
if not dat_path:
    dat_path = md._find_dat(peer, digest, row["create_time"], thumbnail=True)
if not dat_path:
    raise SystemExit("image dat file not found")
out_dir = ROOT / "work" / "image_hook_diag_out"
out_dir.mkdir(parents=True, exist_ok=True)
out_path = out_dir / f"{peer}_{msg['local_id']}.jpg"
payload = json.dumps({"src_path": dat_path, "dst_path": str(out_path)}).encode("utf-8")
request = urllib.request.Request(
    config["hook_url"].rstrip("/") + "/Decode_Pic",
    data=payload,
    headers={"Content-Type": "application/json"},
    method="POST",
)
print(json.dumps({"local_id": msg["local_id"], "dat_path": dat_path, "out_path": str(out_path)}, ensure_ascii=False))
try:
    with urllib.request.urlopen(request, timeout=15) as response:
        print(response.read().decode("utf-8", "replace"))
except Exception as exc:
    print(f"hook_error={exc!r}")
print(f"output_exists={out_path.is_file()} size={out_path.stat().st_size if out_path.is_file() else 0}")

