import sqlite3
import tempfile
import unittest
from pathlib import Path

from outputs.WeChatAI.sticker_library import StickerLibrary


class StickerSchemaTests(unittest.TestCase):
    def test_semantics_cached_without_picture_details(self):
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as folder:
            lib=StickerLibrary(Path(folder)/'s.db')
            try:
                vision=Mock(return_value='{"template":"猫头","text":"不听","emotion":"撒娇","intent":"我不想接受","intensity":2}')
                text=Mock()
                a=lib.understand('b'*32,'unused','one',vision,text,event_id='g:1')
                lib.understand('b'*32,'unused','two',vision,text,event_id='g:2')
                vision.assert_called_once();text.assert_not_called()
                marker=lib.meaning_marker(a)
                self.assertIn('我不想接受',marker);self.assertNotIn('画面',marker)
                self.assertEqual(lib.get('b'*32)['appearance_count'],2)
            finally:lib.close()

    def test_legacy_cache_converts_with_text_only_and_failure_does_not_block(self):
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as folder:
            lib=StickerLibrary(Path(folder)/'s.db')
            try:
                lib.describe('c'*32,'unused','a',lambda *x:'{"description":"猫","text":"好","changes":""}',lambda *x:'{"emotion":"开心","tags":["开心"],"intensity":2}')
                vision=Mock();text=Mock(return_value='{"template":"未知","text":"好","emotion":"赞同","intent":"我同意","intensity":1}')
                lib.understand('c'*32,'unused','a',vision,text)
                vision.assert_not_called();text.assert_called_once()
                bad=Mock(return_value='bad json')
                result=lib.understand('d'*32,'unused','a',bad,text)
                self.assertEqual(bad.call_count,2);self.assertEqual(result['intent'],'含义不明')
            finally:lib.close()

    def test_tone_and_recent_replies_are_conversation_scoped(self):
        from outputs.WeChatAI import dialogue_style as style
        style.clear('a');style.clear('b')
        style.record_reply('a','上次的玩笑',{},now=100)
        hint=style.instruction('a','好难过',{},now=101)
        self.assertIn('委屈',hint);self.assertIn('上次的玩笑',hint)
        self.assertNotIn('上次的玩笑',style.instruction('b','',{},now=101))
        self.assertEqual(style.instruction('a','好难过',{'emotion_recognition_enabled':False,'reply_antirepeat_enabled':False},now=101),'')
        style.clear('a')
        self.assertNotIn('上次的玩笑',style.instruction('a','',{},now=101))

    def test_sticker_prompts_respond_to_intent_but_image_prompt_unchanged(self):
        from outputs.WeChatAI import bridge
        single=bridge.visual_reply_prompt('动画表情','一只猫')
        paired=bridge.visual_reply_prompt('动画表情','一只猫','今天很难过')
        image=bridge.visual_reply_prompt('图片','一只猫')
        self.assertIn('不要描述画面',single)
        self.assertIn('不要描述画面',paired)
        self.assertNotIn('不要描述画面',image)

    def test_migrates_existing_library_without_losing_data(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'stickers.sqlite3'
            with sqlite3.connect(path) as db:
                db.execute('CREATE TABLE sticker_library (md5 TEXT PRIMARY KEY, description TEXT NOT NULL, text TEXT NOT NULL, changes TEXT NOT NULL, emotion TEXT NOT NULL, tags TEXT NOT NULL, intensity INTEGER NOT NULL, first_seen REAL NOT NULL, appearance_count INTEGER NOT NULL, last_user TEXT NOT NULL, status TEXT NOT NULL)')
                db.execute('INSERT INTO sticker_library VALUES (?,?,?,?,?,?,?,?,?,?,?)',('a'*32,'cat','hi','','开心','["开心"]',2,1,3,'','ok'))
            db.close()
            library=StickerLibrary(path)
            try:
                item=library.get('a'*32)
                self.assertEqual(item['description'],'cat')
                self.assertEqual(item['meaning_source'],'vision')
                self.assertEqual(item['disabled'],0)
                tables={r[0] for r in library.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                self.assertTrue({'sticker_usage','sticker_group_meanings'} <= tables)
            finally:library.close()

if __name__=='__main__':unittest.main()
