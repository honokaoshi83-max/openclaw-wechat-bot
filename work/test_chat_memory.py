import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from outputs.WeChatAI import bridge, chat_engine, chat_memory

PEER = "wxid_friend"
ON = {"direct_chat_enabled": True, "direct_chat_peers": [], "direct_chat_private": True,
      "direct_chat_task_temperature": 0.7, "direct_chat_memory_max_chars": 6000,
      "direct_chat_memory_keep_messages": 8}


class TempMemory(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        patcher = patch.object(chat_memory, "DEFAULT_DIR", Path(self.tmp.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)


class MemoryStoreTests(TempMemory):
    def test_turns_are_stored_per_peer_under_a_hashed_name(self):
        chat_memory.append(PEER, 0, "我叫小林", "记住了 小林")
        chat_memory.append("wxid_other", 0, "我叫阿花", "好")
        self.assertEqual([t["content"] for t in chat_memory.load(PEER, 0)["turns"]], ["我叫小林", "记住了 小林"])
        self.assertNotIn("小林", json.dumps(chat_memory.load("wxid_other", 0), ensure_ascii=False))
        self.assertFalse(any("wxid" in p.name for p in Path(self.tmp.name).iterdir()))

    def test_a_new_epoch_reads_as_empty(self):
        chat_memory.append(PEER, 0, "a", "b")
        self.assertEqual(chat_memory.load(PEER, 1), chat_memory.empty(1))

    def test_compaction_folds_older_turns_and_keeps_the_latest(self):
        for i in range(10):
            chat_memory.append(PEER, 0, f"问{i}" + "啊" * 400, f"答{i}")
        summarize = MagicMock(return_value="对方问过很多问题")
        before, after = chat_memory.compact(PEER, 0, summarize, max_chars=3000, keep_messages=4)
        memory = chat_memory.load(PEER, 0)
        self.assertEqual(memory["summary"], "对方问过很多问题")
        self.assertEqual([t["content"] for t in memory["turns"]][-1], "答9")
        self.assertEqual(len(memory["turns"]), 4)
        self.assertEqual(len(summarize.call_args.args[1]), 16)
        self.assertLess(after, before)

    def test_compaction_waits_until_memory_is_large_unless_forced(self):
        for i in range(6):
            chat_memory.append(PEER, 0, f"问{i}", f"答{i}")
        summarize = MagicMock(return_value="摘要")
        self.assertIsNone(chat_memory.compact(PEER, 0, summarize, max_chars=6000, keep_messages=4))
        summarize.assert_not_called()
        self.assertIsNotNone(chat_memory.compact(PEER, 0, summarize, max_chars=6000, keep_messages=4, force=True))

    def test_seed_never_overwrites_existing_memory(self):
        self.assertTrue(chat_memory.seed_summary(PEER, 0, "导入的摘要"))
        self.assertFalse(chat_memory.seed_summary(PEER, 0, "第二次导入"))
        self.assertEqual(chat_memory.load(PEER, 0)["summary"], "导入的摘要")

    def test_clear_removes_the_file(self):
        chat_memory.append(PEER, 0, "a", "b")
        chat_memory.clear(PEER)
        self.assertEqual(chat_memory.load(PEER, 0), chat_memory.empty(0))


class PromptTests(unittest.TestCase):
    def test_memory_sits_after_the_shared_cacheable_prefix(self):
        group = chat_engine.build_messages({}, "x")
        private = chat_engine.build_messages({}, "x", history=[{"role": "user", "content": "上一句"},
                                                              {"role": "assistant", "content": "上一答"}],
                                             summary="对方叫小林")
        prefix = group[:-1]
        self.assertEqual(private[:len(prefix)], prefix)
        self.assertEqual(private[len(prefix)]["role"], "system")
        self.assertIn("对方叫小林", private[len(prefix)]["content"])
        self.assertEqual(private[-3]["content"], "上一句")
        self.assertEqual(private[-1], {"role": "user", "content": "x"})

    def test_private_switch_is_separate_from_groups(self):
        self.assertTrue(chat_engine.enabled_for(ON, PEER))
        self.assertFalse(chat_engine.enabled_for({**ON, "direct_chat_private": False}, PEER))
        self.assertFalse(chat_engine.enabled_for(ON, "1@chatroom"))

    def test_summarize_memory_treats_nothing_to_keep_as_empty(self):
        body = json.dumps({"choices": [{"message": {"content": "无"}}]}).encode()
        resp = MagicMock()
        resp.read.return_value = body
        resp.__enter__.return_value = resp
        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "k"}), patch("urllib.request.urlopen", return_value=resp):
            self.assertEqual(chat_engine.summarize_memory({}, "", [{"role": "user", "content": "嗯"}]), "")


class AskPrivateTests(TempMemory):
    def test_direct_reply_carries_this_chats_memory(self):
        chat_memory.seed_summary(PEER, 0, "对方叫小林，喜欢雨天")
        chat_memory.append(PEER, 0, "推荐个电影", "看《海街日记》")
        engine = MagicMock(return_value=chat_engine.ChatReply("《小森林》也行"))
        with patch.object(bridge.chat_engine, "complete", engine), patch.object(bridge, "ask_openclaw") as openclaw, \
                patch.object(bridge, "contextual_search_query", return_value=None):
            reply = bridge.ask_private(ON, PEER, {"session_epoch": 0}, "prompt", "还有别的吗")
        openclaw.assert_not_called()
        self.assertEqual(reply, "《小森林》也行")
        kwargs = engine.call_args.kwargs
        self.assertEqual(kwargs["summary"], "对方叫小林，喜欢雨天")
        self.assertEqual([m["content"] for m in kwargs["history"]], ["推荐个电影", "看《海街日记》"])
        self.assertTrue(engine.call_args.args[1].startswith("【现在】"))
        self.assertIn("还有别的吗", engine.call_args.args[1])

    def test_switch_off_keeps_openclaw(self):
        with patch.object(bridge.chat_engine, "complete") as engine, \
                patch.object(bridge, "ask_openclaw", return_value="旧") as openclaw:
            self.assertEqual(bridge.ask_private({**ON, "direct_chat_private": False}, PEER,
                                                {"session_epoch": 2}, "prompt", "在吗"), "旧")
        engine.assert_not_called()
        self.assertEqual(openclaw.call_args.kwargs["epoch"], 2)

    def test_remember_folds_once_over_budget_and_never_breaks_on_failure(self):
        config = {**ON, "direct_chat_memory_max_chars": 30, "direct_chat_memory_keep_messages": 2}
        with patch.object(bridge.chat_engine, "summarize_memory", return_value="早先聊了天气") as summarize:
            # Over budget, but with only the latest turn there is nothing older to fold yet.
            bridge.remember_private_turn(config, PEER, {"session_epoch": 0}, "今天好热" * 10, "是啊")
            summarize.assert_not_called()
            bridge.remember_private_turn(config, PEER, {"session_epoch": 0}, "明天呢", "也热")
        self.assertEqual(chat_memory.load(PEER, 0)["summary"], "早先聊了天气")
        summarize.assert_called_once()
        with patch.object(bridge.chat_engine, "summarize_memory", side_effect=chat_engine.ChatEngineError("down")):
            bridge.remember_private_turn(config, PEER, {"session_epoch": 0}, "后天" * 20, "还热")
        self.assertEqual(chat_memory.load(PEER, 0)["turns"][-1]["content"], "还热")

    def test_groups_and_disabled_chats_are_not_remembered(self):
        bridge.remember_private_turn(ON, "1@chatroom", {"session_epoch": 0}, "a", "b")
        bridge.remember_private_turn({**ON, "direct_chat_private": False}, PEER, {"session_epoch": 0}, "a", "b")
        self.assertEqual(list(Path(self.tmp.name).glob("*.json")), [])

    def test_compact_and_clear_commands_use_direct_memory(self):
        for i in range(6):
            chat_memory.append(PEER, 0, f"问{i}", f"答{i}")
        with patch.object(bridge.chat_engine, "summarize_memory", return_value="摘要"), \
                patch.object(bridge, "compact_openclaw_session") as openclaw:
            self.assertIn("记忆已整理", bridge.compact_session_reply(ON, PEER, {"session_epoch": 0}))
        openclaw.assert_not_called()
        chat = {"session_epoch": 0}
        with patch.object(bridge, "STICKER_LIBRARY_PATH", None):
            bridge.clear_chat_memory({"chats": {PEER: chat}}, PEER, chat)
        self.assertEqual(chat_memory.load(PEER, 0), chat_memory.empty(0))


class PrivateFlowTests(TempMemory):
    def test_two_turns_end_to_end_the_second_sees_the_first(self):
        peer = PEER
        messages = []

        class FakeDB:
            def get_sessions(self, limit):
                return [{"username": peer}]

            def get_new_messages(self, requested_peer, since_seq, limit):
                return [m for m in messages if m["sort_seq"] > since_seq][:limit] if requested_peer == peer else []

        chat = {"last_seq": 0, "model": "deepseek", "thinking": "off", "humanized": False,
                "daily_help_date": "2026-10-30", "session_epoch": 0}
        state = {"own_user": "wxid_ai", "activated_at": 0, "chats": {peer: chat}}
        config = {**ON, "send_mode": "hook", "ai_sender_id": 1, "initial_model": "deepseek",
                  "humanized_delay_seconds": 0, "merge_bursts_enabled": False}
        seen = []

        def engine(cfg, content, **kwargs):
            seen.append(kwargs.get("history") or [])
            return chat_engine.ChatReply("看《海街日记》" if len(seen) == 1 else "那就《小森林》")

        with patch.object(bridge, "save_state"), patch.object(bridge, "is_tokyo_quiet_hours", return_value=False), \
                patch.object(bridge.chat_engine, "complete", side_effect=engine), \
                patch.object(bridge, "ask_openclaw", side_effect=AssertionError("OpenClaw called")), \
                patch.object(bridge, "contextual_search_query", return_value=None), \
                patch.object(bridge, "send_wechat_sequence") as send:
            for seq, text in ((1000, "推荐个电影"), (2000, "还有别的吗")):
                messages.append({"sort_seq": seq, "sender_id": 2, "type": "文本", "content": text,
                                 "sender_username": peer, "local_id": seq})
                bridge.process_new_messages(FakeDB(), config, state)

        self.assertEqual(send.call_count, 2)
        self.assertEqual(seen[0], [])
        self.assertEqual([m["content"] for m in seen[1]], ["推荐个电影", "看《海街日记》"])
        self.assertEqual(len(chat_memory.load(peer, 0)["turns"]), 4)


if __name__ == "__main__":
    unittest.main()
