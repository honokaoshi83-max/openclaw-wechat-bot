"""Per-chat memory for direct DeepSeek replies: recent turns plus a rolling summary.

One file per chat, named by a hash of the peer so no wxid appears on disk. The file
remembers the session epoch it belongs to; /reset and /clear bump the epoch, so an
old file simply reads as empty and is overwritten on the next turn.
"""
import hashlib
import json
import os
import threading
from pathlib import Path

DEFAULT_DIR = Path(__file__).resolve().parent.parent.parent / "work" / "wechat_ai_bridge_state" / "chat_memory"
MAX_TURN_CHARS = 2000
_guard = threading.Lock()
_locks = {}


def _lock(peer):
    with _guard:
        return _locks.setdefault(peer, threading.RLock())


def _path(peer, directory):
    return Path(directory or DEFAULT_DIR) / (hashlib.sha256(str(peer).encode()).hexdigest()[:16] + ".json")


def empty(epoch):
    return {"epoch": int(epoch), "summary": "", "turns": []}


def load(peer, epoch, directory=None):
    try:
        data = json.loads(_path(peer, directory).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return empty(epoch)
    if not isinstance(data, dict) or int(data.get("epoch", -1)) != int(epoch):
        return empty(epoch)
    return {"epoch": int(epoch), "summary": str(data.get("summary") or ""),
            "turns": [t for t in data.get("turns", []) if isinstance(t, dict)]}


def _save(peer, data, directory):
    path = _path(peer, directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def history_messages(memory):
    return [{"role": t["role"], "content": t["content"]} for t in memory["turns"]
            if t.get("role") in ("user", "assistant") and t.get("content")]


def size(memory):
    return len(memory["summary"]) + sum(len(t.get("content", "")) for t in memory["turns"])


def append(peer, epoch, user_text, reply, directory=None):
    with _lock(peer):
        memory = load(peer, epoch, directory)
        memory["turns"] += [{"role": "user", "content": str(user_text)[:MAX_TURN_CHARS]},
                            {"role": "assistant", "content": str(reply)[:MAX_TURN_CHARS]}]
        _save(peer, memory, directory)
        return memory


def compact(peer, epoch, summarize, max_chars, keep_messages=8, force=False, directory=None):
    """Fold the older turns into the summary once memory passes ``max_chars``.

    ``summarize(old_summary, turns) -> str`` does the folding. Returns
    ``(chars_before, chars_after)``, or None when there was nothing to fold.
    """
    with _lock(peer):
        memory = load(peer, epoch, directory)
        older = memory["turns"][:-keep_messages] if keep_messages else memory["turns"]
        if not older or (not force and size(memory) <= max_chars):
            return None
        before = size(memory)
        memory["summary"] = str(summarize(memory["summary"], older) or "").strip()
        memory["turns"] = memory["turns"][len(older):]
        _save(peer, memory, directory)
        return before, size(memory)


def seed_summary(peer, epoch, summary, directory=None):
    """Store an imported summary, but never over a memory this chat already has."""
    with _lock(peer):
        memory = load(peer, epoch, directory)
        if memory["summary"] or memory["turns"]:
            return False
        memory["summary"] = str(summary).strip()
        _save(peer, memory, directory)
        return True


def clear(peer, directory=None):
    with _lock(peer):
        try:
            _path(peer, directory).unlink()
        except FileNotFoundError:
            pass
