import unittest
from unittest.mock import patch
from outputs.WeChatAI import bridge


class SelfImageTests(unittest.TestCase):
    def test_both_vision_providers_keep_objective_features(self):
        for alias in ('deepseek',):
            with self.subTest(alias=alias), patch.object(bridge, '_describe_image', return_value='蓝发鲸鱼娘') as vision:
                self.assertEqual(bridge.describe_image('image.png', '客观描述', {}, model_alias=alias), '蓝发鲸鱼娘')
                args, kwargs = vision.call_args
                self.assertIn('只记录实际看见的特征', args[1])
                self.assertNotIn('角色形象，自然用第一人称', args[1])
                self.assertEqual(kwargs['model_alias'], alias)

    def test_visual_reply_knows_self_but_not_every_blue_subject(self):
        prompt = bridge.visual_reply_prompt('图片', '蓝发少女，鲸鳍和蓝白女仆装')
        self.assertIn('蓝发少女，鲸鳍和蓝白女仆装', prompt)
        self.assertIn(bridge.SELF_IMAGE_REPLY_INSTRUCTION, prompt)
        self.assertIn('只有蓝发或只有鲸鱼动物不能认作自己', prompt)

    def test_private_sticker_still_supplements_previous_text(self):
        prompt = bridge.visual_reply_prompt('动画表情', '蓝发鲸鱼娘微笑', '今天真开心')
        self.assertIn('今天真开心', prompt)
        self.assertIn('情绪或语气补充', prompt)
        self.assertIn('不要把它当作新的独立话题', prompt)
        self.assertIn(bridge.SELF_IMAGE_REPLY_INSTRUCTION, prompt)


if __name__ == '__main__':
    unittest.main()
