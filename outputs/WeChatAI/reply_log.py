"""Append each confirmed WeChat reply to a local JSONL file for check_persona.py."""
import hashlib
import json
import threading
import time
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parent.parent.parent / "work" / "wechat_ai_bridge_state" / "replies.jsonl"
_lock = threading.Lock()


def chat_id(peer):
    """Stable short id so the log can be filtered per chat without storing wxids."""
    return hashlib.sha256(str(peer).encode()).hexdigest()[:12]


def append(peer, text, config, is_group=False, path=None, now=None):
    if config is None or not config.get("reply_log_enabled", True):
        return
    path = Path(path or DEFAULT_PATH)
    now = time.time() if now is None else now
    row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now)),
           "kind": "group" if is_group else "private",
           "chat": chat_id(peer), "reply": str(text)}
    max_bytes = max(64 * 1024, int(config.get("reply_log_max_bytes", 2 * 1024 * 1024)))
    with _lock:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
            if path.stat().st_size > max_bytes:
                # Keep the newer half so the file stays bounded without losing recent history.
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines(True)
                path.write_text("".join(lines[len(lines) // 2:]), encoding="utf-8")
        except OSError:
            pass
