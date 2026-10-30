import unittest
from datetime import datetime
from unittest.mock import patch

from outputs.WeChatAI import bridge

NAMES = ["大肥鱼", "DSH20260918"]


def quote(title, chatusr="wxid_ai", displayname="大肥鱼", quoted="下路这是把兵线当自助餐了", kind=1):
    return (f"wxid_a:\n<msg><appmsg><title>{title}</title><type>57</type><refermsg>"
            f"<type>{kind}</type><fromusr>room@chatroom</fromusr><chatusr>{chatusr}</chatusr>"
            f"<displayname>{displayname}</displayname><content>{quoted}</content>"
            "</refermsg></appmsg></msg>")


class ReplyToBotTests(unittest.TestCase):
    def test_quote_of_bot_message_is_a_request_without_at(self):
        self.assertEqual(bridge.extract_group_reply_to_bot(quote("哈哈哈 真的"), "wxid_ai", NAMES),
                         ("哈哈哈 真的", False))

    def test_matches_by_display_name_when_wxid_is_missing(self):
        self.assertEqual(bridge.extract_group_reply_to_bot(quote("你说啥", chatusr=""), "wxid_ai", NAMES)[0],
                         "你说啥")

    def test_quote_of_someone_else_is_not_a_request(self):
        content = quote("哈哈", chatusr="wxid_b", displayname="阿花")
        self.assertEqual(bridge.extract_group_reply_to_bot(content, "wxid_ai", NAMES), (None, False))

    def test_plain_text_and_leading_at_are_handled(self):
        self.assertEqual(bridge.extract_group_reply_to_bot("wxid_a:\n哈哈", "wxid_ai", NAMES), (None, False))
        self.assertEqual(bridge.extract_group_reply_to_bot(quote("@大肥鱼 再说一遍"), "wxid_ai", NAMES)[0],
                         "再说一遍")

    def test_quoted_bot_image_is_flagged(self):
        self.assertTrue(bridge.extract_group_reply_to_bot(quote("这是你？", kind=3), "wxid_ai", NAMES)[1])

    def run_dispatch(self, content):
        peer = "room@chatroom"
        now = datetime(2026, 10, 30, 12, 0, tzinfo=bridge.TOKYO_TIMEZONE)
        msg = {"sort_seq": int(now.timestamp() * 1000), "sender_id": 2, "type": "文本",
               "sender_username": "wxid_a", "content": content}

        class FakeDB:
            def get_sessions(self, limit):
                return [{"username": peer}]

            def get_new_messages(self, requested_peer, since_seq, limit):
                return [msg] if since_seq < msg["sort_seq"] else []

        class Dispatcher:
            def __init__(self):
                self.tasks = []

            def enqueue(self, peer, task):
                self.tasks.append(task)

        state = {"own_user": "wxid_ai", "activated_at": 0,
                 "chats": {peer: {"last_seq": 0, "model": "deepseek", "thinking": "off"}}}
        config = {"send_mode": "hook", "ai_sender_id": 1, "initial_model": "deepseek",
                  "group_mention_names": NAMES}
        dispatcher = Dispatcher()
        with patch.object(bridge, "save_state"), patch.object(bridge, "is_tokyo_quiet_hours", return_value=False):
            bridge.process_new_messages(FakeDB(), config, state, group_dispatcher=dispatcher)
        return dispatcher.tasks

    def test_dispatch_answers_a_quote_of_the_bot_and_carries_the_quote(self):
        tasks = self.run_dispatch(quote("哈哈哈 真的"))
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["request"], "哈哈哈 真的")
        self.assertEqual(tasks[0]["reply_to_bot_quote"], "下路这是把兵线当自助餐了")

    def test_dispatch_still_ignores_quotes_of_other_members(self):
        self.assertEqual(self.run_dispatch(quote("哈哈", chatusr="wxid_b", displayname="阿花")), [])

    def test_prompt_tells_the_model_what_was_quoted(self):
        peer = "room@chatroom"

        class FakeDB:
            def get_group_members(self, group):
                return [{"username": "wxid_a", "nick_name": "小明"}]

            def get_messages(self, group, limit):
                return []

        task = {"peer": peer, "request": "哈哈哈 真的", "reply_to_bot_quote": "下路这是把兵线当自助餐了",
                "msg": {"sort_seq": 4000, "sender_username": "wxid_a", "sender_id": 2,
                        "content": quote("哈哈哈 真的")}}
        state = {"chats": {peer: {"model": "deepseek", "thinking": "off"}}}
        with patch.object(bridge, "ask_openclaw", return_value="对啊") as ask, \
                patch.object(bridge, "maybe_auto_compact"), patch.object(bridge, "save_state"), \
                patch.object(bridge, "send_wechat"):
            bridge.process_group_task(FakeDB(), {"ai_sender_id": 1}, state, task)
        self.assertIn("小明引用了你刚才说的「下路这是把兵线当自助餐了」回你", ask.call_args.args[4])


if __name__ == "__main__":
    unittest.main()
