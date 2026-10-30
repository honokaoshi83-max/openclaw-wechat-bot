import json
import unittest
import urllib.error
from datetime import datetime
from unittest.mock import MagicMock, patch

from outputs.WeChatAI import bridge, chat_engine

PEER = "1@chatroom"
CONFIG = {"direct_chat_enabled": True, "direct_chat_peers": [PEER]}


def response(text, finish="stop"):
    body = json.dumps({"choices": [{"message": {"content": text}, "finish_reason": finish}],
                       "usage": {"prompt_tokens": 10}}).encode()
    resp = MagicMock()
    resp.read.return_value = body
    resp.__enter__.return_value = resp
    return resp


class EngineTests(unittest.TestCase):
    def test_rollout_is_per_peer_and_switchable(self):
        self.assertTrue(chat_engine.enabled_for(CONFIG, PEER))
        self.assertFalse(chat_engine.enabled_for(CONFIG, "2@chatroom"))
        self.assertFalse(chat_engine.enabled_for({**CONFIG, "direct_chat_enabled": False}, PEER))
        all_groups = {**CONFIG, "direct_chat_all_groups": True}
        self.assertTrue(chat_engine.enabled_for(all_groups, "2@chatroom"))
        self.assertFalse(chat_engine.enabled_for(all_groups, "wxid_private"))
        self.assertFalse(chat_engine.enabled_for({**all_groups, "direct_chat_enabled": False}, "2@chatroom"))

    def test_messages_keep_a_stable_cacheable_prefix(self):
        a = chat_engine.build_messages({}, "第一句")
        b = chat_engine.build_messages({}, "完全不同的第二句")
        self.assertEqual(a[:-1], b[:-1])
        self.assertEqual(a[0]["role"], "system")
        roles = [m["role"] for m in a[1:-1]]
        self.assertEqual(roles, ["user", "assistant"] * (len(roles) // 2))
        self.assertGreaterEqual(len(roles) // 2, 15)
        self.assertEqual(b[-1], {"role": "user", "content": "完全不同的第二句"})

    def test_group_prompt_puts_time_and_chat_in_user_message(self):
        text = chat_engine.group_prompt("你没被点名", "小王：好", "阿杰", "下路崩了",
                                        now=datetime(2026, 10, 30, 9, 5, tzinfo=chat_engine.TOKYO))
        self.assertIn("2026年10月30日 星期五 09:05", text)
        self.assertIn("小王：好", text)
        self.assertTrue(text.endswith("阿杰：下路崩了"))

    def test_strips_name_prefix(self):
        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "k"}), \
                patch("urllib.request.urlopen", return_value=response("蓝鲸：啊这")):
            self.assertEqual(chat_engine.complete({}, "x"), "啊这")
        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "k"}), \
                patch("urllib.request.urlopen", return_value=response("赌狗没有好下场[]")):
            self.assertEqual(chat_engine.complete({}, "x"), "赌狗没有好下场")
        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "k"}), \
                patch("urllib.request.urlopen", return_value=response("我这没对准，不敢硬报[citation:来源]。")):
            self.assertEqual(chat_engine.complete({}, "x"), "我这没对准，不敢硬报。")

    def test_empty_reply_raises(self):
        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "k"}), \
                patch("urllib.request.urlopen", return_value=response("  ")):
            with self.assertRaises(chat_engine.ChatEngineError):
                chat_engine.complete({}, "x")

    def test_server_error_is_retried_once(self):
        error = urllib.error.HTTPError("u", 503, "busy", {}, None)
        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "k"}), patch("time.sleep"), \
                patch("urllib.request.urlopen", side_effect=[error, response("在")]) as urlopen:
            self.assertEqual(chat_engine.complete({}, "x"), "在")
        self.assertEqual(urlopen.call_count, 2)

    def test_client_error_is_not_retried(self):
        error = urllib.error.HTTPError("u", 401, "auth", {}, None)
        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "k"}), \
                patch("urllib.request.urlopen", side_effect=[error]) as urlopen:
            with self.assertRaises(chat_engine.ChatEngineError):
                chat_engine.complete({}, "x")
        self.assertEqual(urlopen.call_count, 1)

    def test_missing_key_raises(self):
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(chat_engine.ChatEngineError):
                chat_engine.complete({}, "x")


class RoutingTests(unittest.TestCase):
    chat = {"session_epoch": 0}
    direct = ("你没被点名", "", "小王")

    def route(self, peer=PEER, text="哈哈哈", direct=direct, engine=None, auto_network=True):
        engine = engine or MagicMock(return_value="笑死")
        with patch.object(bridge.chat_engine, "complete", engine), \
                patch.object(bridge, "ask_openclaw", return_value="openclaw") as openclaw, \
                patch.object(bridge, "contextual_search_query", return_value=None):
            reply = bridge.ask_chat(CONFIG, peer, self.chat, "prompt", direct=direct, text=text,
                                    auto_network=auto_network)
        return reply, engine, openclaw

    def test_rollout_peer_casual_turn_uses_engine(self):
        reply, engine, openclaw = self.route()
        self.assertEqual(reply, "笑死")
        openclaw.assert_not_called()

    def test_other_peers_and_non_chat_turns_stay_on_openclaw(self):
        for kwargs in ({"peer": "2@chatroom"}, {"direct": None}):
            reply, engine, openclaw = self.route(**kwargs)
            self.assertEqual(reply, "openclaw")
            engine.assert_not_called()

    def test_turns_needing_search_stay_on_openclaw(self):
        reply, engine, _ = self.route(text="看看 https://example.com")
        self.assertEqual(reply, "openclaw")
        engine.assert_not_called()
        reply, engine, _ = self.route(text="看看 https://example.com", auto_network=False)
        self.assertEqual(reply, "笑死")

    def test_engine_failure_falls_back_to_openclaw(self):
        failing = MagicMock(side_effect=chat_engine.ChatEngineError("down"))
        reply, _, openclaw = self.route(engine=failing)
        self.assertEqual(reply, "openclaw")
        openclaw.assert_called_once()



def tool_call_response(*calls):
    message = {"content": "", "tool_calls": [
        {"id": f"c{i}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}
        for i, (name, args) in enumerate(calls)]}
    body = json.dumps({"choices": [{"message": message, "finish_reason": "tool_calls"}],
                       "usage": {"prompt_tokens": 10}}).encode()
    resp = MagicMock()
    resp.read.return_value = body
    resp.__enter__.return_value = resp
    return resp


class ToolTests(unittest.TestCase):
    def run_engine(self, responses, runner):
        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "k"}), \
                patch("urllib.request.urlopen", side_effect=responses) as urlopen:
            reply = chat_engine.complete({}, "今天谁赢了", tool_runner=runner)
        payloads = [json.loads(call.args[0].data) for call in urlopen.call_args_list]
        return reply, payloads

    def test_search_result_is_fed_back_and_marks_reply_networked(self):
        runner = MagicMock(return_value=("标题：T1 夺冠", True))
        reply, payloads = self.run_engine(
            [tool_call_response(("web_search", {"query": "决赛 结果"})), response("T1 赢了 好家伙")], runner)
        self.assertEqual(reply, "T1 赢了 好家伙")
        self.assertTrue(reply.networked)
        runner.assert_called_once_with("web_search", {"query": "决赛 结果"})
        self.assertIn("【查资料】", payloads[0]["messages"][0]["content"])
        self.assertEqual(payloads[1]["messages"][-1], {"role": "tool", "tool_call_id": "c0", "content": "标题：T1 夺冠"})

    def test_search_budget_is_enforced(self):
        runner = MagicMock(return_value=("资料", True))
        _, payloads = self.run_engine(
            [tool_call_response(("web_search", {"query": "a"}), ("web_search", {"query": "b"})), response("好")],
            runner)
        self.assertEqual(runner.call_count, 1)
        self.assertIn("次数用完", payloads[1]["messages"][-1]["content"])

    def test_last_round_offers_no_tools_so_the_model_must_answer(self):
        runner = MagicMock(return_value=("资料", True))
        calls = [tool_call_response(("web_fetch", {"url": "https://a.example"}))] * chat_engine.MAX_TOOL_ROUNDS
        reply, payloads = self.run_engine(calls + [response("查不太清 不确定")], runner)
        self.assertEqual(reply, "查不太清 不确定")
        self.assertIn("tools", payloads[0])
        self.assertNotIn("tools", payloads[-1])

    def test_tool_failure_is_reported_to_the_model_not_raised(self):
        runner = MagicMock(side_effect=OSError("searxng down"))
        reply, payloads = self.run_engine(
            [tool_call_response(("web_search", {"query": "a"})), response("没查到 不确定")], runner)
        self.assertFalse(reply.networked)
        self.assertIn("查询失败", payloads[1]["messages"][-1]["content"])

    def test_without_runner_no_tools_are_sent(self):
        reply, payloads = self.run_engine([response("在")], None)
        self.assertNotIn("tools", payloads[0])
        self.assertNotIn("【查资料】", payloads[0]["messages"][0]["content"])
        self.assertFalse(reply.networked)


class ToolRoutingTests(unittest.TestCase):
    config = {**CONFIG, "direct_chat_tools_enabled": True}

    def route(self, text, auto_network=True):
        engine = MagicMock(return_value=chat_engine.ChatReply("答", True))
        with patch.object(bridge.chat_engine, "complete", engine), \
                patch.object(bridge, "ask_openclaw", return_value="openclaw") as openclaw, \
                patch.object(bridge, "contextual_search_query", return_value=None):
            reply = bridge.ask_chat(self.config, PEER, {"session_epoch": 0}, "prompt",
                                    direct=("这句是小王对你说的。", "", "小王"), text=text,
                                    auto_network=auto_network)
        return reply, engine, openclaw

    def test_search_turns_use_the_engine_with_tools_and_a_hint(self):
        reply, engine, openclaw = self.route("看看 https://example.com")
        openclaw.assert_not_called()
        self.assertTrue(reply.networked)
        self.assertIsNotNone(engine.call_args.kwargs["tool_runner"])
        self.assertIn("先查再答", engine.call_args.args[1])

    def test_visual_turns_get_no_tools(self):
        _, engine, _ = self.route("[表情包]", auto_network=False)
        self.assertIsNone(engine.call_args.kwargs["tool_runner"])

    def test_sanitize_keeps_the_network_mark(self):
        self.assertTrue(bridge.sanitize_reply(chat_engine.ChatReply("T1 赢了", True)).networked)

    def test_runner_searches_searxng_and_records_follow_up_topic(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as d, \
                patch.object(bridge, "searxng_results", return_value=(["标题：T1"], [])) as search, \
                patch.object(bridge, "search_topic_path", return_value=Path(d) / "t.json"):
            text, ok = bridge.chat_tool_runner({}, PEER, 0)("web_search", {"query": "决赛"})
            topic = json.loads((Path(d) / "t.json").read_text(encoding="utf-8"))
        self.assertEqual((text, ok), ("标题：T1", True))
        search.assert_called_once_with({}, "决赛", include_links=True)
        self.assertEqual(topic["query"], "决赛")

    def test_runner_fetch_uses_the_public_page_reader(self):
        with patch.object(bridge, "fetch_public_page", return_value="正文") as fetch:
            text, ok = bridge.chat_tool_runner({}, PEER, 0)("web_fetch", {"url": "https://a.example"})
        fetch.assert_called_once_with("https://a.example")
        self.assertTrue(ok)
        self.assertIn("正文", text)


class TaskIntentTests(unittest.TestCase):
    def test_task_markers_separate_requests_from_banter(self):
        for text in ("大肥鱼 你预测一下今晚谁会赢", "客观分析一下两边", "你猜他为啥生气", "帮我想个群名",
                     "BLG 下一场你押几比几", "BLG 能不能进四强"):
            self.assertTrue(bridge.is_task_request(text), text)
        for text in ("哈哈哈 笑死", "今天食堂又是土豆", "在吗"):
            self.assertFalse(bridge.is_task_request(text), text)

    def route(self, task):
        engine = MagicMock(return_value=chat_engine.ChatReply("我押GEN"))
        config = {**CONFIG, "direct_chat_task_temperature": 0.7}
        with patch.object(bridge.chat_engine, "complete", engine), \
                patch.object(bridge, "ask_openclaw"), patch.object(bridge, "contextual_search_query", return_value=None):
            bridge.ask_chat(config, PEER, {"session_epoch": 0}, "prompt",
                            direct=("这句是小王对你说的。", "", "小王"), text="你预测一下谁会赢", task=task)
        return engine.call_args

    def test_task_turns_get_the_hint_and_a_lower_temperature(self):
        call = self.route(task=True)
        self.assertIn("这是在请你做事或下判断", call.args[1])
        self.assertEqual(call.kwargs["temperature"], 0.7)

    def test_banter_keeps_the_default_temperature_and_no_hint(self):
        call = self.route(task=False)
        self.assertNotIn("这是在请你做事或下判断", call.args[1])
        self.assertIsNone(call.kwargs["temperature"])

    def test_chime_ins_never_search_or_follow_up_an_old_topic(self):
        engine = MagicMock(return_value=chat_engine.ChatReply("又下雨 适合瘫着"))
        config = {**CONFIG, "direct_chat_tools_enabled": True}
        with patch.object(bridge.chat_engine, "complete", engine), patch.object(bridge, "ask_openclaw") as openclaw, \
                patch.object(bridge, "contextual_search_query", return_value="BLG 赛程；追问：周末去哪玩啊"):
            bridge.ask_chat(config, PEER, {"session_epoch": 0}, "prompt",
                            direct=("你没被点名，是看到阿杰这句话想插一句。", "", "阿杰"), text="周末去哪玩啊",
                            ambient=True)
        openclaw.assert_not_called()
        self.assertIsNone(engine.call_args.kwargs["tool_runner"])
        self.assertNotIn("先查再答", engine.call_args.args[1])
        self.assertIn("接的是最后这一句本身", engine.call_args.args[1])

    def test_complete_uses_the_given_temperature(self):
        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "k"}), \
                patch("urllib.request.urlopen", return_value=response("好")) as urlopen:
            chat_engine.complete({"direct_chat_temperature": 1.3}, "x", temperature=0.7)
        self.assertEqual(json.loads(urlopen.call_args.args[0].data)["temperature"], 0.7)

    def test_examples_no_longer_teach_dodging(self):
        answers = " ".join(item["a"] for item in chat_engine._read("examples.json")["items"])
        for dodge in ("这我哪知道", "别问我", "乱报"):
            self.assertNotIn(dodge, answers)

    def test_addressed_requests_are_tasks_but_chime_ins_are_not(self):
        class FakeDB:
            def get_group_members(self, group):
                return [{"username": "wxid_a", "nick_name": "小明"}]

            def get_messages(self, group, limit):
                return []

        for ambient, expected in ((False, True), (True, False)):
            task = {"peer": PEER, "request": "你预测一下谁会赢", "ambient": ambient,
                    "msg": {"sort_seq": 4000, "sender_username": "wxid_a", "sender_id": 2,
                            "content": "wxid_a:\n@大肥鱼 你预测一下谁会赢"}}
            state = {"chats": {PEER: {"model": "deepseek", "thinking": "off"}}}
            with patch.object(bridge, "ask_chat", return_value="押GEN") as ask, \
                    patch.object(bridge, "maybe_auto_compact"), patch.object(bridge, "save_state"), \
                    patch.object(bridge, "send_wechat"):
                bridge.process_group_task(FakeDB(), {"ai_sender_id": 1}, state, task)
            self.assertIs(ask.call_args.kwargs["task"], expected)
            self.assertIs(ask.call_args.kwargs["ambient"], ambient)


if __name__ == "__main__":
    unittest.main()
