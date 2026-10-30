import ast
from pathlib import Path
import shutil
ROOT=Path(__file__).resolve().parent.parent
archive=ROOT/'work/retired_local_tests'; archive.mkdir(exist_ok=True)
p=ROOT/'work/test_bridge.py'; s=p.read_text(encoding='utf-8')
shutil.copy2(p,archive/'test_bridge_before_migration.py.txt')
obsolete={'test_qwen_compacts_before_output_budget_is_starved',
 'test_switch_to_qwen_uses_local_budget_before_compaction',
 'test_qwen_thinking_commands_are_local','test_thinking_mode_only_changes_qwen_chat',
 'test_group_image_edit_request_collects_requirement_and_unmentioned_image',
 'test_group_image_edit_is_not_activated_when_disabled',
 'test_image_edit_outbox_prunes_only_expired_bridge_pngs',
 'test_chinese_model_switch_supports_followup_question_and_legacy_aliases',
 'test_model_commands_are_local','test_openclaw_command_passes_per_chat_thinking_mode'}
lines=s.splitlines(keepends=True)
nodes=[n for n in ast.walk(ast.parse(s)) if isinstance(n,ast.FunctionDef) and n.name in obsolete]
for node in sorted(nodes,key=lambda n:n.lineno,reverse=True): del lines[node.lineno-1:node.end_lineno]
s=''.join(lines)
s=s.replace('"qwen"','"deepseek"').replace('"llama-cpp/qwen"','"deepseek/deepseek-flash"')
s=s.replace('（快速模式｜','（DeepSeek｜').replace('（思考模式｜','（DeepSeek｜')
s=s.replace('self.assertIn("本地模型进入思考模式：/think", notice)','self.assertNotIn("/think", notice)')
s=s.replace('self.assertIn("本地模型进入快速模式：/fast", notice)','self.assertNotIn("/fast", notice)')
s=s.replace('self.assertIn("\\u5207\\u6362\\u5343\\u95ee\\u6a21\\u578b\\uff1a\\u5207\\u6362\\u5343\\u95ee", notice)','self.assertNotIn("/qwen", notice)')
s=s.replace('self.assertIn("\\u5207\\u6362\\u5343\\u95ee", help_text)','self.assertNotIn("\\u5207\\u6362\\u5343\\u95ee", help_text)')
s=s.replace('self.assertIn("qwen已支持图像识别、表情包识别、GIF识别（此模式为低精确识图）。（0.2版本更新）", notice)','self.assertIn("DeepSeek API", notice)')
s=s.replace('self.assertIn("DeepSeek已支持识图（此模式下为高准确识图）。（0.4版本更新）", notice)','self.assertNotIn("高准确", notice)')
p.write_text(s,encoding='utf-8')
p=ROOT/'work/test_persona_self_image.py';s=p.read_text(encoding='utf-8').replace("('qwen', 'deepseek')", "('deepseek',)")
p.write_text(s,encoding='utf-8')
for name in ('test_image_generation.py','test_drawing_reference.py','test_local_edit_flow.py','test_image_edit_client.py','test_image_edit_state.py'):
    p=ROOT/'work'/name
    if p.exists(): shutil.move(p,archive/(name+'.txt'))
print('Archived only removed-feature tests; retained conversation/media/search regressions.')
