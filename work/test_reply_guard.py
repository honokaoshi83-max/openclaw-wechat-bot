import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from outputs.WeChatAI import bridge, check_persona, reply_guard, reply_log

RULES = json.loads((Path(check_persona.__file__).parent / "persona_rules.json").read_text(encoding="utf-8"))


class ReplyGuardTests(unittest.TestCase):
    def setUp(self):
        reply_guard.configure({})

    def tearDown(self):
        reply_guard.configure({})

    def test_blocks_local_paths_and_credentials(self):
        for text in (r"文件在 C:\Users\me\Documents\a.txt", "看 /mnt/c/Users/me 下面",
                     "在 /home/bot/.openclaw/workspace", "~/.openclaw 里有", "key 是 sk-abcdefghijklmnop1234",
                     "api_key=abcdef123456", "password: hunter2hunter2", r"\\wsl.localhost\Ubuntu"):
            self.assertEqual(reply_guard.guard(text), reply_guard.BLOCKED_REPLY, text)

    def test_allows_ordinary_replies_and_generic_paths(self):
        for text in ("下雨好啊 适合窝着", r"装在 C:\Program Files 就行", "/usr/bin/python3 是系统自带的",
                     "token 这个梗又来了", "密码别发群里"):
            self.assertEqual(reply_guard.guard(text), text, text)

    def test_blocks_exact_configured_secret(self):
        with patch.dict(os.environ, {"MY_KEY": "plainsecretvalue99"}):
            reply_guard.configure({"deepseek_api_key_env": "MY_KEY"})
        self.assertEqual(reply_guard.guard("给你 plainsecretvalue99"), reply_guard.BLOCKED_REPLY)

    def test_switch_disables_guard(self):
        reply_guard.configure({"reply_leak_guard_enabled": False})
        self.assertEqual(reply_guard.guard("sk-abcdefghijklmnop1234"), "sk-abcdefghijklmnop1234")

    def test_sanitize_reply_applies_guard_and_keeps_network_flag(self):
        original = bridge.ModelReply(r"在 C:\Users\me\x", networked=True)
        clean = bridge.sanitize_reply(original)
        self.assertEqual(clean, reply_guard.BLOCKED_REPLY)
        self.assertTrue(clean.networked)
        self.assertEqual(bridge.sanitize_reply("好家伙"), "好家伙")


class ReplyLogTests(unittest.TestCase):
    def test_append_writes_hashed_chat_and_kind(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "r.jsonl"
            reply_log.append("123@chatroom", "笑死", {}, is_group=True, path=path)
            row = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(row["kind"], "group")
        self.assertEqual(row["reply"], "笑死")
        self.assertNotIn("chatroom", row["chat"])

    def test_disabled_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "r.jsonl"
            reply_log.append("wxid_a", "hi", {"reply_log_enabled": False}, path=path)
            self.assertFalse(path.exists())

    def test_file_is_trimmed_to_newer_half(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "r.jsonl"
            config = {"reply_log_max_bytes": 64 * 1024}
            for i in range(1500):
                reply_log.append("wxid_a", f"第{i}句" + "啊" * 30, config, path=path)
            lines = path.read_text(encoding="utf-8").splitlines()
            self.assertLessEqual(path.stat().st_size, 64 * 1024 + 200)
            self.assertIn("第1499句", lines[-1])

    def test_confirmed_hook_send_is_logged(self):
        db = MagicMock()
        db.get_messages.side_effect = [[{"sort_seq": 1}],
                                       [{"sort_seq": 2, "sender_id": 7, "content": "嗯嗯"}]]
        with patch.object(bridge, "use_hook_transport", return_value=True), \
                patch.object(bridge, "send_wechat_hook"), \
                patch.object(bridge.reply_log, "append") as append:
            bridge.send_wechat(db, "9@chatroom", "嗯嗯", 7, config={"reply_log_enabled": True})
        append.assert_called_once()
        self.assertTrue(append.call_args.kwargs["is_group"])


class CheckPersonaTests(unittest.TestCase):
    def check(self, text):
        return {f.rule_id for f in check_persona.check_one(text, RULES, check_persona._rules(RULES))}

    def test_flags_assistant_voice_and_markup_leak(self):
        self.assertIn("R1-assistant-voice", self.check("作为一个AI，希望对你有帮助"))
        self.assertIn("R8-leak-markup", self.check("[聊天语气参考：本地语气线索=开心]"))
        self.assertIn("R9-system-error", self.check(reply_guard.BLOCKED_REPLY))
        self.assertIn("R16-wechat-emoji", self.check("哈哈[捂脸][旺柴]"))

    def test_persona_examples_are_clean(self):
        for text in ("欢迎回来 今天怎么样", "下雨好啊 适合窝着什么都不干[旺柴]", "那是毛绒 不是肉 谢谢"):
            self.assertEqual(self.check(text), set(), text)

    def test_density_rule_flags_commentary_tone(self):
        entries = [{"reply": t} for t in (
            "下路对线被吃干净了，团战也没声音，有点看不下去。", "选永恩就是赌，结果赌输了，还不如直接亮个前排",
            "四成的概率，听着像看天吃饭，真抽到韩华就不好办了", "换上去也只是救火，指望一个人把整局都拉回来，不太现实")]
        flagged = {f.rule_id for r in check_persona.analyze(entries, RULES) for f in r.findings}
        self.assertIn("D4-commentary-density", flagged)
        casual = [{"reply": t} for t in ("别看了 再看血压上去了", "赌狗的下场 唉", "藏这么久 大招是蓄力不成")]
        self.assertEqual([f for r in check_persona.analyze(casual, RULES) for f in r.findings], [])

    def test_density_rule_flags_repeated_benjing(self):
        entries = [{"reply": "本鲸才不胖"}, {"reply": "本鲸困了"}, {"reply": "好"}]
        results = check_persona.analyze(entries, RULES)
        self.assertIn("D1-benjing-density", {f.rule_id for f in results[1].findings})


if __name__ == "__main__":
    unittest.main()
