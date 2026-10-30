import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch


class ControlCallTests(unittest.TestCase):
    def test_short_json_control_disables_thinking_and_rejects_truncation(self):
        import io
        from outputs.WeChatAI import vision_client
        sent=[]
        responses=[{'choices':[{'finish_reason':'stop','message':{'content':'{"action":"reply"}'}}]},
                   {'choices':[{'finish_reason':'length','message':{'content':'{"action":'}}]}]
        def respond(request,**kwargs):
            sent.append(json.loads(request.data))
            return io.BytesIO(json.dumps(responses.pop(0)).encode())
        with patch.dict('os.environ',{'DEEPSEEK_API_KEY':'test-placeholder'}), \
                patch.object(vision_client.urllib.request,'urlopen',side_effect=respond):
            self.assertEqual(json.loads(vision_client.request_text_json('JSON',{},160))['action'],'reply')
            self.assertEqual(sent[0]['thinking'],{'type':'disabled'})
            with self.assertRaisesRegex(vision_client.VisionError,'截断'):
                vision_client.request_text_json('JSON',{},160)


class SessionRuntimeTests(unittest.TestCase):
    def test_gui_fallback_serializes_window_operations(self):
        from outputs.WeChatAI import bridge
        from types import SimpleNamespace
        entered,release,second=threading.Event(),threading.Event(),threading.Event()
        failures=[]
        class DB:
            def get_messages(self,*args,**kwargs):return []
        class GUI:
            def send_msg(self,text,peer,**kwargs):
                if peer=='a':entered.set();release.wait(2)
                else:second.set()
                return {'success':True}
        def send(peer):
            try:bridge.send_wechat(DB(),peer,'text',1,gui=GUI(),config={'send_mode':'gui'})
            except Exception as exc:failures.append(exc)
        with patch.dict('sys.modules',{'wechatauto.guia':SimpleNamespace(WeChatGUI=GUI)}), \
                patch.object(bridge,'get_foreground_window',return_value=1), \
                patch.object(bridge,'restore_foreground_window'), \
                patch.object(bridge,'resolve_search_name',side_effect=lambda db,peer:peer), \
                patch.object(bridge,'was_sent',return_value=True):
            a=threading.Thread(target=send,args=('a',));b=threading.Thread(target=send,args=('b',))
            try:
                a.start();self.assertTrue(entered.wait(1));b.start()
                self.assertFalse(second.wait(.1))
            finally:release.set();a.join(2);b.join(2)
        self.assertTrue(second.is_set());self.assertFalse(failures)

    def test_slow_peer_does_not_block_other_peer_and_fifo(self):
        from outputs.WeChatAI.session_runtime import SessionDispatcher
        entered, release, other = threading.Event(), threading.Event(), threading.Event()
        order = []
        def handle(peer, value):
            if value == 1:
                entered.set()
                release.wait(2)
            order.append((peer, value))
            if peer == 'b': other.set()
        dispatcher = SessionDispatcher(handle)
        try:
            dispatcher.enqueue('a', 1)
            self.assertTrue(entered.wait(1))
            dispatcher.enqueue('a', 2)
            dispatcher.enqueue('b', 3)
            self.assertTrue(other.wait(.5))
            self.assertNotIn(('a', 2), order)
            release.set()
            self.assertTrue(dispatcher.join(2))
            self.assertLess(order.index(('a', 1)), order.index(('a', 2)))
        finally:
            release.set(); dispatcher.shutdown()

    def test_db_lock_is_released_before_inference_and_retry_wait(self):
        from outputs.WeChatAI.session_runtime import LockedDB
        lock = threading.RLock()
        class DB:
            def get_messages(self):
                self_locked = lock._is_owned()
                return self_locked
        db = LockedDB(DB(), lock)
        self.assertTrue(db.get_messages())
        self.assertFalse(lock._is_owned())

    def test_failure_only_affects_one_task(self):
        from outputs.WeChatAI.session_runtime import SessionDispatcher
        results = []
        def handler(peer, value):
            if value == 'bad': raise RuntimeError('fixture')
            results.append(value)
        dispatcher = SessionDispatcher(handler)
        try:
            for value in ('bad', 'good'): dispatcher.enqueue('a', value)
            self.assertTrue(dispatcher.join(2))
            self.assertEqual(results, ['good'])
        finally: dispatcher.shutdown()

    def test_runtime_deduplicates_without_advancing_other_chat_watermark(self):
        from outputs.WeChatAI.session_runtime import ConversationRuntime
        state = {'activated_at': 0, 'chats': {'a': {'last_seq': 0}, 'b': {'last_seq': 0}}}
        class DB:
            def get_sessions(self, limit): return [{'username': 'a'}, {'username': 'b'}]
            def get_new_messages(self, peer, since_seq, limit):
                return [{'sort_seq': 10, 'sender_id': 2}] if peer == 'a' and since_seq < 10 else []
        seen = []
        def process(db, config, state, **kwargs):
            peer=db.get_sessions(1)[0]['username']
            seen.extend(db.get_new_messages(peer,0,100))
            state['chats'][peer]['last_seq']=10
        runtime = ConversationRuntime(DB(), {}, state, process)
        try:
            runtime.poll(); runtime.poll()
            self.assertTrue(runtime.dispatcher.join(2))
            self.assertEqual(len(seen), 1)
            self.assertEqual(state['chats']['b']['last_seq'], 0)
        finally: runtime.shutdown()


class OutputTests(unittest.TestCase):
    def test_debug_cannot_resurrect_removed_status_bar(self):
        from outputs.WeChatAI.humanize_output import toggle_debug
        a={}
        toggle_debug(a,'on',now=10)
        self.assertNotIn('debug_until',a)
        from outputs.WeChatAI import bridge
        self.assertEqual(bridge.format_timed_reply('你好',5,'deepseek','off',
                         config={'debug_status_bar':True},chat={'debug_until':9999}),'你好')

    def test_plain_text_removes_markdown_not_urls_or_math(self):
        from outputs.WeChatAI.humanize_output import plain_text
        text=plain_text('## 标题\n**结论**：见[来源](https://example.org/a?q=1)\n2 * 3 = 6')
        self.assertNotIn('**',text)
        self.assertNotIn('##',text)
        self.assertIn('https://example.org/a?q=1',text)
        self.assertIn('2 * 3 = 6',text)

    def test_status_is_hidden_with_production_config(self):
        from outputs.WeChatAI import bridge
        reply=bridge.format_timed_reply('**你好**',1,'deepseek','off',config={},chat={})
        self.assertEqual(reply,'你好')


class StickerTests(unittest.TestCase):
    def test_references_to_same_message_do_not_inflate_appearance_count(self):
        from outputs.WeChatAI.sticker_library import StickerLibrary
        with tempfile.TemporaryDirectory() as folder:
            library=StickerLibrary(Path(folder)/'s.db')
            facts=lambda *args:'{"description":"猫","text":"好","changes":""}'
            tags=lambda *args:'{"emotion":"开心","tags":["开心"],"intensity":2}'
            library.describe('a'*32,'x','one',facts,tags,event_id='g:1')
            item=library.describe('a'*32,'x','one',facts,tags,event_id='g:1')
            self.assertEqual(item['appearance_count'],1)
            library.close()

    def test_second_md5_hit_skips_vision_and_tracks_count(self):
        from outputs.WeChatAI.sticker_library import StickerLibrary
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as folder:
            library=StickerLibrary(Path(folder)/'stickers.sqlite3')
            vision=Mock(return_value='{"description":"狗头","text":"懂的都懂","changes":"眨眼"}')
            tags=Mock(return_value='{"emotion":"开心","tags":["嘲讽"],"intensity":2}')
            a=library.describe('a'*32,'a.png','user-a',vision,tags)
            b=library.describe('a'*32,'a.png','user-b',vision,tags)
            vision.assert_called_once(); tags.assert_called_once()
            self.assertEqual(b['appearance_count'],2)
            self.assertEqual(b['last_user'],'user-b')
            self.assertIn('懂的都懂',library.marker(a))
            library.close()

    def test_invalid_json_retries_once_then_is_cached_unknown(self):
        from outputs.WeChatAI.sticker_library import StickerLibrary
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as folder:
            library=StickerLibrary(Path(folder)/'s.db')
            vision=Mock(return_value='not json');tags=Mock()
            item=library.describe('b'*32,'a.png','user',vision,tags)
            self.assertEqual(vision.call_count,2)
            self.assertEqual(item['description'],'未识别表情')
            library.describe('b'*32,'a.png','user',vision,tags)
            self.assertEqual(vision.call_count,2)
            tags.assert_not_called();library.close()

    def test_sticker_frames_include_middle_and_last(self):
        from outputs.WeChatAI.sticker_library import prepare_sticker_frames
        from PIL import Image
        with tempfile.TemporaryDirectory() as folder:
            src=Path(folder)/'a.gif'
            frames=[Image.new('RGB',(8,8),c) for c in ('red','green','blue')]
            frames[0].save(src,save_all=True,append_images=frames[1:],duration=20,loop=0)
            path=prepare_sticker_frames(src,Path(folder))
            with Image.open(path) as sheet:
                self.assertEqual(sheet.size,(24,8))
                self.assertEqual(sheet.getpixel((20,4)),(0,0,255))

    def test_md5_is_strictly_an_emoji_attribute(self):
        from outputs.WeChatAI.sticker_library import message_md5
        self.assertEqual(message_md5('<emoji md5="'+'A'*32+'"/>'),'a'*32)
        self.assertIsNone(message_md5('prefix '+'a'*32+' other text'))


class GateTests(unittest.TestCase):
    def make_gate(self,folder,call=None,**overrides):
        from outputs.WeChatAI.humanize_gate import HumanizeGate
        config={'humanize_enabled':True,'humanize_groups':['g@chatroom'],'humanize_shadow_mode':False,
                'ai_sender_id':1,'humanize_talk_value':.5}
        config.update(overrides)
        gate=HumanizeGate(config,Path(folder),call or (lambda p:'{"action":"reply"}'),
                          lambda peer,msg:True,quiet=lambda msg:False,
                          namer=lambda peer,msg:{'wxid_a':'小明','wxid_b':'阿花'}.get(msg.get('sender_username'),'群友'))
        gate.queued=[];gate.send=lambda peer,msg:gate.queued.append(msg) or True
        return gate

    def msg(self,now,text='有人看昨天的比赛吗',sender='wxid_a'):
        return {'sort_seq':int(now*1000),'content':f'{sender}:\n{text}','sender_username':sender,
                'sender_id':1 if sender=='bot' else 2,'type':'文本'}

    def test_name_call_replies_without_a_model_call(self):
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as folder:
            call=Mock();gate=self.make_gate(folder,call)
            gate.observe('g@chatroom',self.msg(1000,'大肥鱼呢'),now=1000)
            gate.flush('g@chatroom',now=1004)
            call.assert_not_called()
            self.assertEqual([m['content'] for m in gate.queued],['大肥鱼呢'])
            self.assertTrue(gate.can_dispatch('g@chatroom','1000000',now=1005))
            gate.note_reply('g@chatroom',now=1006,anchor='1000000',sender=self.msg(1000))
            self.assertIn('1000000',gate.peers['g@chatroom']['handled'])
            self.assertEqual(len(gate.peers['g@chatroom']['replies']),1)
            gate.shutdown()

    def test_whitelist_and_quiet_hours_block_everything(self):
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as folder:
            call=Mock();gate=self.make_gate(folder,call)
            gate.observe('other@chatroom',self.msg(1000,'大肥鱼呢'),now=1000)
            gate.flush('other@chatroom',now=1004)
            gate.quiet=lambda msg:True
            gate.observe('g@chatroom',self.msg(1000,'大肥鱼呢'),now=1000)
            gate.flush('g@chatroom',now=1004)
            call.assert_not_called();self.assertEqual(gate.queued,[]);gate.shutdown()

    def test_plain_chat_asks_the_model_with_names_and_obeys_it(self):
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as folder:
            call=Mock(return_value='{"action":"ignore"}');gate=self.make_gate(folder,call)
            gate.observe('g@chatroom',self.msg(990,'我先说一句',sender='wxid_b'),now=990)
            gate.observe('g@chatroom',self.msg(1000),now=1000)
            gate.flush('g@chatroom',now=1004)
            prompt=call.call_args.args[0]
            self.assertIn('阿花：我先说一句',prompt);self.assertIn('小明：有人看昨天的比赛吗',prompt)
            self.assertEqual(gate.queued,[])
            self.assertIn('1000000',gate.peers['g@chatroom']['handled'])
            call.return_value='{"action":"reply"}'
            gate.observe('g@chatroom',self.msg(1100,'真的没人看吗'),now=1100)
            gate.flush('g@chatroom',now=1104)
            self.assertEqual(len(gate.queued),1);gate.shutdown()

    def test_minimum_gap_and_hourly_target_skip_before_any_model_call(self):
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as folder:
            call=Mock();gate=self.make_gate(folder,call)
            gate.observe('g@chatroom',self.msg(990,'刚说完',sender='bot'),now=990)
            gate.observe('g@chatroom',self.msg(1000),now=1000)
            gate.flush('g@chatroom',now=1004)
            call.assert_not_called()
            gate.peers['g@chatroom']['replies']=[500]*6
            gate.observe('g@chatroom',self.msg(1100),now=1100)
            gate.flush('g@chatroom',now=1104)
            call.assert_not_called()
            # A name call still gets through once the hourly target is used up.
            gate.observe('g@chatroom',self.msg(1200,'大肥鱼你说呢'),now=1200)
            gate.flush('g@chatroom',now=1204)
            self.assertEqual(len(gate.queued),1);gate.shutdown()

    def test_same_member_continuing_the_chat_uses_the_short_gap(self):
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as folder:
            call=Mock(return_value='{"action":"reply"}');gate=self.make_gate(folder,call)
            gate.peers['g@chatroom']={'replies':[500]*6}
            gate.note_reply('g@chatroom',now=1000,sender=self.msg(990,'大肥鱼呢'))
            gate.observe('g@chatroom',self.msg(1008,'那你觉得呢'),now=1008)
            gate.flush('g@chatroom',now=1012)
            self.assertEqual(call.call_count,1);self.assertEqual(len(gate.queued),1)
            gate.note_reply('g@chatroom',now=1015,anchor='1008000',sender=self.msg(1008))
            # Someone else 10 s later is not continuing a chat with the bot.
            gate.observe('g@chatroom',self.msg(1025,'我也觉得',sender='wxid_b'),now=1025)
            gate.flush('g@chatroom',now=1029)
            self.assertEqual(call.call_count,1);gate.shutdown()

    def test_only_one_reply_in_flight(self):
        with tempfile.TemporaryDirectory() as folder:
            gate=self.make_gate(folder)
            gate.observe('g@chatroom',self.msg(1000,'大肥鱼呢'),now=1000)
            gate.flush('g@chatroom',now=1004)
            gate.observe('g@chatroom',self.msg(1010,'大肥鱼？'),now=1010)
            gate.flush('g@chatroom',now=1014)
            self.assertEqual(len(gate.queued),1)
            gate.note_failed('g@chatroom','1000000',now=1015)
            self.assertFalse(gate.can_dispatch('g@chatroom','1000000',now=1016))
            gate.shutdown()

    def test_shadow_logs_without_sending_or_chat_text(self):
        with tempfile.TemporaryDirectory() as folder:
            gate=self.make_gate(folder,humanize_shadow_mode=True)
            secret='大肥鱼这个私有测试内容你怎么看'
            gate.observe('g@chatroom',self.msg(1000,secret),now=1000)
            gate.flush('g@chatroom',now=1004)
            log=(Path(folder)/'humanize_gate.log').read_text(encoding='utf-8')
            self.assertNotIn(secret,log);self.assertIn('shadow',log)
            self.assertEqual(gate.queued,[]);gate.shutdown()

    def test_state_from_the_previous_gate_still_loads(self):
        with tempfile.TemporaryDirectory() as folder:
            old={'g@chatroom':{'created':0,'replies':[1],'shadow_replies':[],'handled':{},'waits':{'5':1},
                 'activity':[],'last_bot':0,'topics':[],'scheduled':{'900000':900},'called':{}}}
            (Path(folder)/'humanize_state.json').write_text(json.dumps(old),encoding='utf-8')
            gate=self.make_gate(folder)
            self.assertEqual(gate.peers['g@chatroom']['scheduled'],{})
            gate.observe('g@chatroom',self.msg(1000,'大肥鱼呢'),now=1000)
            gate.flush('g@chatroom',now=1004)
            self.assertEqual(len(gate.queued),1);gate.shutdown()


    def sticker(self,now,sender='wxid_a'):
        return dict(self.msg(now,'<msg><emoji md5="'+'a'*32+'" cdnurl="http://x/@y"/></msg>',sender),type='动画表情')

    def test_sticker_from_someone_chatting_with_the_bot_is_answered(self):
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as folder:
            call=Mock();gate=self.make_gate(folder,call)
            gate.note_reply('g@chatroom',now=1000,sender=self.msg(990,'大肥鱼呢'))
            gate.observe('g@chatroom',self.sticker(1010),now=1010)
            gate.flush('g@chatroom',now=1014)
            call.assert_not_called()
            self.assertEqual(len(gate.queued),1)
            self.assertIn('<emoji',gate.queued[0]['content'])
            self.assertEqual(gate.queued[0]['type'],'动画表情');gate.shutdown()

    def test_unrelated_sticker_is_ignored_but_earlier_text_still_counts(self):
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as folder:
            call=Mock(return_value='{"action":"reply"}');gate=self.make_gate(folder,call)
            gate.observe('g@chatroom',self.msg(1000,'有人吃饭了吗',sender='wxid_b'),now=1000)
            gate.observe('g@chatroom',self.sticker(1001),now=1001)
            gate.flush('g@chatroom',now=1005)
            self.assertIn('小明：[表情包]',call.call_args.args[0])
            self.assertEqual([m['content'] for m in gate.queued],['有人吃饭了吗'])
            gate.observe('g@chatroom',self.sticker(1100,sender='wxid_b'),now=1100)
            gate.flush('g@chatroom',now=1104)
            self.assertEqual(call.call_count,1);gate.shutdown()

    def test_name_call_is_picked_up_after_one_second(self):
        with tempfile.TemporaryDirectory() as folder:
            gate=self.make_gate(folder);picked=[]
            gate.dispatcher.enqueue=lambda peer,instant:picked.append(peer) or True
            gate.observe('g@chatroom',self.msg(1000,'今天好冷'),now=1000)
            gate.tick(now=1001.5);self.assertEqual(picked,[])
            gate.buffers.clear();gate.running.clear()
            gate.observe('g@chatroom',self.msg(1100,'大肥鱼你在吗'),now=1100)
            gate.tick(now=1101.5);self.assertEqual(picked,['g@chatroom']);gate.shutdown()


    def test_follow_ups_after_an_unanswered_mention_wait_for_that_reply(self):
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as folder:
            call=Mock(return_value='{"action":"reply"}');gate=self.make_gate(folder,call)
            gate.observe('g@chatroom',self.msg(1000,'@大肥鱼 你知道空洞骑士吗'),now=1000)
            gate.observe('g@chatroom',self.msg(1003,'就是那个游戏'),now=1003)
            gate.flush('g@chatroom',now=1007)
            call.assert_not_called();self.assertEqual(gate.queued,[])
            gate.note_reply('g@chatroom',now=1010,sender=self.msg(1000,'@大肥鱼 你知道空洞骑士吗'))
            gate.observe('g@chatroom',self.msg(1015,'那你玩过吗'),now=1015)
            gate.flush('g@chatroom',now=1019)
            self.assertEqual(call.call_count,1);self.assertEqual(len(gate.queued),1);gate.shutdown()


class HumanMemoryTests(unittest.TestCase):
    def test_negation_questions_and_sensitive_topics_are_not_profiles(self):
        from outputs.WeChatAI.humanize_memory import update_profile
        chat={}
        for text in ('不要叫我小明。','我喜欢摄影吗？其实不喜欢。','我喜欢佛教音乐。'):
            update_profile(chat,'a',text)
        self.assertFalse(chat.get('member_profiles',{}).get('a'))
    def test_mood_clamps_and_decays_hourly(self):
        from outputs.WeChatAI.humanize_memory import update_mood
        chat={}
        for _ in range(9):update_mood(chat,'你真厉害',now=100)
        self.assertEqual(chat['humanize_mood']['value'],5)
        self.assertEqual(update_mood(chat,'',now=7300),3)
        self.assertEqual(update_mood(chat,'',now=999999),0)

    def test_profiles_only_keep_explicit_non_sensitive_preferences(self):
        from outputs.WeChatAI.humanize_memory import update_profile,context_for
        chat={}
        update_profile(chat,'a','叫我小明，我喜欢摄影。')
        update_profile(chat,'b','叫我小红，我喜欢蓝色。')
        update_profile(chat,'a','我喜欢吃治疗焦虑的药，我的电话是13800000000。')
        prompt=context_for(chat,'a')
        self.assertIn('小明',prompt);self.assertIn('摄影',prompt)
        self.assertNotIn('小红',prompt);self.assertNotIn('13800000000',prompt);self.assertNotIn('焦虑',prompt)

    def test_clear_removes_mood_and_profiles_from_only_current_chat(self):
        from outputs.WeChatAI import bridge
        chat={'model':'deepseek','humanize_mood':{'value':3},'member_profiles':{'a':{'name':'甲'}}}
        other={'member_profiles':{'b':{'name':'乙'}}}
        state={'chats':{'g@chatroom':chat,'h@chatroom':other}}
        bridge.clear_chat_memory(state,'g@chatroom',chat)
        self.assertNotIn('humanize_mood',chat);self.assertNotIn('member_profiles',chat)
        self.assertIn('member_profiles',other)

    def test_delay_is_bounded_and_splitting_max_three(self):
        from outputs.WeChatAI.humanize_output import natural_delay,reply_segments
        for size in (1,10,100,1000):
            for _ in range(10):self.assertTrue(.8<=natural_delay('字'*size,{})<=3)
        self.assertLessEqual(len(reply_segments('甲。\n\n乙。\n\n丙。\n\n丁。',{})),3)



if __name__ == '__main__': unittest.main()
