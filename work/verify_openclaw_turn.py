"""Verify the bridge's real ask_openclaw path works end to end.

Calls the exact function the bridge calls (with the same keyword arguments the
bridge passes), against a throwaway session key, so signature drift is caught
before a WeChat message hits it.
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from outputs.WeChatAI import bridge  # noqa: E402


def main():
    config = json.loads((ROOT / "outputs" / "WeChatAI" / "config.json").read_text(encoding="utf-8"))
    for peer, model, thinking in [
        ("diag-turn-check-deepseek@chatroom", "deepseek", "off"),
    ]:
        started = time.monotonic()
        try:
            reply = bridge.ask_openclaw(
                config, peer, model, thinking,
                bridge.prepare_model_prompt("只回复两个字：收到"),
                epoch=0,
            )
            print(f"{model}/{thinking}: {time.monotonic() - started:.2f}s reply={reply[:40]!r}")
        except Exception as exc:  # noqa: BLE001
            print(f"{model}/{thinking}: FAILED after {time.monotonic() - started:.2f}s "
                  f"{type(exc).__name__}: {exc}")


if __name__ == "__main__":
    main()
