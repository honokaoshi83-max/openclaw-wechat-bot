"""Export each private chat's OpenClaw session as a memory summary for the direct engine.

Run once before switching private chats to direct (direct_chat_private). Each chat's
own OpenClaw session writes the summary, since it holds the full history including what
it already compacted. Existing direct memory is never overwritten, so a rerun is safe.
Only lengths and checks are printed, never the summaries themselves.

    work\\.venv\\Scripts\\python.exe work\\migrate_openclaw_memory.py --dry-run
    work\\.venv\\Scripts\\python.exe work\\migrate_openclaw_memory.py
"""
import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from outputs.WeChatAI import bridge, chat_engine, chat_memory, reply_guard  # noqa: E402

EXPORT_PROMPT = ("[系统维护，不是聊天消息，也不要回复对方] 请把你和这位用户到目前为止的全部对话，"
                 "整理成一份记忆摘要，给你以后接着和他聊时参考。" + chat_engine.MEMORY_RULES)
LEAK = re.compile(r"wxid_[a-z0-9]{6,}|\d{8,}@chatroom")
WSL = ["wsl.exe", "-d", "OpenClawGateway", "-u", "openclaw"]


def wsl(*args, timeout=120):
    result = subprocess.run([*WSL, *args], capture_output=True, text=True, encoding="utf-8",
                            timeout=timeout, **bridge.openclaw_run_options())
    if result.returncode:
        raise RuntimeError(f"wsl exited {result.returncode}")
    return result.stdout


def transcript_turns(config, peer, epoch, label):
    """Read an archived session's own transcript through OpenClaw's redacted export.

    Archived sessions refuse new turns, so they cannot write their own summary. The
    export copy is deleted as soon as it has been read.
    """
    out = f"memmigrate-{label}"
    info = json.loads(wsl("--cd", "~", "-e", "openclaw", "sessions", "export-trajectory", "--agent", config["agent"],
                          "--session-key", bridge.openclaw_session_key(config, peer, epoch), "--output", out, "--json"))
    try:
        events = [json.loads(line) for line in wsl("-e", "cat", info["outputDir"] + "/events.jsonl").splitlines()
                  if line.strip()]
    finally:
        wsl("-e", "rm", "-rf", info["outputDir"])
    turns = []
    for event in events:
        message = (event.get("data") or {}).get("message") or {}
        if event.get("type") == "user.message":
            # The bridge appends its bracketed instructions after the user's words.
            text = str(message.get("content") or "").split("\n\n[", 1)[0].strip()
            role = "user"
        elif event.get("type") == "assistant.message":
            parts = message.get("content") or []
            text = "".join(p.get("text", "") for p in parts if isinstance(p, dict) and p.get("type") == "text").strip()
            role = "assistant"
        else:
            continue
        if text:
            turns.append({"role": role, "content": text[:chat_memory.MAX_TURN_CHARS]})
    return turns


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="export and check, but write nothing")
    args = parser.parse_args()
    config = json.loads(bridge.CONFIG_PATH.read_text(encoding="utf-8"))
    reply_guard.configure(config)
    state = json.loads(bridge.STATE_PATH.read_text(encoding="utf-8"))
    own_user = state.get("own_user", "")
    for peer, chat in state.get("chats", {}).items():
        if not bridge.is_direct_peer(peer, own_user):
            continue
        label = hashlib.sha256(peer.encode()).hexdigest()[:12]
        epoch = int(chat.get("session_epoch", 0))
        memory = chat_memory.load(peer, epoch)
        if memory["summary"] or memory["turns"]:
            print(f"{label}: 已有直连记忆，跳过")
            continue
        usage = bridge.get_openclaw_session_usage(config, peer, epoch, timeout=30)
        if not usage or not usage.get("totalTokens"):
            print(f"{label}: OpenClaw 里没有这个会话，从零开始")
            continue
        try:
            summary = str(bridge.ask_openclaw(
                config, peer, "deepseek", "off", EXPORT_PROMPT, epoch=epoch, auto_network=False)).strip()
            how = "会话自己整理"
        except Exception:  # noqa: BLE001 - an archived session refuses new turns; read its transcript instead
            try:
                turns = transcript_turns(config, peer, epoch, label)
                summary = chat_engine.summarize_memory(config, "", turns) if turns else ""
                how = f"导出 {len(turns)} 条记录后整理"
            except Exception as exc:  # noqa: BLE001 - report and move on to the next chat
                print(f"{label}: 导出失败（{type(exc).__name__}），这个会话从零开始")
                continue
        summary = str(reply_guard.guard(summary)).strip()
        if summary in ("", "无", "无。") or summary == reply_guard.BLOCKED_REPLY:
            print(f"{label}: 没有可导入的内容（会话 {usage.get('totalTokens')} tokens）")
            continue
        if LEAK.search(summary):
            print(f"{label}: 摘要里出现了 wxid 或群号，未导入，请人工检查")
            continue
        if args.dry_run:
            print(f"{label}: [试运行] {how}，摘要 {len(summary)} 字")
            continue
        stored = chat_memory.seed_summary(peer, epoch, summary)
        print(f"{label}: {how}，摘要 {len(summary)} 字，" + ("已导入" if stored else "期间已有新记忆，未覆盖"))


if __name__ == "__main__":
    main()
