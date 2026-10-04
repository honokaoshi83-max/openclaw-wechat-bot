import json
import os
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

from outputs.WeChatAI import bridge
from outputs.WeChatAI.bridge import (classify_incoming_message, classify_message, extract_group_mention,
                                     extract_reply, is_direct_peer, is_group_peer,
                                     is_group_summary_request,
                                     load_state, plan_offline_batch, resolve_db_dir,
                                     resolve_search_name, save_state, select_ai_window,
                                     session_key, was_sent)
from outputs.WeChatAI.vision_client import build_vision_payload
from outputs.WeChatAI.vision_media import (ImageUnavailable, cleanup_image,
                                           extract_image, extract_visual,
                                           extract_emoji_from_content,
                                           is_emoji_message, is_image_message,
                                           is_visual_message,
                                           prepare_vision_image)


class BridgeTests(unittest.TestCase):
    def test_qwen_compacts_before_output_budget_is_starved(self):
        from unittest.mock import patch
        with patch.object(bridge, "get_openclaw_session_usage", return_value={
                "totalTokens": 22156, "contextTokens": 32768}), \
                patch.object(bridge, "compact_openclaw_session", return_value={}) as compact:
            bridge.maybe_auto_compact_before_turn({"auto_compact_ratio": .7},
                                                  "wxid_one", {"model": "qwen"})
        compact.assert_called_once()

    def test_search_retains_conversation_peer_and_epoch_for_followups(self):
        from unittest.mock import patch, MagicMock
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps({
            "results": [{"title": "赛事", "content": "两小分由甲队拿到"}]
        }).encode()
        with patch.object(bridge.urllib.request, "urlopen", return_value=response), \
                patch.object(bridge, "ask_openclaw", return_value="甲队") as ask:
            bridge.summarize_search({"initial_model": "qwen", "session_key_prefix": "test"}, "wxid_one",
                                    {"model": "qwen", "session_epoch": 7}, "赛事成绩")
        self.assertEqual(ask.call_args.args[1], "wxid_one")
        self.assertEqual(ask.call_args.kwargs["epoch"], 7)
        self.assertFalse(ask.call_args.kwargs["auto_network"])

    def test_current_fact_followups_do_not_require_explicit_date(self):
        for prompt in ("那推测一下BLG第一轮都会遇见哪些对手", "BLG首轮对手是谁",
                       "iPhone价格是多少", "千问发布了什么新版本", "东京天气怎么样"):
            self.assertTrue(bridge.should_auto_search(prompt), prompt)
        for prompt in ("第一轮面试该怎么准备", "为什么天气会变冷", "解释版本控制的原理"):
            self.assertFalse(bridge.should_auto_search(prompt), prompt)

    def test_group_natural_search_footer_keeps_sender_mention_and_single_reply(self):
        from unittest.mock import patch
        peer = "room@chatroom"
        state = {"chats": {peer: {"model": "qwen", "thinking": "off"}}}
        task = {"peer": peer, "request": "2026英雄联盟S赛什么时候开始？",
                "msg": {"sender_username": "wxid_user", "content": "wxid_user:\n@大肥鱼 问题"}}
        with patch.object(bridge, "ask_openclaw", return_value=bridge.ModelReply("日期已核实。", True)), \
                patch.object(bridge, "maybe_auto_compact"), \
                patch.object(bridge, "resolve_group_sender_name", return_value="小明"), \
                patch.object(bridge, "send_wechat") as send:
            bridge.process_group_task(object(), {"ai_sender_id": 1}, state, task)
        send.assert_called_once()
        self.assertIn("已联网", send.call_args.args[2])
        self.assertEqual(send.call_args.kwargs["at_name"], "小明")

    def test_automatic_search_routes_current_facts_not_creative_requests(self):
        for prompt in ("2026英雄联盟S赛什么时候开始？", "SearXNG目前的官方安装说明在哪里？",
                       "现在iPhone价格是多少", "LPL昨天谁赢了？"):
            self.assertTrue(bridge.should_auto_search(prompt), prompt)
        for prompt in ("你好", "帮我翻译：2026英雄联盟S赛什么时候开始？",
                       "写一篇关于最新新闻的小说", "我今天有点难过", "解释一下光合作用"):
            self.assertFalse(bridge.should_auto_search(prompt), prompt)

    def test_qwen_automatic_search_runs_retrieval_before_model(self):
        from unittest.mock import patch
        with patch.object(bridge, "summarize_search", return_value="答案") as search:
            reply = bridge.ask_openclaw({}, "wxid_user", "qwen", "off",
                                       bridge.prepare_model_prompt("2026英雄联盟S赛什么时候开始？"))
        self.assertEqual(reply, "答案")
        self.assertEqual(search.call_args.args[3], "2026英雄联盟S赛什么时候开始？")

    def test_public_webpage_reader_rejects_local_addresses(self):
        for url in ("file:///etc/passwd", "http://127.0.0.1:8080/", "http://localhost/"):
            with self.assertRaises(ValueError):
                bridge.fetch_public_page(url)

    def test_network_footer_requires_successful_web_tool(self):
        data = {"status": "ok", "result": {"payloads": [{"text": "事实已核实。"}],
                "meta": {"agentMeta": {"terminalReceipt": {
                    "successfulToolNames": ["web_fetch"]}}}}}
        reply = bridge.sanitize_reply(bridge.extract_reply(data))
        parts = bridge.prepare_reply_parts(reply, 3.2, "qwen", "off", True)
        self.assertIn("已联网", parts[-1])
        ordinary = bridge.extract_reply({"status": "ok", "result": {
            "payloads": [{"text": "我已经联网搜索。"}], "meta": {
                "agentMeta": {"terminalReceipt": {"successfulToolNames": []}},
                "toolSummary": {"tools": ["web_search"], "failures": 1}}}})
        self.assertNotIn("已联网", bridge.format_timed_reply(ordinary, 1, "deepseek", "off"))

    def test_network_metadata_survives_search_and_visual_cleanup(self):
        reply = bridge.ModelReply("**联网结果**", networked=True)
        for clean in (bridge.polish_search_reply(reply), bridge.compact_visual_reply(reply)):
            self.assertIn("已联网", bridge.format_timed_reply(clean, 2, "deepseek", "off"))
        self.assertNotIn("已联网", bridge.format_timed_reply("普通回复", 1, "qwen", "off"))

    def test_natural_network_policy_has_date_tool_rules_and_link_preference(self):
        prompt = bridge.prepare_model_prompt("2026英雄联盟S赛什么时候开始？")
        self.assertIn("web_search", prompt)
        self.assertIn("web_fetch", prompt)
        self.assertIn("当前日期", prompt)
        self.assertIn("闲聊", prompt)
        self.assertIn("2", prompt)
        self.assertIn("链接", prompt)

    def test_image_edit_outbox_prunes_only_expired_bridge_pngs(self):
        with tempfile.TemporaryDirectory() as temp:
            outbox = Path(temp)
            old = outbox / ("a" * 32 + ".png")
            recent = outbox / ("b" * 32 + ".png")
            unrelated = outbox / "keep-this.png"
            for path in (old, recent, unrelated):
                path.write_bytes(b"image")
            old_time = time.time() - 120
            os.utime(old, (old_time, old_time))
            os.utime(unrelated, (old_time, old_time))

            removed = bridge.prune_image_edit_outbox(outbox, max_age_seconds=60)

            self.assertEqual(removed, 1)
            self.assertFalse(old.exists())
            self.assertTrue(recent.exists())
            self.assertTrue(unrelated.exists())

    def test_group_dispatcher_preserves_order_within_one_group(self):
        seen = []
        done = threading.Event()

        def handle(task):
            seen.append(task)
            if len(seen) == 2:
                done.set()

        dispatcher = bridge.GroupTaskDispatcher(handle)
        try:
            dispatcher.enqueue("room-a", "first")
            dispatcher.enqueue("room-a", "second")
            self.assertTrue(done.wait(1.0))
            self.assertEqual(seen, ["first", "second"])
        finally:
            dispatcher.shutdown()

    def test_group_dispatcher_allows_different_groups_to_run_concurrently(self):
        both_started = threading.Event()
        release = threading.Event()
        started = set()
        lock = threading.Lock()

        def handle(task):
            with lock:
                started.add(task)
                if len(started) == 2:
                    both_started.set()
            release.wait(1.0)

        dispatcher = bridge.GroupTaskDispatcher(handle)
        try:
            dispatcher.enqueue("room-a", "a")
            dispatcher.enqueue("room-b", "b")
            self.assertTrue(both_started.wait(1.0))
        finally:
            release.set()
            dispatcher.shutdown()

    def test_group_mentions_are_required_and_extracted(self):
        self.assertEqual(extract_group_mention("@大肥鱼\u2005总结一下", ["大肥鱼"]), "总结一下")
        self.assertEqual(extract_group_mention("wxid_sender:\n@大肥鱼\u2005总结一下", ["大肥鱼"]), "总结一下")
        quoted = ('wxid_sender:\n<msg><appmsg><refermsg><content>'
                  '@大肥鱼 测试</content></refermsg></appmsg></msg>')
        self.assertIsNone(extract_group_mention(quoted, ["大肥鱼"]))
        self.assertIsNone(extract_group_mention("大家晚上好", ["大肥鱼"]))
        self.assertTrue(is_group_peer("123@chatroom", "wxid_ai"))
        self.assertFalse(is_group_peer("wxid_user", "wxid_ai"))
        self.assertTrue(is_group_summary_request("帮我总结一下对话"))
        self.assertTrue(is_group_summary_request("总结对话"))
        self.assertFalse(is_group_summary_request("总结一下群聊"))

    def test_group_quote_text_is_extracted_and_comment_intent_detected(self):
        content = ('wxid_sender:\n<msg><appmsg><title>@大肥鱼 评价一下</title>'
                   '<refermsg><content>小明：这条消息值得相信吗？</content><type>1</type>'
                   '</refermsg></appmsg></msg>')
        self.assertEqual(bridge.extract_group_quote_text(content), '小明：这条消息值得相信吗？')
        self.assertTrue(bridge.is_group_quote_comment_request('评价一下'))
        self.assertFalse(bridge.is_group_quote_search_request('评价一下'))

    def test_group_quote_search_intent_detected(self):
        self.assertTrue(bridge.is_group_quote_search_request('这是真的吗？'))
        self.assertTrue(bridge.is_group_quote_search_request('帮我核实一下'))
        self.assertFalse(bridge.is_group_quote_search_request('评论一下'))
    def test_image_messages_are_detected_without_reading_content(self):
        self.assertTrue(is_image_message({"type": "图片", "local_id": 7}))
        self.assertTrue(is_image_message({"local_type": 3, "local_id": 7}))
        self.assertFalse(is_image_message({"type": "文本", "content": "图片"}))

    def test_emoji_messages_are_detected_as_visual_media(self):
        self.assertTrue(is_emoji_message({"type": "动画表情", "local_type": 47}))
        self.assertTrue(is_emoji_message({"local_type": 11000}))
        self.assertTrue(is_emoji_message({"local_type": (17 << 32) | 11000}))
        self.assertTrue(is_visual_message({"type": "动画表情", "local_type": 47}))
        self.assertFalse(is_visual_message({"type": "文本", "content": "[动画表情]"}))

    def test_incoming_emoji_is_routed_to_the_visual_pipeline(self):
        self.assertEqual(
            classify_incoming_message(
                {"type": "动画表情", "local_type": 47, "sender_id": 4}, 1),
            ("image", "emoji"),
        )
        self.assertIsNone(classify_incoming_message(
            {"type": "动画表情", "local_type": 47, "sender_id": 1}, 1))

    def test_sticker_followup_requires_same_sender_and_three_second_window(self):
        text = {"sort_seq": 10000, "sender_id": 4, "sender_username": "wxid_a",
                "type": "文本", "content": "台风不断"}
        sticker = {"sort_seq": 12999, "sender_id": 4, "sender_username": "wxid_a",
                   "type": "动画表情", "local_type": 47}
        self.assertTrue(bridge.is_sticker_followup(text, sticker))
        self.assertFalse(bridge.is_sticker_followup(
            text, {**sticker, "sender_id": 5, "sender_username": "wxid_b"}))
        self.assertFalse(bridge.is_sticker_followup(
            text, {**sticker, "sort_seq": 13001}))
        self.assertFalse(bridge.is_sticker_followup(
            text, {**sticker, "type": "图片", "local_type": 3}))

    def test_sticker_context_prompt_is_limited_to_stickers(self):
        prompt = bridge.sticker_context_prompt("台风不断", "悲伤的猫，文字写着悲伤")
        self.assertIn("台风不断", prompt)
        self.assertIn("情绪或语气补充", prompt)
        self.assertIn("悲伤的猫", prompt)
        image_prompt = bridge.visual_reply_prompt("图片", "台风不断", "一把伞")
        self.assertNotIn("用户先发送了文字", image_prompt)

    def test_private_text_and_following_sticker_produce_one_contextual_reply(self):
        from unittest.mock import patch

        peer = "wxid_sticker_user"
        base = int(datetime(2026, 9, 23, 10, 0,
                            tzinfo=bridge.TOKYO_TIMEZONE).timestamp() * 1000)
        text = {"sort_seq": base, "sender_id": 2, "sender_username": peer,
                "local_id": 10, "type": "文本", "content": "台风不断"}
        sticker = {"sort_seq": base + 1200, "sender_id": 2,
                   "sender_username": peer, "local_id": 11,
                   "type": "动画表情", "local_type": 47, "content": "[动画表情]"}

        class FakeDB:
            def get_sessions(self, limit):
                return [{"username": peer}]

            def get_new_messages(self, requested_peer, since_seq, limit):
                if requested_peer != peer:
                    return []
                return [item for item in (text, sticker)
                        if item["sort_seq"] > since_seq][:limit]

        prompts = []
        chat = {"last_seq": 0, "model": "qwen", "thinking": "off",
                "humanized": False, "daily_help_date": "2026-09-23"}
        state = {"own_user": "wxid_ai", "activated_at": 0, "chats": {peer: chat}}
        config = {"send_mode": "hook", "ai_sender_id": 1,
                  "initial_model": "qwen", "agent": "wechat-public",
                  "session_key_prefix": "wechat-test",
                  "models": {"qwen": "llama-cpp/example"},
                  "humanized_delay_seconds": 0}

        with patch.object(bridge, "save_state"), \
                patch.object(bridge, "extract_visual", return_value=Path("sticker.gif")), \
                patch.object(bridge, "prepare_vision_image", return_value=Path("sticker.png")), \
                patch.object(bridge, "describe_image", return_value="悲伤的猫"), \
                patch.object(bridge, "ask_openclaw",
                             side_effect=lambda *args, **kwargs: prompts.append(args[-1]) or "懂了。"), \
                patch.object(bridge, "send_wechat_sequence") as send, \
                patch.object(bridge, "cleanup_image"), \
                patch.object(bridge, "maybe_auto_compact_before_turn"), \
                patch.object(bridge, "maybe_auto_compact"):
            bridge.process_new_messages(FakeDB(), config, state)

        self.assertEqual(len(prompts), 1)
        self.assertIn("台风不断", prompts[0])
        self.assertIn("悲伤的猫", prompts[0])
        self.assertIn("情绪或语气补充", prompts[0])
        send.assert_called_once()
        self.assertEqual(chat["last_seq"], sticker["sort_seq"])

    def test_animated_gif_is_reduced_to_a_three_frame_contact_sheet(self):
        from PIL import Image

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            source = root / "emoji.gif"
            frames = [Image.new("RGB", (20, 10), color) for color in ("red", "green", "blue", "white")]
            frames[0].save(source, save_all=True, append_images=frames[1:], duration=20, loop=0)
            prepared = prepare_vision_image(source, root)
            self.assertNotEqual(prepared, source)
            with Image.open(prepared) as image:
                self.assertEqual(image.format, "PNG")
                self.assertEqual(image.size, (20, 10))

    def test_emoji_cache_is_extracted_without_using_the_wechat_ui(self):
        class EmojiDownloader:
            def __init__(self, cache_path):
                self.cache_path = str(cache_path)
                self.db = object()

            def _img_md5(self, row):
                return "a" * 32

            def _find_dat(self, peer, digest, create_time, thumbnail=False):
                return self.cache_path

            def decrypt_image(self, dat_path):
                return b"GIF89a-test"

        class EmojiDb:
            def get_message_row(self, peer, local_id, local_type=None):
                if local_type == 47:
                    return {"local_type": 47, "create_time": 1, "content": b"", "packed_info": b""}
                return None

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            cache = root / "emoji.dat"
            cache.write_bytes(b"encrypted")
            result = extract_visual(
                EmojiDb(), "wxid_peer", 9, {"local_type": 47}, root,
                downloader_factory=lambda _db, _save_dir: EmojiDownloader(cache),
            )
            self.assertEqual(result.suffix, ".gif")
            self.assertEqual(result.read_bytes(), b"GIF89a-test")

    def test_emoji_cdn_fallback_uses_the_decompressed_message_xml(self):
        xml = ('<msg><emoji md5="' + 'b' * 32 + '" '
               'cdnurl="https://emoji.qpic.cn/emoji?a=1&amp;b=2" /></msg>')
        seen = []
        with tempfile.TemporaryDirectory() as d:
            result = extract_emoji_from_content(
                xml, Path(d) / "emoji",
                fetch_bytes=lambda url: seen.append(url) or b"\x89PNG\r\n\x1a\nbody",
            )
            self.assertEqual(seen, ["https://emoji.qpic.cn/emoji?a=1&b=2"])
            self.assertEqual(result.suffix, ".png")

    def test_emoji_cdn_fallback_rejects_non_wechat_hosts(self):
        xml = '<msg><emoji cdnurl="http://127.0.0.1/private" /></msg>'
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(ImageUnavailable, "不受信任"):
                extract_emoji_from_content(xml, Path(d) / "emoji")

    def test_vision_payload_contains_local_multimodal_content(self):
        payload = build_vision_payload(b"abc", "描述图片", "qwen", "image/png")
        content = payload["messages"][0]["content"]
        self.assertEqual(content[0], {"type": "text", "text": "描述图片"})
        self.assertTrue(content[1]["image_url"]["url"].startswith("data:image/png;base64,"))

    def test_image_cleanup_cannot_escape_temp_directory(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            image = root / "image.jpg"
            image.write_bytes(b"x")
            cleanup_image(image, root)
            self.assertFalse(image.exists())
            with self.assertRaises(ValueError):
                cleanup_image(Path(__file__).resolve(), root)

    def test_image_decrypt_runtime_error_becomes_recoverable_unavailable(self):
        class BrokenDownloader:
            def download_image(self, _peer, _local_id):
                raise RuntimeError("无法获取图片 AES 密钥")

        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(ImageUnavailable, "AES 密钥"):
                extract_image(
                    object(), "wxid_peer", 7, Path(d),
                    downloader_factory=lambda _db, _save_dir: BrokenDownloader(),
                )

    def test_image_decrypt_runtime_error_falls_back_to_hook_decoder(self):
        class BrokenDownloader:
            def download_image(self, _peer, _local_id):
                raise RuntimeError("无法获取图片 AES 密钥")

        with tempfile.TemporaryDirectory() as d:
            output = Path(d) / "decoded.jpg"
            output.write_bytes(b"\xff\xd8\xfftest")
            calls = []

            def hook_decoder(downloader, peer, local_id, temp_dir, hook_url):
                calls.append((peer, local_id, temp_dir, hook_url))
                return output

            result = extract_image(
                object(), "wxid_peer", 7, Path(d),
                downloader_factory=lambda _db, _save_dir: BrokenDownloader(),
                hook_url="http://127.0.0.1:30001",
                hook_decoder=hook_decoder,
            )
            self.assertEqual(result, output.resolve())
            self.assertEqual(calls[0][0:2], ("wxid_peer", 7))

    def test_daily_help_notice_is_claimed_once_per_tokyo_day(self):
        claim = getattr(bridge, "claim_daily_help_notice", None)
        self.assertIsNotNone(claim)
        chat = {}
        morning = datetime(2026, 9, 19, 1, 0, tzinfo=timezone.utc)
        notice = claim(chat, morning)
        self.assertIn("时间：2026年9月19日", notice)
        self.assertIn("大肥鱼0.4版本", notice)
        self.assertIn("\u5207\u6362DeepSeek\u6a21\u578b\uff1a\u5207\u6362\u5927\u80a5\u9c7c", notice)
        self.assertIn("\u5207\u6362\u5343\u95ee\u6a21\u578b\uff1a\u5207\u6362\u5343\u95ee", notice)
        self.assertIn("\u641c\u7d22\u5173\u952e\u8bcd", notice)
        self.assertIn("本地模型进入思考模式：/think", notice)
        self.assertIn("本地模型进入快速模式：/fast", notice)
        self.assertIsNone(claim(chat, morning))

    def test_daily_help_notice_has_version_02_and_media_capabilities(self):
        notice = bridge.render_daily_help(datetime(2026, 9, 20, 1, 0, tzinfo=timezone.utc))
        self.assertIn("大肥鱼0.4版本", notice)
        self.assertIn("qwen已支持图像识别、表情包识别、GIF识别（此模式为低精确识图）。（0.2版本更新）", notice)
        self.assertIn("DeepSeek已支持识图（此模式下为高准确识图）。（0.4版本更新）", notice)
        self.assertIn("🐋🐋🐋🐋🐋🐋🐋🐋🐋🐋🐋", notice)
        self.assertIn("/compact", notice)
        self.assertIn("/reset", notice)

    def test_daily_help_notice_resets_at_tokyo_midnight(self):
        claim = getattr(bridge, "claim_daily_help_notice", None)
        self.assertIsNotNone(claim)
        chat = {}
        before_midnight = datetime(2026, 9, 19, 14, 59, tzinfo=timezone.utc)
        after_midnight = datetime(2026, 9, 19, 15, 0, tzinfo=timezone.utc)
        self.assertIn("2026年9月19日", claim(chat, before_midnight))
        self.assertIn("2026年9月20日", claim(chat, after_midnight))

    def test_group_visual_request_requires_image_followup_within_three_seconds(self):
        self.assertTrue(bridge.is_group_visual_request("识别图片"))
        self.assertTrue(bridge.is_group_visual_request("帮我看看这个GIF"))
        self.assertFalse(bridge.is_group_visual_request("早上好"))
        pending = bridge.make_group_visual_pending("wxid_user", 1000, "识别图片")
        image = {"sender_username": "wxid_user", "sort_seq": 3500, "type": "图片"}
        self.assertEqual(bridge.match_group_visual_pending(pending, image), "识别图片")
        late = {"sender_username": "wxid_user", "sort_seq": 4501, "type": "图片"}
        self.assertIsNone(bridge.match_group_visual_pending(pending, late))

    def test_group_visual_followup_does_not_match_other_sender(self):
        pending = bridge.make_group_visual_pending("wxid_user", 1000, "")
        other = {"sender_username": "wxid_other", "sort_seq": 1500, "type": "图片"}
        self.assertIsNone(bridge.match_group_visual_pending(pending, other))

    def test_group_sender_name_uses_group_member_display_name(self):
        class FakeDB:
            def get_group_members(self, group):
                return [{"username": "wxid_user", "remark": "小明", "nick_name": "明明"}]
            def get_nickname(self, user):
                return "用户"
        self.assertEqual(bridge.resolve_group_sender_name(FakeDB(), "g@chatroom", "wxid_user"), "小明")

    def test_search_command_is_explicit_and_query_is_preserved(self):
        self.assertEqual(bridge.classify_message({"sender_id": 2, "type": "文本", "content": "/search llama.cpp"}, 1),
                         ("search", "llama.cpp"))
        self.assertEqual(bridge.classify_message({"sender_id": 2, "type": "文本", "content": "/search"}, 1),
                         ("search", ""))
        self.assertFalse(bridge.search_requests_links("英雄联盟最新赛况"))
        self.assertTrue(bridge.search_requests_links("英雄联盟最新赛况，标明链接"))

    def test_natural_language_search_requests_are_explicit_and_preserve_query(self):
        base = {"sender_id": 2, "type": "\u6587\u672c"}
        self.assertEqual(bridge.classify_message({**base, "content": "\u641c\u7d22 2026 LPL \u9009\u62d4\u8d5b\u7ed3\u679c"}, 1),
                         ("search", "2026 LPL \u9009\u62d4\u8d5b\u7ed3\u679c"))
        self.assertEqual(bridge.classify_message({**base, "content": "\u5e2e\u6211\u6838\u5b9e\u4e00\u4e0b\uff1aLPL\u9009\u62d4\u8d5b\u7ed3\u675f\u4e86\u5417\uff1f"}, 1),
                         ("search", "LPL\u9009\u62d4\u8d5b\u7ed3\u675f\u4e86\u5417\uff1f"))
        self.assertEqual(bridge.classify_message({**base, "content": "\u6211\u521a\u624d\u641c\u7d22\u4e86\u4e00\u4e0b\u8fd9\u4e2a\u95ee\u9898"}, 1),
                         ("prompt", "\u6211\u521a\u624d\u641c\u7d22\u4e86\u4e00\u4e0b\u8fd9\u4e2a\u95ee\u9898"))

    def test_chinese_model_switch_supports_followup_question_and_legacy_aliases(self):
        base = {"sender_id": 2, "type": "\u6587\u672c"}
        self.assertEqual(bridge.classify_message({**base, "content": "\u5207\u6362\u5927\u80a5\u9c7c"}, 1),
                         ("model", "deepseek"))
        self.assertEqual(bridge.classify_message({**base, "content": "\u5207\u6362\u5343\u95ee"}, 1),
                         ("model", "qwen"))
        self.assertEqual(bridge.classify_message({**base, "content": "\u5207\u6362\u5927\u80a5\u9c7c\uff0c\u89e3\u91ca\u4e00\u4e0b\u8fd9\u4e2a\u9519\u8bef"}, 1),
                         ("switch_prompt", ("deepseek", "\u89e3\u91ca\u4e00\u4e0b\u8fd9\u4e2a\u9519\u8bef")))
        self.assertEqual(bridge.classify_message({**base, "content": "\u5207\u6362\u5343\u95ee \u5e2e\u6211\u603b\u7ed3\u8fd9\u6bb5\u6587\u5b57"}, 1),
                         ("switch_prompt", ("qwen", "\u5e2e\u6211\u603b\u7ed3\u8fd9\u6bb5\u6587\u5b57")))
        self.assertEqual(bridge.classify_message({**base, "content": "/deepseek"}, 1),
                         ("model", "deepseek"))
        self.assertEqual(bridge.classify_message({**base, "content": "/qwen"}, 1),
                         ("model", "qwen"))

    def test_help_uses_simplified_chinese_switch_and_search_instructions(self):
        help_text = bridge.render_daily_help(datetime(2026, 9, 20, tzinfo=timezone.utc))
        self.assertIn("\u5207\u6362\u5927\u80a5\u9c7c", help_text)
        self.assertIn("\u5207\u6362\u5343\u95ee", help_text)
        self.assertIn("\u641c\u7d22\u5173\u952e\u8bcd", help_text)


    def test_daily_help_notice_state_survives_round_trip(self):
        claim = getattr(bridge, "claim_daily_help_notice", None)
        self.assertIsNotNone(claim)
        now = datetime(2026, 9, 19, 1, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "state.json"
            chat = {}
            self.assertIsNotNone(claim(chat, now))
            save_state(path, {"chats": {"wxid_user": chat}})
            restored = load_state(path)["chats"]["wxid_user"]
            self.assertIsNone(claim(restored, now))

    def test_only_incoming_text_is_answered(self):
        base = {"username": "wxid_main", "sender_id": 4, "type": "文本", "content": "你好"}
        self.assertEqual(classify_message(base, 1), ("prompt", "你好"))
        self.assertEqual(classify_message({**base, "sender_id": 8}, 1), ("prompt", "你好"))
        self.assertIsNone(classify_message({**base, "sender_id": 1}, 1))
        self.assertIsNone(classify_message({**base, "type": "图片"}, 1))
        self.assertTrue(is_direct_peer("wxid_other", "wxid_ai"))
        self.assertFalse(is_direct_peer("room@chatroom", "wxid_ai"))
        self.assertFalse(is_direct_peer("filehelper", "wxid_ai"))

    def test_model_commands_are_local(self):
        base = {"username": "wxid_main", "sender_id": 4, "type": "文本"}
        self.assertEqual(classify_message({**base, "content": "/qwen"}, 1), ("model", "qwen"))
        self.assertEqual(classify_message({**base, "content": "/deepseek"}, 1), ("model", "deepseek"))

    def test_private_switch_with_followup_uses_new_model_for_answer(self):
        from unittest.mock import patch

        peer = "wxid_user"
        msg = {"username": peer, "sender_id": 2, "type": "文本",
               "content": "切换大肥鱼，解释一下这个错误", "sort_seq": 1}

        class FakeDB:
            def get_sessions(self, limit):
                return [{"username": peer}]

            def get_new_messages(self, requested_peer, since_seq, limit):
                return [msg] if since_seq < msg["sort_seq"] else []

        chat = {"last_seq": 0, "model": "qwen", "thinking": "off",
                "humanized": False, "daily_help_date": "2026-10-04"}
        state = {"own_user": "wxid_ai", "activated_at": 0, "chats": {peer: chat}}
        config = {"ai_sender_id": 1, "initial_model": "qwen", "send_mode": "hook",
                  "humanized_delay_seconds": 0, "models": {"qwen": "qwen", "deepseek": "deepseek"}}

        with patch.object(bridge, "save_state"), \
                patch.object(bridge, "ask_openclaw", return_value="这是解释。") as ask, \
                patch.object(bridge, "send_wechat_sequence"):
            count = bridge.process_new_messages(FakeDB(), config, state)

        self.assertEqual(count, 1)
        self.assertEqual(chat["model"], "deepseek")
        self.assertEqual(ask.call_args.args[2], "deepseek")
        self.assertIn("解释一下这个错误", ask.call_args.args[4])

    def test_group_switch_with_followup_uses_new_model_for_answer(self):
        from unittest.mock import patch

        peer = "room@chatroom"
        chat = {"model": "qwen", "thinking": "off", "session_epoch": 0}
        state = {"chats": {peer: chat}}
        task = {"peer": peer,
                "msg": {"sender_username": "wxid_user", "sender_id": 2,
                        "content": "wxid_user:\n@大肥鱼 切换大肥鱼，解释一下这个错误"},
                "request": "切换大肥鱼，解释一下这个错误"}

        class FakeDB:
            def get_group_members(self, group):
                return [{"username": "wxid_user", "nick_name": "小明"}]

        with patch.object(bridge, "save_state"), \
                patch.object(bridge, "ask_openclaw", return_value="这是解释。") as ask, \
                patch.object(bridge, "send_wechat"):
            bridge.process_group_task(FakeDB(), {"ai_sender_id": 1}, state, task)

        self.assertEqual(chat["model"], "deepseek")
        self.assertEqual(ask.call_args.args[2], "deepseek")
        self.assertIn("解释一下这个错误", ask.call_args.args[4])

    def test_qwen_thinking_commands_are_local(self):
        base = {"username": "wxid_main", "sender_id": 4, "type": "文本"}
        self.assertEqual(classify_message({**base, "content": "/fast"}, 1), ("thinking", "off"))
        self.assertEqual(classify_message({**base, "content": "/think"}, 1), ("thinking", "high"))
        self.assertEqual(classify_message({**base, "content": "/help"}, 1), ("help", ""))

    def test_thinking_mode_only_changes_qwen_chat(self):
        apply_mode = getattr(bridge, "apply_thinking_mode", lambda chat, level: "missing")
        qwen_chat = {"model": "qwen", "thinking": "off"}
        self.assertEqual(apply_mode(qwen_chat, "high"), "已切换到思考模式。")
        self.assertEqual(qwen_chat["thinking"], "high")

        deepseek_chat = {"model": "deepseek", "thinking": "off"}
        self.assertEqual(apply_mode(deepseek_chat, "high"), "请先使用 /qwen 切换到 Qwen。")
        self.assertEqual(deepseek_chat["thinking"], "off")

    def test_openclaw_command_passes_per_chat_thinking_mode(self):
        build_command = getattr(bridge, "build_openclaw_command", lambda *args: [])
        cfg = {
            "agent": "wechat-public",
            "session_key_prefix": "wechat-ai-public",
            "models": {"qwen": "llama-cpp/example"},
        }
        command = build_command(cfg, "wxid_main", "qwen", "high", "你好")
        thinking_index = command.index("--thinking") if "--thinking" in command else -1
        self.assertGreaterEqual(thinking_index, 0)
        self.assertEqual(command[thinking_index + 1], "high")

    def test_session_keys_are_unique_per_peer(self):
        cfg = {"session_key_prefix": "wechat-ai-public"}
        self.assertNotEqual(session_key(cfg, "wxid_1"), session_key(cfg, "wxid_2"))
        self.assertEqual(session_key(cfg, "wxid_1"), session_key(cfg, "wxid_1"))

    def test_fast_lookup_only_for_unique_display_name(self):
        class FakeDB:
            def get_nickname(self, peer):
                return {"wxid_1": "小明", "wxid_2": "同名", "wxid_3": "wxid_3"}[peer]

            def search_contact(self, name):
                return {"小明": [{"username": "wxid_1", "remark": "小明", "nick_name": "甲"}],
                        "同名": [{"username": "wxid_2", "remark": "同名", "nick_name": "乙"},
                                 {"username": "wxid_4", "remark": "同名", "nick_name": "丙"}]}.get(name, [])

        db = FakeDB()
        self.assertEqual(resolve_search_name(db, "wxid_1"), "小明")
        self.assertEqual(resolve_search_name(db, "wxid_2"), "wxid_2")
        self.assertEqual(resolve_search_name(db, "wxid_3"), "wxid_3")

    def test_multi_window_selection_checks_process_account(self):
        windows = [{"hwnd": 11, "pid": 101}, {"hwnd": 22, "pid": 202}]
        accounts = {101: "wxid_main", 202: "wxid_ai"}
        self.assertEqual(select_ai_window(windows, "wxid_ai", lambda pid: accounts.get(pid)), 22)
        with self.assertRaises(RuntimeError):
            select_ai_window(windows, "wxid_missing", lambda pid: accounts.get(pid))
        with self.assertRaises(RuntimeError):
            select_ai_window([{"hwnd": 11, "pid": 101}, {"hwnd": 33, "pid": 101}],
                             "wxid_main", lambda pid: accounts.get(pid))

    def test_single_window_selection_falls_back_when_process_identity_is_unavailable(self):
        windows = [{"hwnd": 44, "pid": 404}]
        self.assertEqual(select_ai_window(windows, "wxid_ai", lambda _pid: None), 44)

    def test_db_dir_auto_mode_uses_current_user_detection(self):
        self.assertEqual(
            resolve_db_dir({"db_dir": "auto"}, detect_db_dir=lambda: r"C:\Users\WechatAI\xwechat_files"),
            r"C:\Users\WechatAI\xwechat_files",
        )

    def test_db_dir_explicit_mode_is_preserved(self):
        self.assertEqual(
            resolve_db_dir({"db_dir": r"D:\jilu\xwechat_files"}, detect_db_dir=lambda: None),
            r"D:\jilu\xwechat_files",
        )

    def test_offline_batch_ignores_content_and_coalesces_incoming_messages(self):
        class NoContentAccess(dict):
            def get(self, key, default=None):
                if key in ("content", "type"):
                    raise AssertionError("offline message content must not be read")
                return super().get(key, default)

        messages = [
            NoContentAccess(sort_seq=101, sender_id=4, content="第一条"),
            NoContentAccess(sort_seq=102, sender_id=4, content="/deepseek"),
            NoContentAccess(sort_seq=103, sender_id=1, content="本账号消息"),
        ]
        self.assertEqual(plan_offline_batch(messages, 100, 1), (103, True))
        self.assertEqual(plan_offline_batch([NoContentAccess(sort_seq=104, sender_id=1)], 103, 1),
                         (104, False))

    def test_tokyo_quiet_hours_cover_midnight_to_eight(self):
        self.assertTrue(bridge.is_tokyo_quiet_hours(datetime(2026, 9, 22, 0, 0,
                                                             tzinfo=bridge.TOKYO_TIMEZONE)))
        self.assertTrue(bridge.is_tokyo_quiet_hours(datetime(2026, 9, 22, 7, 59,
                                                             tzinfo=bridge.TOKYO_TIMEZONE)))
        self.assertFalse(bridge.is_tokyo_quiet_hours(datetime(2026, 9, 22, 8, 0,
                                                              tzinfo=bridge.TOKYO_TIMEZONE)))

    def test_own_send_confirmation_uses_actual_sender_id(self):
        records = [{"sort_seq": 101, "sender_id": 1, "content": "reply"}]
        self.assertTrue(was_sent(records, 100, 1, "reply"))
        self.assertFalse(was_sent(records, 100, 2, "reply"))
        self.assertFalse(was_sent(records, 101, 1, "reply"))

    def test_response_payload(self):
        self.assertEqual(extract_reply({"status": "ok", "result": {"payloads": [{"text": "答复"}]}}), "答复")
        with self.assertRaises(ValueError):
            extract_reply({"status": "error", "result": {"payloads": []}})

    def test_generated_reply_includes_mode_output_character_count_and_elapsed_time(self):
        formatter = getattr(bridge, "format_timed_reply", lambda *args: args[0])
        self.assertEqual(
            formatter("答复", 18.64, "qwen", "high"),
            "答复\n\n（思考模式｜输出文字：2 字｜耗时 18.6 秒）",
        )

    def test_generated_reply_handles_fast_and_deepseek_modes(self):
        formatter = getattr(bridge, "format_timed_reply", lambda *args: args[0])
        self.assertEqual(
            formatter("答复\n第二行", 7.26, "qwen", "off"),
            "答复\n第二行\n\n（快速模式｜输出文字：5 字｜耗时 7.3 秒）",
        )
        self.assertEqual(
            formatter("答复", 3.04, "deepseek", "off"),
            "答复\n\n（DeepSeek｜输出文字：2 字｜耗时 3.0 秒）",
        )

    def test_short_casual_prompts_are_compact_but_tasks_are_not(self):
        is_compact = getattr(bridge, "is_short_casual_prompt", lambda prompt: False)
        self.assertTrue(is_compact("你好，大肥鱼"))
        self.assertTrue(is_compact("在吗"))
        self.assertTrue(is_compact("测试"))
        self.assertFalse(is_compact("为什么 OpenClaw 又变慢了？"))
        self.assertFalse(is_compact("帮我检查一下模型配置"))
        self.assertFalse(is_compact("请分析这段代码"))

    def test_short_casual_prompt_gets_one_or_two_sentence_instruction(self):
        prepare_prompt = getattr(bridge, "prepare_model_prompt", lambda prompt: prompt)
        compact = prepare_prompt("你好，大肥鱼")
        self.assertIn("1～2 句", compact)
        self.assertIn("不要主动展开话题", compact)
        task_prompt = prepare_prompt("为什么 OpenClaw 又变慢了？")
        self.assertIn("先回应用户实际说出的事实或情绪", task_prompt)
        self.assertIn("不要模拟双方对话", task_prompt)

    def test_compact_reply_is_forced_into_one_message(self):
        prepare = getattr(bridge, "prepare_reply_parts", lambda *args, **kwargs: [args[0]])
        parts = prepare(
            "第一句。第二句！第三句？第四句。", 2.0, "qwen", "off", True,
            compact=True,
        )
        self.assertEqual(parts, [
            "第一句。第二句！\n\n（快速模式｜输出文字：8 字｜耗时 2.0 秒）",
        ])

    def test_compact_reply_has_a_hard_character_limit(self):
        prepare = getattr(bridge, "prepare_reply_parts", lambda *args, **kwargs: [args[0]])
        long_reply = "这是一段没有句号而且会一直继续扩写的模型回复" * 4
        parts = prepare(long_reply, 2.0, "qwen", "off", True, compact=True)
        body = parts[0].split("\n\n（", 1)[0]
        self.assertLessEqual(len(body), 60)
        self.assertTrue(body.endswith("…"))

    def test_model_prompt_adds_intent_and_natural_dialogue_guidance(self):
        prepare = getattr(bridge, "prepare_model_prompt")
        prompt = prepare("最近老师建议我带一下大一新生，我有点犹豫")
        self.assertIn("判断用户是在分享、提问、求建议还是闲聊", prompt)
        self.assertIn("不要模拟双方对话", prompt)
        self.assertIn("最多追问一个相关问题", prompt)
        self.assertIn("个人会话记忆隔离", prompt)
        self.assertIn("不得引用、猜测或泄露其他微信用户", prompt)

    def test_short_prompt_keeps_general_and_compact_limits(self):
        prepare = getattr(bridge, "prepare_model_prompt")
        prompt = prepare("测试")
        self.assertIn("不要把每句话都写成引号台词", prompt)
        self.assertIn("只用 1～2 句自然回应", prompt)

    def test_sanitize_reply_removes_internal_workspace_context(self):
        sanitize = getattr(bridge, "sanitize_reply", lambda text: text)
        leaked = (
            "刚才聊得挺开心。\n\n---\n\n"
            "<!-- project: path:/home/openclaw/.openclaw/workspace-wechat-public -->\n"
            "<!-- observed: 2026-09-19 | status: active -->\n"
            "- User is considering mentoring."
        )
        self.assertEqual(sanitize(leaked), "刚才聊得挺开心。")

    def test_sanitize_reply_returns_safe_fallback_when_only_context_leaked(self):
        sanitize = getattr(bridge, "sanitize_reply", lambda text: text)
        leaked = "<!-- project: path:/home/openclaw/.openclaw/workspace-wechat-public -->"
        self.assertEqual(sanitize(leaked), "刚才回复格式出了点问题，你再说一次。")

    def test_state_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "state.json"
            state = {"last_seq": 123, "model": "qwen"}
            save_state(p, state)
            self.assertEqual(load_state(p), state)
            self.assertEqual(json.loads(p.read_text(encoding="utf-8")), state)

    def test_hook_transport_builds_local_send_request(self):
        build_request = getattr(bridge, "build_hook_request", None)
        self.assertIsNotNone(build_request)
        endpoint, payload = build_request(
            {"send_mode": "hook", "hook_url": "http://127.0.0.1:30001"},
            "wxid_main",
            "你好",
        )
        self.assertEqual(endpoint, "http://127.0.0.1:30001/SendTextMsg")
        self.assertEqual(payload, {"wxidorgid": "wxid_main", "msg": "你好"})

    def test_hook_transport_builds_local_image_send_request(self):
        build_request = getattr(bridge, "build_hook_image_request", None)
        self.assertIsNotNone(build_request)
        endpoint, payload = build_request(
            {"hook_url": "http://127.0.0.1:30001"}, "room@chatroom", r"D:\outbox\edited.png"
        )
        self.assertEqual(endpoint, "http://127.0.0.1:30001/SendImgMsg")
        self.assertEqual(payload, {"wxidorgid": "room@chatroom", "path": r"D:\outbox\edited.png"})

    def test_group_image_edit_request_collects_requirement_and_unmentioned_image(self):
        from datetime import datetime
        from unittest.mock import patch

        peer = "room@chatroom"
        base = int(datetime(2026, 9, 23, 10, 0, tzinfo=bridge.TOKYO_TIMEZONE).timestamp() * 1000)
        messages = [
            {"sort_seq": base, "sender_id": 2, "sender_username": "wxid_user",
             "type": "文本", "content": "wxid_user:\n@大肥鱼\u2005帮我修图"},
            {"sort_seq": base + 1000, "sender_id": 2, "sender_username": "wxid_user",
             "type": "文本", "content": "wxid_user:\n把背景改成海边"},
            {"sort_seq": base + 2000, "sender_id": 2, "sender_username": "wxid_user",
             "type": "图片", "local_type": 3, "local_id": 99, "content": "wxid_user:"},
        ]

        class FakeDB:
            def get_sessions(self, limit):
                return [{"username": peer}]

            def get_new_messages(self, requested_peer, since_seq, limit):
                return [item for item in messages if item["sort_seq"] > since_seq]

            def get_group_members(self, group):
                return [{"username": "wxid_user", "nick_name": "小明"}]

        class FakeDispatcher:
            def __init__(self):
                self.tasks = []

            def enqueue(self, group, task):
                self.tasks.append((group, task))

        chat = {"last_seq": 0, "model": "qwen", "thinking": "off",
                "humanized": False, "daily_help_date": "2026-09-23"}
        state = {"own_user": "wxid_ai", "activated_at": 0, "chats": {peer: chat}}
        config = {"image_edit_enabled": True, "image_edit_request_timeout_seconds": 300,
                  "group_mention_names": ["大肥鱼"], "ai_sender_id": 1,
                  "initial_model": "qwen", "send_mode": "hook"}
        dispatcher = FakeDispatcher()
        self.assertEqual(bridge.extract_group_mention(messages[0]["content"], ["大肥鱼"]), "帮我修图")

        with patch.object(bridge, "save_state"), \
                patch.object(bridge, "send_wechat") as send:
            count = bridge.process_new_messages(FakeDB(), config, state,
                                                group_dispatcher=dispatcher)

        self.assertEqual(count, 1, repr(state))
        self.assertEqual(len(dispatcher.tasks), 1)
        task = dispatcher.tasks[0][1]
        self.assertEqual(task["image_edit_task"]["instruction"], "把背景改成海边")
        self.assertEqual(task["image_edit_task"]["image_message"]["local_id"], 99)
        self.assertEqual(dispatcher.tasks[0][0], peer)
        self.assertEqual(send.call_count, 3)

    def test_group_image_edit_is_not_activated_when_disabled(self):
        from datetime import datetime
        from unittest.mock import patch

        peer = "room@chatroom"
        base = int(datetime(2026, 9, 23, 10, 0, tzinfo=bridge.TOKYO_TIMEZONE).timestamp() * 1000)
        messages = [{"sort_seq": base, "sender_id": 2, "sender_username": "wxid_user",
                     "type": "\u6587\u672c", "content": "wxid_user:\n@\u5927\u80a5\u9c7c \u5e2e\u6211\u4fee\u56fe"}]

        class FakeDB:
            def get_sessions(self, limit):
                return [{"username": peer}]

            def get_new_messages(self, requested_peer, since_seq, limit):
                return [item for item in messages if item["sort_seq"] > since_seq]

            def get_group_members(self, group):
                return [{"username": "wxid_user", "nick_name": "\u5c0f\u660e"}]

        class FakeDispatcher:
            def __init__(self):
                self.tasks = []

            def enqueue(self, group, task):
                self.tasks.append((group, task))

        chat = {"last_seq": 0, "model": "qwen", "thinking": "off", "humanized": False,
                "daily_help_date": "2026-09-23"}
        state = {"own_user": "wxid_ai", "activated_at": 0, "chats": {peer: chat}}
        config = {"image_edit_enabled": False, "group_mention_names": ["\u5927\u80a5\u9c7c"],
                  "ai_sender_id": 1, "initial_model": "qwen", "send_mode": "hook"}
        dispatcher = FakeDispatcher()

        with patch.object(bridge, "save_state"), \
                patch.object(bridge, "_send_group_edit_prompt") as edit_notice:
            count = bridge.process_new_messages(FakeDB(), config, state,
                                                group_dispatcher=dispatcher)

        self.assertEqual(count, 1)
        self.assertEqual(dispatcher.tasks, [])
        edit_notice.assert_called_once()
        self.assertEqual(edit_notice.call_args.args[4], "本机修图功能已停用。")

    def test_hook_transport_is_selected_without_a_window(self):
        use_hook = getattr(bridge, "use_hook_transport", None)
        self.assertIsNotNone(use_hook)
        self.assertTrue(use_hook({"send_mode": "hook"}))
        self.assertFalse(use_hook({"send_mode": "gui"}))

    def test_hook_send_rejects_not_logged_in_without_waiting_for_database_confirmation(self):
        from unittest.mock import patch

        with patch.object(bridge.urllib.request, "urlopen") as urlopen:
            urlopen.return_value.__enter__.return_value.read.return_value = (
                b'{"msg":"not logged in","ret":-2}'
            )
            with self.assertRaisesRegex(RuntimeError, "not logged in"):
                bridge.send_wechat_hook(
                    {"hook_url": "http://127.0.0.1:30001"}, "wxid_peer", "hello"
                )

    def test_slow_turn_is_delivered_before_automatic_compaction(self):
        from datetime import datetime
        from unittest.mock import patch

        events = []
        peer = "wxid_peer"
        now = datetime(2026, 9, 23, 10, 0, tzinfo=bridge.TOKYO_TIMEZONE)
        msg = {
            "sort_seq": int(now.timestamp() * 1000),
            "sender_id": 2,
            "type": "文本",
            "content": "早上好",
        }

        class FakeDB:
            def get_sessions(self, limit):
                return [{"username": peer}]

            def get_new_messages(self, requested_peer, since_seq, limit):
                return [msg] if requested_peer == peer else []

        chat = {
            "last_seq": 0,
            "model": "qwen",
            "thinking": "off",
            "humanized": False,
            "daily_help_date": "2026-09-23",
        }
        state = {"own_user": "wxid_ai", "activated_at": 0, "chats": {peer: chat}}
        config = {
            "send_mode": "hook",
            "ai_sender_id": 1,
            "initial_model": "qwen",
            "agent": "wechat-public",
            "session_key_prefix": "wechat-test",
            "models": {"qwen": "llama-cpp/example"},
            "humanized_delay_seconds": 0,
        }

        def send_reply(_db, _peer, _parts, _sender, _delay, config=None):
            events.append("send")

        def compact(_config, _peer, _chat, elapsed_seconds):
            events.append("compact")

        with patch.object(bridge, "save_state"), \
                patch.object(bridge, "find_sticker_followup", return_value=None), \
                patch.object(bridge, "maybe_auto_compact_before_turn"), \
                patch.object(bridge, "ask_openclaw", return_value="早上好。"), \
                patch.object(bridge, "send_wechat_sequence", side_effect=send_reply), \
                patch.object(bridge, "maybe_auto_compact", side_effect=compact), \
                patch.object(bridge.time, "monotonic",
                             side_effect=[0.0, 1.0, 101.0, 102.0, 103.0]):
            bridge.process_new_messages(FakeDB(), config, state)

        self.assertEqual(events, ["send", "compact"])


    def test_group_summary_uses_only_the_latest_seventy_messages(self):
        history = [{"sender_id": 2, "sender_username": f"u{i}", "content": f"m{i}"}
                   for i in range(100)]
        prompt = bridge.group_summary_prompt(history, "总结一下对话")
        self.assertIn("最多70条", prompt)
        self.assertIn("u99：m99", prompt)
        self.assertIn("u30：m30", prompt)
        self.assertNotIn("u29：m29", prompt)

    def test_group_summary_transcript_stays_within_the_command_line_budget(self):
        history = [{"sender_id": 2, "sender_username": "u", "content": "x" * 500}
                   for _ in range(70)]
        prompt = bridge.group_summary_prompt(history, "总结一下对话")
        self.assertLessEqual(len(prompt), bridge.GROUP_SUMMARY_MAX_CHARS + 400)
        self.assertIn(bridge.GROUP_SUMMARY_OMITTED_MARK, prompt)

    def test_group_summary_always_keeps_the_newest_message(self):
        history = [{"sender_id": 2, "sender_username": "u", "content": "y" * 20000}]
        prompt = bridge.group_summary_prompt(history, "总结一下对话")
        self.assertIn("y" * 100, prompt)

    def test_transcript_trimming_keeps_the_newest_lines(self):
        lines = ["a" * 100, "b" * 100, "c" * 100]
        text = bridge.trim_transcript_lines(lines, max_chars=150)
        self.assertTrue(text.startswith(bridge.GROUP_SUMMARY_OMITTED_MARK))
        self.assertIn("c" * 100, text)
        self.assertNotIn("a" * 100, text)

    def test_group_failure_notice_is_rate_limited_per_group(self):
        notify = bridge.should_notify_group_failure
        self.assertTrue(notify("room-cooldown-a", now=100.0))
        self.assertFalse(notify("room-cooldown-a", now=130.0))
        self.assertTrue(notify("room-cooldown-a", now=170.0))
        self.assertTrue(notify("room-cooldown-b", now=170.0))

    def test_crash_logging_keeps_native_faults_and_unhandled_errors(self):
        import faulthandler
        import sys as _sys
        import threading as _threading

        with tempfile.TemporaryDirectory() as d:
            original_runtime = bridge.RUNTIME_DIR
            original_hook = _sys.excepthook
            original_thread_hook = _threading.excepthook
            bridge.RUNTIME_DIR = Path(d)
            try:
                bridge.install_crash_logging()
                self.assertIsNot(_sys.excepthook, original_hook)
                self.assertIsNot(_threading.excepthook, original_thread_hook)
                self.assertTrue((Path(d) / "bridge.fault.log").exists())
            finally:
                bridge.RUNTIME_DIR = original_runtime
                _sys.excepthook = original_hook
                _threading.excepthook = original_thread_hook
                faulthandler.disable()
                if bridge.CRASH_LOG_STREAM is not None:
                    bridge.CRASH_LOG_STREAM.close()
                    bridge.CRASH_LOG_STREAM = None


    def test_openclaw_runs_without_a_console_window(self):
        options = bridge.openclaw_run_options()
        self.assertEqual(options.get("stdin"), __import__("subprocess").DEVNULL)
        if __import__("os").name == "nt":
            self.assertTrue(options["creationflags"] & 0x08000000,
                            "CREATE_NO_WINDOW must be set or wsl.exe flashes a console")

    def test_windowless_spawn_default_applies_to_every_child_process(self):
        import os as _os
        import subprocess as _subprocess

        if _os.name != "nt" or not hasattr(_subprocess, "CREATE_NO_WINDOW"):
            self.skipTest("Windows-only behavior")
        original_run = _subprocess.run
        original_popen = _subprocess.Popen
        bridge.apply_windowless_spawn()
        try:
            self.assertTrue(getattr(_subprocess, "_windowless_default", False))
            seen = {}

            class FakePopen:
                def __init__(self, *args, **kwargs):
                    seen.update(kwargs)
                    raise OSError("stubbed by test")

            _subprocess.Popen = FakePopen
            for flags in (0, 0x00000200):
                seen.clear()
                with self.assertRaises(OSError):
                    _subprocess.run(["wsl.exe", "--version"], creationflags=flags)
                merged = seen["creationflags"]
                self.assertTrue(merged & _subprocess.CREATE_NO_WINDOW)
                self.assertEqual(merged & flags, flags, "caller flags must be preserved")
                self.assertEqual(seen["stdin"], _subprocess.DEVNULL)

            # calling it twice must not stack hooks
            hooked_run = _subprocess.run
            bridge.apply_windowless_spawn()
            self.assertIs(_subprocess.run, hooked_run)
        finally:
            _subprocess.run = original_run
            _subprocess.Popen = original_popen
            _subprocess._windowless_default = False

    def test_console_window_is_hidden_unless_config_keeps_it(self):
        calls = []

        class FakeKernel32:
            def GetConsoleWindow(self):
                return 1234

        class FakeUser32:
            def ShowWindow(self, handle, command):
                calls.append((handle, command))

        class FakeWindll:
            kernel32 = FakeKernel32()
            user32 = FakeUser32()

        original = bridge.ctypes.windll
        bridge.ctypes.windll = FakeWindll()
        try:
            bridge.show_console_window({"show_console_window": True})
            self.assertEqual(calls, [])
            bridge.show_console_window({})
            self.assertEqual(calls, [(1234, 0)])
        finally:
            bridge.ctypes.windll = original


    def test_compaction_and_reset_commands_are_local(self):
        base = {"username": "wxid_main", "sender_id": 4, "type": "文本"}
        self.assertEqual(classify_message({**base, "content": "/compact"}, 1), ("compact", ""))
        self.assertEqual(classify_message({**base, "content": "/reset"}, 1), ("reset", ""))

    def test_reset_starts_a_brand_new_openclaw_session(self):
        cfg = {"session_key_prefix": "wechat-ai-public", "agent": "wechat-public"}
        chat = {}
        first = bridge.openclaw_session_key(cfg, "wxid_main", int(chat.get("session_epoch", 0)))
        self.assertTrue(first.startswith("agent:wechat-public:wechat-ai-public:"))
        self.assertIn("已开启新会话", bridge.reset_session_reply(chat))
        self.assertEqual(chat["session_epoch"], 1)
        second = bridge.openclaw_session_key(cfg, "wxid_main", int(chat["session_epoch"]))
        self.assertNotEqual(first, second)
        self.assertEqual(second, bridge.openclaw_session_key(cfg, "wxid_main", 1))

    def test_compact_reply_reports_the_token_delta(self):
        cfg = {"session_key_prefix": "wechat-ai-public", "agent": "wechat-public"}
        chat = {"session_epoch": 0}
        calls = []

        def fake_compact(config, peer, epoch=0, timeout=900.0):
            calls.append((peer, epoch))
            return {"compacted": True, "tokensBefore": 26944, "tokensAfter": 10400}

        original = bridge.compact_openclaw_session
        bridge.compact_openclaw_session = fake_compact
        try:
            reply = bridge.compact_session_reply(cfg, "wxid_main", chat)
        finally:
            bridge.compact_openclaw_session = original
        self.assertIn("26944", reply)
        self.assertIn("10400", reply)
        self.assertEqual(calls, [("wxid_main", 0)])

    def test_compact_reply_reports_when_nothing_was_needed(self):
        cfg = {"session_key_prefix": "wechat-ai-public", "agent": "wechat-public"}

        def fake_compact(config, peer, epoch=0, timeout=900.0):
            return {"compacted": False}

        original = bridge.compact_openclaw_session
        bridge.compact_openclaw_session = fake_compact
        try:
            reply = bridge.compact_session_reply(cfg, "wxid_main", {})
        finally:
            bridge.compact_openclaw_session = original
        self.assertIn("无需压缩", reply)

    def test_slow_turn_triggers_automatic_compaction_only_when_slow(self):
        cfg = {"session_key_prefix": "wechat-ai-public", "agent": "wechat-public"}
        compacted = []

        def fake_compact(config, peer, epoch=0, timeout=900.0):
            compacted.append(peer)
            return {"compacted": True, "tokensBefore": 30000, "tokensAfter": 9000}

        original = bridge.compact_openclaw_session
        bridge.compact_openclaw_session = fake_compact
        try:
            bridge.maybe_auto_compact(cfg, "wxid_main", {}, bridge.COMPACT_AFTER_SECONDS - 1)
            self.assertEqual(compacted, [])
            bridge.maybe_auto_compact(cfg, "wxid_main", {}, bridge.COMPACT_AFTER_SECONDS + 1)
            self.assertEqual(compacted, ["wxid_main"])
        finally:
            bridge.compact_openclaw_session = original
        self.assertFalse(bridge.should_compact_after_turn(5.0))
        self.assertTrue(bridge.should_compact_after_turn(120.0))

    def test_context_threshold_triggers_before_turn(self):
        self.assertFalse(bridge.should_compact_before_turn(22000, 32768, 0.70))
        self.assertTrue(bridge.should_compact_before_turn(23000, 32768, 0.70))
        self.assertFalse(bridge.should_compact_before_turn(0, 32768, 0.70))

    def test_switch_to_qwen_uses_local_budget_before_compaction(self):
        from unittest.mock import patch
        usage = {"totalTokens": 27436, "contextTokens": 1000000}
        with patch.object(bridge, "get_openclaw_session_usage", return_value=usage), \
                patch.object(bridge, "compact_openclaw_session", return_value={}) as compact:
            bridge.maybe_auto_compact_before_turn({}, "wxid_main", {"model": "qwen"})
            compact.assert_called_once()

    def test_deepseek_keeps_its_larger_context_budget(self):
        from unittest.mock import patch
        usage = {"totalTokens": 27436, "contextTokens": 1000000}
        with patch.object(bridge, "get_openclaw_session_usage", return_value=usage), \
                patch.object(bridge, "compact_openclaw_session", return_value={}) as compact:
            bridge.maybe_auto_compact_before_turn({}, "wxid_main", {"model": "deepseek"})
            compact.assert_not_called()

    def test_sanitize_reply_removes_openclaw_truncation_notice(self):
        text = "这是已经生成的内容。\n\n⚠️ Reply truncated at the model's output token limit. The text above is partial — ask to continue it."
        self.assertEqual(bridge.sanitize_reply(text), "这是已经生成的内容。")


    def test_ask_openclaw_accepts_a_session_epoch(self):
        import inspect

        parameters = inspect.signature(bridge.ask_openclaw).parameters
        self.assertIn("epoch", parameters)
        self.assertEqual(parameters["epoch"].default, 0)
        self.assertIn("epoch", inspect.signature(bridge.build_openclaw_command).parameters)

    def test_openclaw_command_uses_the_epoch_session_key(self):
        cfg = {"agent": "wechat-public", "session_key_prefix": "wechat-ai-public",
               "models": {"qwen": "llama-cpp/example"}}
        base = bridge.build_openclaw_command(cfg, "wxid_main", "qwen", "off", "你好")
        fresh = bridge.build_openclaw_command(cfg, "wxid_main", "qwen", "off", "你好", epoch=3)
        key_index = base.index("--session-key") + 1
        self.assertNotEqual(base[key_index], fresh[key_index])
        self.assertEqual(fresh[key_index],
                         bridge.session_key(cfg, "wxid_main", 3))

    def test_every_ask_openclaw_call_site_passes_only_supported_keywords(self):
        import ast
        import inspect
        from pathlib import Path as _Path

        source = _Path(bridge.__file__).read_text(encoding="utf-8")
        supported = set(inspect.signature(bridge.ask_openclaw).parameters)
        tree = ast.parse(source)
        call_sites = 0
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = getattr(func, "id", None) or getattr(func, "attr", None)
            if name != "ask_openclaw":
                continue
            call_sites += 1
            for keyword in node.keywords:
                self.assertIn(keyword.arg, supported,
                              f"line {node.lineno}: ask_openclaw() got unsupported "
                              f"keyword {keyword.arg!r}")
        self.assertGreaterEqual(call_sites, 3, "expected direct, group and image call sites")


    def test_help_notice_renders_a_real_date(self):
        from datetime import datetime, timezone

        rendered = bridge.render_daily_help(datetime(2026, 9, 20, 1, 0, tzinfo=timezone.utc))
        self.assertTrue(rendered.startswith("时间：2026年9月20日"))
        for placeholder in ("{year}", "{month}", "{day}"):
            self.assertNotIn(placeholder, rendered)

    def test_help_notice_uses_tokyo_date_across_midnight(self):
        from datetime import datetime, timezone

        before = bridge.render_daily_help(datetime(2026, 9, 19, 14, 59, tzinfo=timezone.utc))
        after = bridge.render_daily_help(datetime(2026, 9, 19, 15, 0, tzinfo=timezone.utc))
        self.assertIn("2026年9月19日", before)
        self.assertIn("2026年9月20日", after)

    def test_daily_notice_and_help_command_agree(self):
        from datetime import datetime, timezone

        instant = datetime(2026, 9, 20, 1, 0, tzinfo=timezone.utc)
        chat = {}
        self.assertEqual(bridge.claim_daily_help_notice(chat, instant),
                         bridge.render_daily_help(instant))

    def test_no_code_path_returns_the_raw_help_template(self):
        import ast
        from pathlib import Path as _Path

        source = _Path(bridge.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        offenders = []
        for node in ast.walk(tree):
            # any `x = DAILY_HELP_TEMPLATE` (bare name, not a call) leaks placeholders
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Name) \
                    and node.value.id == "DAILY_HELP_TEMPLATE":
                offenders.append(node.lineno)
        self.assertEqual(offenders, [],
                         f"lines {offenders} send the unrendered help template; "
                         "use render_daily_help()")


if __name__ == "__main__":
    unittest.main()

