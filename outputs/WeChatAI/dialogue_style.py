"""Small local tone hints and per-conversation recent replies."""
import json
import threading
import time

_recent={}
_lock=threading.Lock()
SIGNALS={
    '开心':('好开心','太好了','哈哈','笑死','太棒了'),
    '委屈':('好难过','委屈','想哭','不开心'),
    '生气':('气死','烦死','好生气','别烦我'),
    '困':('好困','困死','想睡觉'),
    '无语':('无语','服了','离谱'),
    '赞同':('确实','有道理','说得对'),
}

def emotion_hints(text):
    return [emotion for emotion,words in SIGNALS.items() if any(word in text for word in words)]

def record_reply(peer,text,config,now=None):
    if config is None or not config.get('reply_antirepeat_enabled',True):return
    now=time.time() if now is None else now
    with _lock:
        entries=[(t,s) for t,s in _recent.get(peer,[]) if now-t<600]
        entries.append((now,str(text)[:300]))
        _recent[peer]=entries[-5:]
        for key in list(_recent):
            if _recent[key] and now-_recent[key][-1][0]>=600:del _recent[key]

def instruction(peer,text,config,now=None):
    now=time.time() if now is None else now
    parts=[]
    if config.get('emotion_recognition_enabled',True):
        hints=emotion_hints(text)
        if hints:parts.append('本地语气线索='+json.dumps(hints,ensure_ascii=False)+'。只是词语线索，结合前后文判断，反话和否定可能改变含义，不做心理诊断。直接回应对方，不报出情绪标签。')
    if config.get('reply_antirepeat_enabled',True) and peer:
        with _lock:recent=[s for t,s in _recent.get(peer,[]) if now-t<600]
        if recent:parts.append('你刚才说过='+json.dumps(recent,ensure_ascii=False)+'。避免重复相同开场、动作、追问和梗；事实、数字、结论保持一致，不为了换措辞改事实。')
    return '\n\n[聊天语气参考：'+' '.join(parts)+']' if parts else ''

def clear(peer):
    with _lock:_recent.pop(peer,None)
