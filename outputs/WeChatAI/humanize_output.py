"""Plain-text output and conversation-scoped temporary diagnostics."""
import re
import random


def toggle_debug(chat,mode='',now=None,seconds=1800):
    chat.pop('debug_until',None)
    return '回复状态栏已移除，诊断信息仅保留在本机日志。'


def plain_text(text):
    text=re.sub(r'\s*[（(](?:DeepSeek|快速模式|思考模式)[｜|][^）)]*(?:输出文字|耗时)[^）)]*[）)]\s*$','',str(text))
    text=re.sub(r'(?m)^\s*```[^\n]*\n?', '', str(text)).replace('```','')
    text=re.sub(r'(?m)^\s{0,3}#{1,6}\s+','',text)
    text=re.sub(r'!?\[([^\]]+)\]\((https?://[^\s)]+)\)',r'\1（\2）',text)
    text=re.sub(r'\*\*(.+?)\*\*|__(.+?)__',lambda m:m.group(1) or m.group(2),text,flags=re.S)
    text=re.sub(r'`([^`\n]+)`',r'\1',text)
    return text.strip()


def natural_delay(text,config):
    minimum=max(.8,min(3,float(config.get('humanize_delay_min_seconds',.8))))
    maximum=max(minimum,min(3,float(config.get('humanize_delay_max_seconds',3))))
    lower=max(minimum,min(maximum,len(text)*.04))
    return random.uniform(lower,min(maximum,lower+.4))


def reply_segments(text,config):
    maximum=max(1,min(3,int(config.get('humanize_max_parts',3))))
    parts=[part.strip() for part in re.split(r'\n\s*\n',plain_text(text)) if part.strip()]
    if len(parts)<=maximum:return parts or ['']
    return parts[:maximum-1]+['\n\n'.join(parts[maximum-1:])]
