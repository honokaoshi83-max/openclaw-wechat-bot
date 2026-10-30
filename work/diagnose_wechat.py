from pathlib import Path

from wechatauto.db import WeChatDB


root = Path(__file__).resolve().parent
db = WeChatDB(
    db_dir=r"D:\jilu\xwechat_files",
    account="<AI账号目录>",
    workdir=str(root / "wechat_db_cache"),
)
print("self_username:", db.get_self_info().get("username"))
sessions = db.get_sessions(limit=100)
print("session_count:", len(sessions))
for session in sessions:
    username = session["username"]
    if "@chatroom" in username:
        continue
    for msg in db.get_messages(username, limit=15):
        if "桥接测试123" in str(msg.get("content", "")):
            print("matched_chat:", username)
            print("sender_id:", msg.get("sender_id")); print("sender_username:", msg.get("sender_username"))
            print("message_type:", msg.get("type"))
            print("sort_seq:", msg.get("sort_seq"))
            print("create_time:", msg.get("create_time"))

