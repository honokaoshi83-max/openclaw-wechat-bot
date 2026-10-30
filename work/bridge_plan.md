# Dedicated WeChat AI bridge plan

Goal: Have the Windows WeChat client logged into the AI account answer messages from the verified main account only.

1. Verify the AI account database and locate the main account through the unique test message. Done: AI `<AI账号wxid>`, main `<主账号wxid>`.
2. Keep an isolated Python 3.12 environment with the pinned `wechatauto-replica` checkout. Done: commit `f4fdf73621f667bb68df221c4301be7222770bf6`.
3. Build a bridge that polls only the main account conversation, ignores its own outgoing messages, persists a watermark before processing, supports `/qwen` and `/deepseek`, asks OpenClaw through its CLI, and sends through the logged-in Windows WeChat client.
4. Test filtering and state transitions with synthetic records, then verify a controlled real send to the main account and a fresh main-to-AI message.
5. Start the bridge for the current desktop session, document its log, startup, and stop commands. Add automatic start only after the live test succeeds.
