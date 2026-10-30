import unittest
from unittest.mock import MagicMock, patch

from outputs.WeChatAI import bridge, chat_engine

PEER = "1@chatroom"
ON = {"direct_chat_enabled": True, "direct_chat_peers": [PEER], "direct_chat_task_temperature": 0.7,
      "ai_sender_id": 1, "initial_model": "deepseek"}


class AskModelTests(unittest.TestCase):
    def test_rollout_peer_gets_one_direct_tool_free_call(self):
        engine = MagicMock(return_value=chat_engine.ChatReply("总结好了"))
        with patch.object(bridge.chat_engine, "complete", engine), patch.object(bridge, "ask_openclaw") as openclaw:
            reply = bridge.ask_model(ON, PEER, {}, "请总结：小王：吃了吗")
        openclaw.assert_not_called()
        self.assertEqual(reply, "总结好了")
        content = engine.call_args.args[1]
        self.assertTrue(content.startswith("【现在】"))
        self.assertIn("请总结：小王：吃了吗", content)
        self.assertNotIn("SOUL.md", content)
        self.assertEqual(engine.call_args.kwargs["temperature"], 0.7)
        self.assertNotIn("tool_runner", engine.call_args.kwargs)

    def test_other_peers_keep_the_exact_openclaw_prompt(self):
        with patch.object(bridge.chat_engine, "complete") as engine, \
                patch.object(bridge, "ask_openclaw", return_value="旧路径") as openclaw:
            reply = bridge.ask_model(ON, "wxid_private", {"session_epoch": 3}, "问题", auto_network=False)
        engine.assert_not_called()
        self.assertEqual(reply, "旧路径")
        self.assertEqual(openclaw.call_args.args[4], bridge.prepare_model_prompt("问题"))
        self.assertEqual(openclaw.call_args.kwargs, {"epoch": 3, "auto_network": False})


class FallbackTests(unittest.TestCase):
    direct =("这句是小王对你说的。", "", "小王")

    def run_chat(self, fallback, ambient=False):
        config = {**ON, "direct_chat_openclaw_fallback": fallback}
        with patch.object(bridge.chat_engine, "complete", side_effect=chat_engine.ChatEngineError("down")), \
                patch.object(bridge, "ask_openclaw", return_value="openclaw") as openclaw, \
                patch.object(bridge, "contextual_search_query", return_value=None):
            reply = bridge.ask_chat(config, PEER, {}, "prompt", direct=self.direct, text="在吗", ambient=ambient)
        return reply, openclaw

    def test_fallback_on_uses_openclaw(self):
        reply, openclaw = self.run_chat(True)
        self.assertEqual(reply, "openclaw")
        openclaw.assert_called_once()

    def test_fallback_off_answers_without_openclaw(self):
        reply, openclaw = self.run_chat(False)
        self.assertEqual(reply, bridge.DIRECT_FAILURE_REPLY)
        openclaw.assert_not_called()

    def test_fallback_off_keeps_failed_chime_ins_quiet(self):
        with self.assertRaises(chat_engine.ChatEngineError):
            self.run_chat(False, ambient=True)

    def test_ask_model_fallback_off(self):
        config = {**ON, "direct_chat_openclaw_fallback": False}
        with patch.object(bridge.chat_engine, "complete", side_effect=chat_engine.ChatEngineError("down")), \
                patch.object(bridge, "ask_openclaw") as openclaw:
            self.assertEqual(bridge.ask_model(config, PEER, {}, "总结"), bridge.DIRECT_FAILURE_REPLY)
        openclaw.assert_not_called()


class GroupFeatureRoutingTests(unittest.TestCase):
    class FakeDB:
        def get_group_members(self, group):
            return [{"username": "wxid_a", "nick_name": "小明"}]

        def get_messages(self, group, limit):
            return [{"sort_seq": 1, "sender_id": 2, "sender_username": "wxid_a", "content": "wxid_a:\n今天吃土豆"}]

    def run_task(self, task):
        state = {"chats": {PEER: {"model": "deepseek", "thinking": "off"}}}
        with patch.object(bridge, "ask_model", return_value="好") as ask_model, \
                patch.object(bridge, "ask_openclaw") as openclaw, \
                patch.object(bridge, "maybe_auto_compact"), patch.object(bridge, "save_state"), \
                patch.object(bridge, "send_wechat"):
            bridge.process_group_task(self.FakeDB(), ON, state, task)
        openclaw.assert_not_called()
        return ask_model

    def test_group_summary_uses_ask_model_with_the_transcript(self):
        ask_model = self.run_task({"peer": PEER, "request": "总结一下对话",
                                   "msg": {"sort_seq": 9, "sender_username": "wxid_a", "sender_id": 2,
                                           "content": "wxid_a:\n@大肥鱼 总结一下对话"}})
        self.assertIn("小明：今天吃土豆", ask_model.call_args.args[3])
        self.assertNotIn("wxid_", ask_model.call_args.args[3])

    def test_quote_comment_uses_ask_model_without_search(self):
        ask_model = self.run_task({"peer": PEER, "request": "评价一下", "quote_text": "地球是平的",
                                   "quote_comment": True,
                                   "msg": {"sort_seq": 9, "sender_username": "wxid_a", "sender_id": 2,
                                           "content": "wxid_a:\n<msg/>"}})
        self.assertIn("地球是平的", ask_model.call_args.args[3])
        self.assertIs(ask_model.call_args.kwargs["auto_network"], False)

    def test_search_synthesis_goes_direct_and_keeps_the_network_mark(self):
        engine = MagicMock(return_value=chat_engine.ChatReply("T1 赢了"))
        with patch.object(bridge, "searxng_results", return_value=(["标题：T1 夺冠"], [])), \
                patch.object(bridge, "fetch_public_page", return_value=""), \
                patch.object(bridge, "search_topic_path", return_value=MagicMock()), \
                patch.object(bridge.chat_engine, "complete", engine), \
                patch.object(bridge, "ask_openclaw") as openclaw:
            reply = bridge.summarize_search(ON, PEER, {"session_epoch": 0}, "决赛谁赢了")
        openclaw.assert_not_called()
        self.assertEqual(reply, "T1 赢了")
        self.assertTrue(reply.networked)
        self.assertIn("标题：T1 夺冠", engine.call_args.args[1])


if __name__ == "__main__":
    unittest.main()
