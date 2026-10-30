"""Regression checks for the DeepSeek-only migration (no network calls)."""
import unittest
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock
from unittest.mock import patch
from outputs.WeChatAI import bridge
from outputs.WeChatAI import vision_client


class DeepSeekOnlyTests(unittest.TestCase):
    def test_local_commands_do_not_switch_models_or_modes(self):
        for text in ('/qwen', '/think', '/fast', '切换千问', '切换到千问，你好'):
            action = bridge.classify_message({'type': '文本', 'content': text, 'sender_id': 2}, 1)
            self.assertNotIn(action[0], ('model', 'thinking', 'switch_prompt'), text)

    def test_legacy_model_selection_cannot_reach_local_provider(self):
        command = bridge.build_openclaw_command(
            {'agent': 'wechat-public', 'session_key_prefix': 'test',
             'models': {'deepseek': 'deepseek/deepseek-flash'}},
            'diagnostic', 'qwen', 'high', 'hello')
        self.assertEqual(command[command.index('--model') + 1], 'deepseek/deepseek-flash')
        self.assertNotIn('--thinking', command)

    def test_saved_sessions_migrate_without_losing_history(self):
        state = {'chats': {'one': {'model': 'qwen', 'thinking': 'high',
                                  'session_epoch': 3, 'last_seq': 123}}}
        bridge.migrate_deepseek_sessions(state)
        self.assertEqual(state['chats']['one']['model'], 'deepseek')
        self.assertEqual(state['chats']['one']['thinking'], 'off')
        self.assertEqual(state['chats']['one']['session_epoch'], 3)
        self.assertEqual(state['chats']['one']['last_seq'], 123)

    def test_help_has_no_local_model_commands(self):
        for token in ('千问', '/qwen', '/think', '/fast'):
            self.assertNotIn(token, bridge.DAILY_HELP_TEMPLATE)

    def test_status_does_not_claim_local_fast_or_thinking_mode(self):
        reply = bridge.format_timed_reply('你好', 1.2, 'qwen', 'high')
        self.assertEqual('你好', reply)
        self.assertNotIn('思考模式', reply)

    def test_drawing_request_has_no_generation_dispatch(self):
        for text in ('/draw 一只猫', '画一张海报', '改一下图片'):
            action = bridge.classify_message({'type': '文本', 'content': text, 'sender_id': 2}, 1)
            self.assertNotEqual(action[0], 'draw')
        self.assertFalse(hasattr(bridge, 'process_drawing_task'))

    def test_vision_uses_authenticated_deepseek_even_for_legacy_alias(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps({
            'choices': [{'message': {'content': '一只猫'}}]}).encode()
        with TemporaryDirectory() as folder:
            image = Path(folder)/'test.png'
            image.write_bytes(b'fixture-image')
            with patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'test-key'}), \
                    patch.object(vision_client.urllib.request, 'urlopen', return_value=response) as request:
                result = vision_client.describe_image(image, '描述图片', {}, model_alias='qwen')
            sent = request.call_args.args[0]
            self.assertEqual(sent.full_url, 'https://api.deepseek.com/chat/completions')
            self.assertEqual(sent.get_header('Authorization'), 'Bearer test-key')
            self.assertNotIn('chat_template_kwargs', json.loads(sent.data))
            self.assertEqual(result, '一只猫')

    def test_configuration_and_supervisor_have_no_local_dependency(self):
        root = Path(__file__).resolve().parent.parent
        config_path = root/'outputs/WeChatAI/config.json'
        # config.json is local-only (gitignored), so a fresh clone checks the scripts alone.
        if config_path.exists():
            config = json.loads(config_path.read_text(encoding='utf-8'))
            self.assertEqual(set(config['models']), {'deepseek'})
            self.assertEqual(config['initial_model'], 'deepseek')
            self.assertFalse(any(k.startswith(('image_generation_', 'image_edit_', 'local_image_edit_')) for k in config))
        for name in ('startup_backend.ps1', 'wait_for_ai_wechat.ps1', 'manage_bot_stack.ps1'):
            source = (root/'outputs/WeChatAI'/name).read_text(encoding='utf-8')
            for token in ('8080', 'llama-server', '$llamaOnline'):
                self.assertNotIn(token, source, name)


if __name__ == '__main__':
    unittest.main()
