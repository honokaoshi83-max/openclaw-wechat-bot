"""Recorded integrations for the read-only review findings and status-bar removal."""
import ast
import re
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent
p=ROOT/'outputs/WeChatAI/bridge.py';s=p.read_text(encoding='utf-8')
tree=ast.parse(s);node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='send_wechat')
lines=s.splitlines(keepends=True)
start=next(i for i in range(node.lineno-1,node.end_lineno) if lines[i].startswith('    from wechatauto.guia'))
body=lines[start:node.end_lineno]
body=[line.replace('                return\n','                callback=DELIVERY_CALLBACK.get()\n                if callback:callback()\n                return\n') for line in body]
lines[start:node.end_lineno]=['    with GUI_SEND_LOCK:\n']+['    '+line if line.strip() else line for line in body]
s=''.join(lines)
main=next(n for n in ast.parse(s).body if isinstance(n,ast.FunctionDef) and n.name=='main')
node=next(n for n in main.body if isinstance(n,ast.FunctionDef) and n.name=='handle_group_task')
replacement='''    def handle_group_task(task: dict) -> None:
        peer=task['peer'];ambient=bool(task.get('ambient'))
        anchor=str(task['msg'].get('sort_seq',0))
        finished=threading.Event();timer=None;guard_token=receipt_token=None
        delivered=[False];group_db=None
        def alive():
            return (not is_tokyo_quiet_hours() and HUMANIZE_GATE is not None and
                    HUMANIZE_GATE.enabled(peer) and not config.get('humanize_shadow_mode',True) and
                    task.get('gate_generation')==HUMANIZE_GATE.generations.get(peer,0) and
                    int(task.get('session_epoch',0))==int(state['chats'][peer].get('session_epoch',0)) and
                    0<=time.time()-float(anchor)/1000<=float(config.get('humanize_anchor_max_age_seconds',1800)))
        def guard():
            return alive() and (delivered[0] or HUMANIZE_GATE.can_dispatch(peer,anchor))
        def receipt():
            if not delivered[0]:
                delivered[0]=True
                if HUMANIZE_GATE:HUMANIZE_GATE.note_reply(peer,anchor=anchor if ambient else None)
        if ambient and not guard():
            if HUMANIZE_GATE:HUMANIZE_GATE.note_failed(peer,anchor)
            return
        try:
            with group_db_lock:
                group_db=group_dbs.get(peer)
                if group_db is None:
                    digest=hashlib.sha256(peer.encode('utf-8')).hexdigest()[:12]
                    group_db=WeChatDB(db_dir=resolve_db_dir(config),account=config['ai_account_dir'],
                                     workdir=str(RUNTIME_DIR/f'group_db_{digest}'))
                    if concurrent_sessions:group_db=LockedDB(group_db,db_access_lock)
                    group_dbs[peer]=group_db
            def timeout_notice():
                if finished.is_set():return
                try:send_wechat(group_db,peer,'图片处理较慢，请稍候。',config['ai_sender_id'],config=config)
                except Exception:LOG.exception('group visual delay notice failed peer=%s',peer)
            if not ambient and (is_visual_message(task['msg']) or task.get('reference_latest_visual')):
                timer=threading.Timer(float(config.get('group_visual_timeout_seconds',45)),timeout_notice)
                timer.daemon=True;timer.start()
            receipt_token=DELIVERY_CALLBACK.set(receipt)
            if ambient:guard_token=DELIVERY_GUARD.set(guard)
            if concurrent_sessions:process_group_task(group_db,config,state,task)
            else:
                with db_access_lock:process_group_task(group_db,config,state,task)
        except Exception:
            LOG.exception('group reply failed peer=%s seq=%s',peer,anchor)
            if ambient:
                if not delivered[0] and HUMANIZE_GATE:HUMANIZE_GATE.note_failed(peer,anchor)
            else:
                try:notify_group_failure(group_db or db,config,task)
                except Exception:LOG.exception('group failure notice could not be sent peer=%s',peer)
        finally:
            finished.set()
            if timer:timer.cancel()
            if guard_token is not None:DELIVERY_GUARD.reset(guard_token)
            if receipt_token is not None:DELIVERY_CALLBACK.reset(receipt_token)
'''
lines=s.splitlines(keepends=True);lines[node.lineno-1:node.end_lineno]=[replacement]
p.write_text(''.join(lines),encoding='utf-8')

# Old formatter tests now explicitly verify that diagnostic metadata stays internal.
p=ROOT/'work/test_bridge.py';s=p.read_text(encoding='utf-8')
s=re.sub(r'\\n\\n（DeepSeek｜输出文字：[0-9]+ 字｜耗时 [0-9.]+ 秒）','',s)
s=s.replace('self.assertIn("已联网", send.call_args.args[2])','self.assertEqual("日期已核实。", send.call_args.args[2])')
s=s.replace('self.assertIn("已联网", parts[-1])','self.assertTrue(reply.networked)\n        self.assertNotIn("已联网", parts[-1])')
p.write_text(s,encoding='utf-8')
p=ROOT/'work/test_deepseek_only.py';s=p.read_text(encoding='utf-8')
s=s.replace("self.assertIn('DeepSeek', reply)","self.assertEqual('你好', reply)")
p.write_text(s,encoding='utf-8')
