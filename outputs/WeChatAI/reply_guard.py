"""Last-line check that keeps local paths and credentials out of model replies."""
import logging
import os
import re
import threading

LOG = logging.getLogger("wechat_ai_bridge")
BLOCKED_REPLY = "这条我不方便发。"
# Only paths that point into this machine's accounts or the OpenClaw workspace; generic
# examples like C:\Program Files or /usr/bin stay allowed for ordinary tech answers.
LEAK = re.compile(
    r"[A-Za-z]:[\\/]+Users[\\/]"
    r"|\\\\wsl(?:\$|\.localhost)"
    r"|/mnt/[a-z]/"
    r"|/home/[^\s/]+/"
    r"|/root/"
    r"|\.openclaw\b"
    r"|\bsk-[A-Za-z0-9_-]{16,}"
    r"|\b(?:api[_-]?key|access[_-]?key|secret|password|passwd|token)\s*[:=：]\s*\S{8,}",
    re.I,
)
_lock = threading.Lock()
_enabled = True
_secrets = ()


def configure(config):
    """Load the switch and the real secret values named by the config's env-var fields."""
    global _enabled, _secrets
    names = [str(v) for k, v in config.items() if k.endswith("_env") and isinstance(v, str)]
    names.append(str(config.get("deepseek_api_key_env", "DEEPSEEK_API_KEY")))
    values = {os.environ.get(name, "").strip() for name in names}
    with _lock:
        _enabled = bool(config.get("reply_leak_guard_enabled", True))
        _secrets = tuple(v for v in values if len(v) >= 8)


def leaks(text):
    with _lock:
        enabled, secrets = _enabled, _secrets
    if not enabled or not text:
        return False
    return bool(LEAK.search(text)) or any(s in text for s in secrets)


def guard(text):
    """Return the reply unchanged, or the fixed refusal when it would leak."""
    if not leaks(text):
        return text
    LOG.warning("Reply guard blocked a reply containing a local path or credential")
    return BLOCKED_REPLY
