"""Reply to incoming direct messages from the logged-in AI WeChat account."""

from __future__ import annotations

import argparse
import faulthandler
import hashlib
import html as html_lib
import json
import logging
import os
import queue
import re
import subprocess
import sys
import threading
import time
import ctypes
import msvcrt
import urllib.error
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from .vision_client import VisionError, describe_image
    from .vision_media import (ImageUnavailable, cleanup_image, extract_visual,
                               is_emoji_message, is_visual_message,
                               prepare_vision_image)
except ImportError:
    from vision_client import VisionError, describe_image
    from vision_media import (ImageUnavailable, cleanup_image, extract_visual,
                              is_emoji_message, is_visual_message,
                              prepare_vision_image)


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
CONFIG_PATH = HERE / "config.json"
RUNTIME_DIR = ROOT / "work" / "wechat_ai_bridge_state"
STATE_PATH = RUNTIME_DIR / "state.json"
LOG_PATH = RUNTIME_DIR / "bridge.log"
LOG = logging.getLogger("wechat_ai_bridge")
INSTANCE_LOCK_PATH = RUNTIME_DIR / "bridge.instance.lock"
INSTANCE_LOCK_HANDLE = None
STATE_SAVE_LOCK = threading.RLock()
ONLINE_NOTICE = "当前模型已上线，如有需求可以现在开始对话"
TOKYO_TIMEZONE = timezone(timedelta(hours=9), name="Asia/Tokyo")
DAILY_HELP_TEMPLATE = """时间：{year}年{month}月{day}日
大肥鱼0.4版本
重大更新！
qwen已支持图像识别、表情包识别、GIF识别（此模式为低精确识图）。（0.2版本更新）
DeepSeek已支持识图（此模式下为高准确识图）。（0.4版本更新）
加入了联网搜索功能（0.3版本更新）。
🐋🐋🐋🐋🐋🐋🐋🐋🐋🐋🐋

使用帮助
切换deepseek模型：/deepseek
切换本地模型：/qwen
本地模型进入思考模式：/think
本地模型进入快速模式：/fast
压缩当前会话上下文：/compact
重置当前会话上下文：/reset
联网搜索：/search
清除当前会话所有记忆：/clear（⚠此操作会清除当前会话所有记忆⚠）"""
SHORT_CHAT_INSTRUCTION = (
    "\n\n[回复风格要求：这是简短聊天，请只用 1～2 句自然回应，"
    "不要主动展开话题、列清单或长篇解释。]"
)
CONVERSATION_STYLE_INSTRUCTION = (
    "\n\n[对话理解与回复限制：先判断用户是在分享、提问、求建议还是闲聊，"
    "先回应用户实际说出的事实或情绪。默认使用自然叙述，不要把每句话都写成引号台词，"
    "不要模拟双方对话、替用户编造问题或连续自言自语。分享和闲聊通常回复 1～3 句，"
    "最多追问一个相关问题；提问先直接回答；求建议给出明确判断或少量选项。"
    "只有确实适合玩笑或角色扮演时才少量使用对白体。控制在必要长度，避免重复和发散。]"
)
USER_MEMORY_ISOLATION_INSTRUCTION = (
    "\n\n[个人会话记忆隔离：你正在与当前微信用户单独对话。只能使用本次会话中该用户提供的信息，"
    "不得引用、猜测或泄露其他微信用户、群聊或其他会话的姓名、经历、偏好、项目和记忆。"
    "如果共享工作区记忆与当前对话无关，忽略它。不要把别人的信息当成当前用户信息。]"
)
INTERNAL_CONTEXT_MARKER = re.compile(
    r"(?im)(?:<!--|--)?\s*(?:project:\s*path:|observed:\s*\d{4}-\d{2}-\d{2}|assistantTexts\b)"
)
INTERNAL_CONTEXT_FALLBACK = "刚才回复格式出了点问题，你再说一次。"
MODEL_TRUNCATION_NOTICE = re.compile(
    r"\s*⚠️?\s*Reply truncated at the model's output token limit\."
    r"\s*The text above is partial\s*[—-]\s*ask to continue it\.?.*$",
    re.S | re.I,
)
# Windows caps a process command line near 32k characters; WSL/OpenClaw receives the
# whole prompt as an argument, so group transcripts must stay well below that.
GROUP_SUMMARY_HISTORY_LIMIT = 70
GROUP_SUMMARY_MAX_CHARS = 12000
GROUP_SUMMARY_OMITTED_MARK = "（较早消息已省略）"
GROUP_FAILURE_NOTICE = "AI 暂时无法回复，请稍后重发。"
GROUP_FAILURE_COOLDOWN_SECONDS = 60.0
GROUP_FAILURE_LOCK = threading.Lock()
GROUP_FAILURE_LAST: dict[str, float] = {}
# A turn slower than this almost always means the OpenClaw session outgrew its
# prompt budget and its per-turn compaction is timing out.
COMPACT_AFTER_SECONDS = 90.0
GROUP_COMPACT_AFTER_SECONDS = 30.0
CRASH_LOG_STREAM = None


def render_daily_help(now: datetime | None = None) -> str:
    """Render the help notice with a real date.

    ``DAILY_HELP_TEMPLATE`` is a ``str.format`` template, so every path that sends it
    MUST render it. Returning the raw template leaks the placeholders verbatim
    ("year年month月day日"), which is exactly what /help used to do.
    """
    instant = now or datetime.now(timezone.utc)
    tokyo_now = instant.astimezone(TOKYO_TIMEZONE)
    return DAILY_HELP_TEMPLATE.format(
        year=tokyo_now.year,
        month=tokyo_now.month,
        day=tokyo_now.day,
    )


def claim_daily_help_notice(chat: dict, now: datetime | None = None) -> str | None:
    """Return and claim today's help notice using the Tokyo calendar date."""
    instant = now or datetime.now(timezone.utc)
    date_key = instant.astimezone(TOKYO_TIMEZONE).date().isoformat()
    if chat.get("daily_help_date") == date_key:
        return None
    chat["daily_help_date"] = date_key
    return render_daily_help(instant)


def is_tokyo_quiet_hours(instant: datetime | None = None) -> bool:
    """Return whether Tokyo time is in the 00:00-08:00 quiet period."""
    value = instant or datetime.now(TOKYO_TIMEZONE)
    tokyo = value.astimezone(TOKYO_TIMEZONE)
    return tokyo.hour < 8


def message_tokyo_datetime(msg: dict) -> datetime:
    """Convert a WeChat sort sequence (Unix milliseconds) to Tokyo time."""
    seq = int(msg.get("sort_seq") or 0)
    if seq > 100000000000:
        return datetime.fromtimestamp(seq / 1000.0, tz=timezone.utc).astimezone(TOKYO_TIMEZONE)
    return datetime.now(TOKYO_TIMEZONE)


def get_foreground_window() -> int:
    """Return the current foreground window without changing desktop focus."""
    try:
        return int(ctypes.windll.user32.GetForegroundWindow())
    except Exception:
        return 0


def restore_foreground_window(hwnd: int) -> None:
    """Best-effort focus restoration after the GUI sender temporarily activates WeChat."""
    if not hwnd:
        return
    try:
        ctypes.windll.user32.SetForegroundWindow(hwnd)
    except Exception:
        LOG.debug("unable to restore foreground window", exc_info=True)


def is_direct_peer(peer: str, own_user: str) -> bool:
    return bool(peer) and peer != own_user and "@" not in peer and not peer.startswith("gh_") and peer not in {
        "weixin", "newsapp", "filehelper", "fmessage", "medianote", "floatbottle", "qmessage"
    }


def is_group_peer(peer: str, own_user: str) -> bool:
    """Return whether peer is a real group chat owned by neither system nor self."""
    return bool(peer) and peer != own_user and peer.endswith("@chatroom")


def extract_group_mention(content: str, mention_names: list[str]) -> str | None:
    """Remove a leading @bot mention and return the remaining user request."""
    text = str(content or "").replace("\u2005", " ").replace("\u2006", " ").strip()
    # Group database rows prefix the outer body with ``sender_wxid:\n``.
    # Only inspect that outer body; quoted/appmsg XML may contain an old @.
    text = re.sub(r"^[^:\n]+:\s*(?:\r?\n)?", "", text, count=1).lstrip()
    if text.startswith("<"):
        return None
    for name in mention_names:
        clean_name = str(name or "").strip().lstrip("@")
        if not clean_name:
            continue
        match = re.match(r"^@" + re.escape(clean_name) + r"(?:[\s\u200b\u3000:：,，、]*)", text, re.I)
        if match:
            return text[match.end():].strip()
    return None


def extract_group_quote_request(content: str, mention_names: list[str]) -> tuple[str | None, bool]:
    """Extract @ request and image-reference flag from WeChat quote cards."""
    raw = html_lib.unescape(str(content or ""))
    title_match = re.search(r"<title>\s*(.*?)\s*</title>", raw, re.S | re.I)
    if not title_match:
        return None, False
    title = re.sub(r"\s+", " ", title_match.group(1)).strip()
    request = extract_group_mention(title, mention_names)
    if request is None:
        return None, False
    return request, bool(re.search(r"<refermsg>.*?<type>\s*3\s*</type>.*?</refermsg>", raw, re.S | re.I))


def extract_group_quote_text(content: str) -> str | None:
    """Extract the plain text carried by a WeChat group quote card."""
    raw = html_lib.unescape(str(content or ""))
    match = re.search(r"<refermsg\b.*?<content>(.*?)</content>", raw, re.S | re.I)
    if not match:
        return None
    quoted = html_lib.unescape(match.group(1))
    quoted = re.sub(r"<[^>]+>", " ", quoted)
    quoted = re.sub(r"^\s*[^:\n]+:\s*", "", quoted, count=1)
    quoted = re.sub(r"\s+", " ", quoted).strip()
    return quoted[:4000] or None


def is_group_quote_comment_request(request: str) -> bool:
    text = re.sub(r"\s+", "", str(request or "")).lower()
    return any(word in text for word in ("评价一下", "评论一下", "点评一下", "评价"))


def is_group_quote_search_request(request: str) -> bool:
    text = re.sub(r"\s+", "", str(request or "")).lower()
    return any(word in text for word in (
        "这是真的吗", "是真的吗", "核实一下", "核实", "查证一下", "查证",
        "验证一下", "验证", "是真是假", "求证"))


GROUP_VISUAL_KEYWORDS = ("识别图片", "识别图像", "看看图片", "看图", "识别表情包",
                         "识别表情", "识别gif", "识别gif图", "看看gif", "分析图片")
GROUP_VISUAL_WINDOW_MS = 3000


def is_group_visual_request(request: str) -> bool:
    text = re.sub(r"\s+", "", str(request or "").lower())
    return any(keyword in text for keyword in GROUP_VISUAL_KEYWORDS) or "gif" in text


def is_pure_visual_request(request: str) -> bool:
    text = re.sub(r"[\s。！？!?，,、]+", "", str(request or "")).lower()
    return text in {"识别图片", "识别图像", "识别一下", "这是什么", "看看图片", "看看图", "分析图片"}


def is_group_visual_history_request(request: str) -> bool:
    """Match requests referring to an image already sent in the group."""
    text = re.sub(r"\s+", "", str(request or "").lower())
    markers = ("上面这张图片", "上面的图片", "上面那张图", "刚才的图片",
               "刚才那张图", "前面的图片", "之前的图片", "这张图片", "这张图",
               "这是什么", "评论一下", "评价一下", "分析一下")
    visual_intent = (is_group_visual_request(text + "识别图片") or
                     any(word in text for word in ("引用", "图片", "图", "表情", "gif")))
    return any(marker in text for marker in markers) and visual_intent


def compact_visual_reply(text: str) -> str:
    """Keep visual replies short, plain, and readable in WeChat."""
    clean = str(text or "").replace("\r", " ").replace("\n", " ")
    clean = re.sub(r"```[^`]*```", "", clean, flags=re.S)
    clean = clean.replace("**", "").replace("__", "")
    clean = re.sub(r"（[^（）]{0,24}(?:动作|尾巴|摇|晃|识别流程)[^（）]{0,24}）", "", clean)
    clean = re.sub(r"^\s*[-*+#>]\s*", "", clean)
    clean = re.sub(r"([，。！？、；：,.!?])\1+", r"\1", clean)
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean


def extract_quoted_message_text(content: str) -> tuple[str, str] | None:
    """Return (user_text, quoted_text) for WeChat's quote message format."""
    match = re.search(r"^(.*?)\s*\n引用\s+(.+?)\s+的消息\s*:\s*(.*)$",
                      str(content or ""), re.S)
    if not match:
        return None
    return match.group(1).strip(), match.group(3).strip()


def is_quoted_visual_search(content: str) -> bool:
    quoted = extract_quoted_message_text(content)
    if not quoted:
        return False
    text = re.sub(r"\s+", "", quoted[1].lower())
    intent = re.sub(r"\s+", "", quoted[0].lower())
    return ("图片" in text or "图像" in text or "表情" in text or "gif" in text
            or "这是什么" in intent or "识别" in intent or "评论" in intent)


def is_quoted_visual_request(content: str) -> bool:
    """Detect WeChat quote cards that point at an image or animated sticker."""
    raw = html_lib.unescape(str(content or ""))
    if re.search(r"<refermsg>.*?<type>\s*(?:3|47|49|62)\s*</type>.*?</refermsg>",
                 raw, re.S | re.I):
        text = re.sub(r"\s+", "", raw.lower())
        return any(word in text for word in ("这是什么", "识别", "评论", "分析", "图片", "图像", "表情", "gif"))
    return is_quoted_visual_search(content)


def nearby_visual_messages(history: list[dict], request: dict, radius: int = 2) -> list[dict]:
    """Find visual messages in the two-message neighborhood of a request."""
    if not history:
        return []
    ordered = sorted(history, key=lambda item: int(item.get("sort_seq") or 0))
    try:
        index = next(i for i, item in enumerate(ordered)
                     if int(item.get("local_id") or 0) == int(request.get("local_id") or 0))
    except StopIteration:
        index = len(ordered)
    sender = str(request.get("sender_username") or request.get("sender_id") or "")
    # WeChat quote cards embed the original image XML. Match its md5 first so
    # an older nearby image cannot be mistaken for the quoted one.
    raw = html_lib.unescape(str(request.get("content") or ""))
    quoted_md5 = next((value.lower() for value in re.findall(
        r"(?:<refermsg>.*?){0,1}\bmd5\s*=\s*[\"']([0-9a-f]{32})[\"']", raw,
        re.S | re.I)), "")
    if quoted_md5:
        exact = []
        for item in ordered:
            if not is_visual_message(item):
                continue
            item_md5 = re.search(r"\bmd5\s*=\s*[\"']([0-9a-f]{32})[\"']",
                                 str(item.get("content") or ""), re.I)
            if item_md5 and item_md5.group(1).lower() == quoted_md5:
                exact.append(item)
        if exact:
            return [exact[-1]]
    rows = []
    for distance in range(1, radius + 1):
        for position in (index - distance, index + distance):
            if position < 0 or position >= len(ordered):
                continue
            item = ordered[position]
            if not is_visual_message(item):
                continue
            if quoted_md5:
                item_md5 = re.search(r"\bmd5\s*=\s*[\"']([0-9a-f]{32})[\"']",
                                     str(item.get("content") or ""), re.I)
                if item_md5 and item_md5.group(1).lower() == quoted_md5:
                    rows.append((-1, 0, 0, item))
                    continue
            item_sender = str(item.get("sender_username") or item.get("sender_id") or "")
            rows.append((0 if sender and sender == item_sender else 1, distance,
                         0 if position < index else 1, item))
    rows.sort(key=lambda row: row[:3])
    return [row[3] for row in rows]


def summarize_quoted_visual_reply(db, config: dict, peer: str, chat: dict,
                                  msg: dict, request: str) -> str:
    """Recognize the visual message referred to by a private quote card."""
    current_seq = int(msg.get("sort_seq") or 0)
    history = db.get_messages(peer, limit=50)
    candidates = nearby_visual_messages(history, msg)
    if not candidates:
        candidates = [item for item in history
                      if int(item.get("sort_seq") or 0) < current_seq and is_visual_message(item)]
    if not candidates:
        return "引用图片未能从最近50条消息中找到，暂时无法识别。"
    source = candidates[-1]
    image_path = vision_path = None
    try:
        image_temp = RUNTIME_DIR / "vision_tmp"
        image_path = extract_visual(db, peer, int(source["local_id"]), source, image_temp,
                                    hook_url=str(config.get("hook_url") or ""))
        vision_path = prepare_vision_image(image_path, image_temp)
        label = "动画表情" if is_emoji_message(source) else "图片"
        description = describe_image(
            vision_path,
            f"请客观描述这个{label}的外形、颜色、结构和清晰文字，判断主体时不要猜测；"
            "无法确认就明确说明。只输出简洁事实。",
            config, model_alias=chat.get("model", "qwen"),
        )
        prompt = prepare_model_prompt(
            f"用户引用了一条{label}并提问：{request}\n视觉识别结果：{description}\n"
            "请用一句自然、通顺的中文回答，最多60个中文字符。不要使用Markdown、连续标点或动作描写。"
        )
        return compact_visual_reply(ask_openclaw(
            config, peer, chat.get("model", "qwen"), chat.get("thinking", "off"), prompt,
            epoch=int(chat.get("session_epoch", 0))))
    except (ImageUnavailable, VisionError, OSError, KeyError):
        LOG.exception("quoted visual reply failed peer=%s seq=%s", peer, msg.get("sort_seq"))
        return "引用图片已找到，但暂时无法识别。"
    finally:
        for path in {path for path in (vision_path, image_path) if path is not None}:
            try:
                cleanup_image(path, RUNTIME_DIR / "vision_tmp")
            except (OSError, ValueError):
                LOG.exception("quoted visual reply cleanup failed path=%s", path)


def summarize_quoted_visual_search(db, config: dict, peer: str, chat: dict,
                                   msg: dict, request: str) -> str:
    """Recognize the latest quoted image, then search its visual content."""
    current_seq = int(msg.get("sort_seq") or 0)
    history = db.get_messages(peer, limit=50)
    candidates = nearby_visual_messages(history, msg)
    if not candidates:
        candidates = [item for item in history
                      if int(item.get("sort_seq") or 0) < current_seq
                      and is_visual_message(item)]
    if not candidates:
        return "引用图片未能从最近50条消息中找到，无法进行图片联网搜索。"
    source = candidates[-1]
    image_path = vision_path = None
    try:
        image_temp = RUNTIME_DIR / "vision_tmp"
        image_path = extract_visual(
            db, peer, int(source["local_id"]), source, image_temp,
            hook_url=str(config.get("hook_url") or ""),
        )
        vision_path = prepare_vision_image(image_path, image_temp)
        description = describe_image(
            vision_path,
            "先客观描述可见的外形、颜色、结构和文字，再提取适合联网搜索的关键信息："
            "人物、地点、物品、品牌、事件、画面文字。不要仅凭模糊轮廓猜测；无法确认时写‘无法确认’。"
            "只输出简洁事实，不要写动作描写。",
            config,
            model_alias=chat.get("model", "qwen"),
        )
        query = f"{request}\n图片识别出的搜索线索：{description}"
        try:
            image_evidence = search_image_evidence(config, description)
        except Exception:
            LOG.exception("image search failed peer=%s seq=%s", peer, msg.get("sort_seq"))
            image_evidence = "（图片搜索没有返回结果）"
        return summarize_search(config, peer, chat, query, image_evidence)
    except (ImageUnavailable, VisionError, OSError, KeyError):
        LOG.exception("quoted visual search failed peer=%s seq=%s", peer, msg.get("sort_seq"))
        return "引用图片已找到，但图片识别未完成，暂时无法联网搜索。"
    finally:
        for path in {path for path in (vision_path, image_path) if path is not None}:
            try:
                cleanup_image(path, RUNTIME_DIR / "vision_tmp")
            except (OSError, ValueError):
                LOG.exception("quoted visual search cleanup failed path=%s", path)


def make_group_visual_pending(sender: str, sort_seq: int, request: str) -> dict:
    return {"sender": str(sender or ""), "sort_seq": int(sort_seq),
            "request": str(request or "")}


def match_group_visual_pending(pending: dict | None, msg: dict) -> str | None:
    if not pending or not is_visual_message(msg):
        return None
    sender = str(msg.get("sender_username") or msg.get("sender_id") or "")
    if sender != str(pending.get("sender") or ""):
        return None
    delta = int(msg.get("sort_seq") or 0) - int(pending.get("sort_seq") or 0)
    if 0 <= delta <= GROUP_VISUAL_WINDOW_MS:
        return str(pending.get("request") or "")
    return None


def resolve_group_sender_name(db, group: str, sender_wxid: str) -> str:
    """Resolve a group member's display name for a real @ mention."""
    sender_wxid = str(sender_wxid or "").strip()
    if not sender_wxid:
        return ""
    try:
        for member in db.get_group_members(group):
            if str(member.get("username") or "") != sender_wxid:
                continue
            return str(member.get("remark") or member.get("nick_name") or sender_wxid)
    except Exception:
        LOG.debug("group member lookup failed group=%s sender=%s", group, sender_wxid,
                  exc_info=True)
    try:
        return str(db.get_nickname(sender_wxid) or sender_wxid)
    except Exception:
        return sender_wxid


class GroupTaskDispatcher:
    """Run one FIFO worker per group so model latency never blocks polling."""

    def __init__(self, handler):
        self.handler = handler
        self._queues: dict[str, queue.Queue] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._lock = threading.Lock()
        self._closed = False

    def enqueue(self, group: str, task) -> None:
        with self._lock:
            if self._closed:
                raise RuntimeError("group dispatcher is shut down")
            work_queue = self._queues.get(group)
            if work_queue is None:
                work_queue = queue.Queue()
                self._queues[group] = work_queue
                worker = threading.Thread(
                    target=self._run, args=(group, work_queue),
                    name=f"wechat-group-{hashlib.sha256(group.encode()).hexdigest()[:8]}",
                    daemon=True,
                )
                self._threads[group] = worker
                worker.start()
            work_queue.put(task)

    def _run(self, group: str, work_queue: queue.Queue) -> None:
        while True:
            task = work_queue.get()
            try:
                if task is None:
                    return
                self.handler(task)
            except Exception:
                LOG.exception("unhandled group worker error group=%s", group)
            finally:
                work_queue.task_done()

    def shutdown(self, timeout: float = 2.0) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            items = list(self._queues.items())
            threads = list(self._threads.values())
        for _, work_queue in items:
            work_queue.put(None)
        deadline = time.monotonic() + timeout
        for worker in threads:
            worker.join(max(0.0, deadline - time.monotonic()))


def is_group_summary_request(request: str) -> bool:
    """Enable group history only for explicit summary wording."""
    text = re.sub(r"\s+", "", str(request or "").strip())
    return bool(re.search(r"总结.{0,8}对话", text))


def trim_transcript_lines(lines: list[str], max_chars: int = GROUP_SUMMARY_MAX_CHARS) -> str:
    """Keep the newest transcript lines that fit the command-line budget."""
    kept: list[str] = []
    total = 0
    for line in reversed(lines):
        cost = len(line) + 1
        if kept and total + cost > max_chars:
            break
        kept.append(line)
        total += cost
    kept.reverse()
    if len(kept) < len(lines):
        kept.insert(0, GROUP_SUMMARY_OMITTED_MARK)
    return "\n".join(kept)


def group_summary_prompt(history: list[dict], request: str, own_sender_id: int = 1) -> str:
    lines = []
    for item in history[-GROUP_SUMMARY_HISTORY_LIMIT:]:
        # Bot messages are delivery artifacts, not group discussion content.
        if int(item.get("sender_id") or 0) in (0, own_sender_id):
            continue
        body = str(item.get("content") or "").strip()
        if not body or body.startswith("["):
            continue
        sender = str(item.get("sender_username") or "群成员")
        lines.append(f"{sender}：{body}")
    transcript = trim_transcript_lines(lines) or "（最近没有可用的文字消息）"
    return ("请根据下面这段微信群最近消息回答用户请求。只使用提供的群消息，"
            "不要声称读取了未提供的内容；如果消息不足，请明确说明。\n"
            f"用户请求：{request or '请简要总结最近群聊'}\n\n"
            f"最近消息（最多{GROUP_SUMMARY_HISTORY_LIMIT}条）：\n{transcript}")


def should_notify_group_failure(peer: str, now: float | None = None,
                                cooldown: float = GROUP_FAILURE_COOLDOWN_SECONDS) -> bool:
    """Rate-limit the visible failure notice so one outage cannot spam a group."""
    instant = time.monotonic() if now is None else now
    with GROUP_FAILURE_LOCK:
        previous = GROUP_FAILURE_LAST.get(peer)
        if previous is not None and instant - previous < cooldown:
            return False
        GROUP_FAILURE_LAST[peer] = instant
        return True


def classify_message(msg: dict, own_sender_id: int):
    if int(msg.get("sender_id") or 0) in (0, own_sender_id):
        return None
    if msg.get("type") != "文本":
        return None
    content = str(msg.get("content") or "").strip()
    if not content:
        return None
    command = content.lower()
    if command.startswith("/search"):
        query = content[7:].strip()
        return ("search", query) if query else ("search", "")
    if command in ("/qwen", "/deepseek"):
        return ("model", command[1:])
    if command == "/fast":
        return ("thinking", "off")
    if command == "/think":
        return ("thinking", "high")
    if command == "/help":
        return ("help", "")
    if command == "/compact":
        return ("compact", "")
    if command == "/reset":
        return ("reset", "")
    if command == "/clear":
        return ("clear", "")
    return ("prompt", content)


def search_web(query: str, limit: int = 5, include_links: bool = True) -> str:
    """Perform a small, controlled Bing HTML search for /search."""
    query = str(query or "").strip()
    if not query:
        return "用法：/search 关键词"
    url = "https://www.bing.com/search?" + urllib.parse.urlencode({"format": "rss", "q": query})
    request = urllib.request.Request(url, headers={"User-Agent": "OpenClaw-WeChatBot/0.2"})
    try:
        with urllib.request.urlopen(request, timeout=12) as response:
            html = response.read().decode("utf-8", errors="replace")
    except Exception:
        LOG.warning("web search failed query=%s", query, exc_info=True)
        return "联网搜索暂时不可用，请稍后再试。"
    results = []
    titles = []
    snippets = []
    try:
        root = ET.fromstring(html)
        for item in root.findall(".//item"):
            titles.append((item.findtext("link") or "", item.findtext("title") or ""))
            snippets.append(item.findtext("description") or "")
    except ET.ParseError:
        LOG.warning("Bing RSS parse failed", exc_info=True)
    for index, (href, title) in enumerate(titles):
        snippet = snippets[index] if index < len(snippets) else ""
        clean = lambda value: html_lib.unescape(re.sub(r"<[^>]+>", "", value)).strip()
        href = html_lib.unescape(href)
        if href.startswith("//"):
            href = "https:" + href
        entry = f"{len(results)+1}. {clean(title)}\n"
        if include_links:
            entry += f"{href}\n"
        entry += clean(snippet)
        results.append(entry)
        if len(results) >= limit:
            break
    if results:
        return "搜索结果：\n\n" + "\n\n".join(results)
    fallback = "https://www.google.com/search?" + urllib.parse.urlencode({"q": query})
    return f"暂时未能解析搜索摘要，你可以打开这个搜索链接查看：\n{fallback}"


def search_requests_links(query: str) -> bool:
    text = re.sub(r"\s+", "", str(query or "").lower())
    return any(mark in text for mark in ("标明链接", "附链接", "给链接", "提供链接", "来源链接", "网址"))


def polish_search_reply(text: str) -> str:
    """Make search synthesis readable in WeChat plain text."""
    clean = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    # Markdown emphasis and code fences render poorly in the WeChat sender.
    clean = re.sub(r"```(?:text|markdown|纯文本)?", "", clean, flags=re.I)
    clean = clean.replace("```", "").replace("**", "").replace("__", "")
    clean = re.sub(r"^\s*#{1,6}\s*", "", clean, flags=re.M)
    clean = re.sub(r"^\s*[-*+]\s+", "• ", clean, flags=re.M)
    clean = re.sub(r"[ \t]+", " ", clean)
    clean = re.sub(r"\n{3,}", "\n\n", clean)
    return clean.strip()


def search_image_evidence(config: dict, query: str) -> str:
    """Fetch compact image-search results from local SearXNG."""
    endpoint = str(config.get("searxng_url", "http://127.0.0.1:8888")).rstrip("/")
    url = endpoint + "/search?" + urllib.parse.urlencode({
        "q": query, "format": "json", "language": "zh-CN", "categories": "images"})
    request = urllib.request.Request(url, headers={"User-Agent": "OpenClaw-WeChatBot/0.3"})
    with urllib.request.urlopen(request, timeout=8) as response:
        payload = json.loads(response.read().decode("utf-8", errors="replace"))
    rows = []
    for item in (payload.get("results") or [])[:4]:
        title = re.sub(r"\s+", " ", str(item.get("title") or "")).strip()
        source = str(item.get("source") or item.get("url") or "").strip()
        if title:
            rows.append(f"图片结果：{title}；来源：{source}")
    return "\n".join(rows)


def summarize_search(config: dict, peer: str, chat: dict, query: str,
                     extra_evidence: str = "") -> str:
    include_links = search_requests_links(query)
    # Fetch a small, bounded result set locally from Docker SearXNG first.  Passing
    # raw web-search tool output to the agent can consume its output budget before
    # the final answer is produced.
    try:
        endpoint = str(config.get("searxng_url", "http://127.0.0.1:8888")).rstrip("/")
        url = endpoint + "/search?" + urllib.parse.urlencode(
            {"q": query, "format": "json", "language": "zh-CN",
             "categories": "news,general"})
        request = urllib.request.Request(url, headers={"User-Agent": "OpenClaw-WeChatBot/0.2"})
        with urllib.request.urlopen(request, timeout=8) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
        rows = []
        raw_results = list(payload.get("results") or [])
        # Prefer dated news items so current events do not get displaced by old
        # evergreen pages. Keep undated results afterward as supporting sources.
        def result_date(item):
            value = str(item.get("publishedDate") or item.get("published_date") or "")
            match = re.search(r"(20\d{2})[/-](\d{1,2})[/-](\d{1,2})", value)
            return tuple(map(int, match.groups())) if match else (0, 0, 0)
        raw_results.sort(key=result_date, reverse=True)
        for item in raw_results[:6]:
            title = str(item.get("title") or "").strip()
            content = re.sub(r"\s+", " ", str(item.get("content") or "")).strip()
            href = str(item.get("url") or "").strip()
            published = str(item.get("publishedDate") or item.get("published_date") or "").strip()
            date_line = f"\n发布日期：{published}" if published else ""
            if title or content:
                rows.append(f"标题：{title}{date_line}\n摘要：{content[:500]}" +
                            (f"\n来源：{href}" if include_links and href else ""))
        evidence = "\n\n".join(rows) or "（搜索没有返回可用摘要）"
        if extra_evidence:
            evidence += "\n\n" + extra_evidence
    except Exception:
        LOG.exception("SearXNG search failed peer=%s query=%s", peer, query)
        evidence = "（本地搜索暂时没有返回可用摘要）"
    instruction = (
        "请根据下面提供的本地联网搜索结果，用中文归纳回答。"
        "优先采用带有最新日期、且多个来源一致的结果；如果搜索摘要已经明确给出答案，"
        "直接给出该答案，不要回复‘不知道’或‘尚未确定’。只有来源互相矛盾时才说明不确定。"
        "不要逐条复述，使用自然、通顺的中文纯文本，不要使用 Markdown 加粗、标题、表格或代码块，"
        "不要堆砌符号；如果需要列举，用简短分行表达。"
        "不要编造搜索结果中没有的信息。只保留最重要的 3～5 条结论，"
        "总长度控制在 500 个汉字以内，先给结论再补充必要背景，避免长篇分析，"
        "确保在一次回复中完整结束，不要输出‘未完待续’或截断提示。"
    )
    if include_links:
        instruction += "用户明确要求链接，请在相关要点后保留来源链接。"
    else:
        instruction += "用户没有要求链接，禁止输出网址、Markdown 链接或链接列表。"
    prompt = prepare_model_prompt(f"用户搜索：{query}\n\n{instruction}\n\n搜索结果：\n{evidence}")
    try:
        return polish_search_reply(sanitize_reply(ask_openclaw(
            # Use a short-lived search session so a long personal conversation
            # cannot slow down or exhaust the search summarization context.
            config, peer + ":search:" + hashlib.sha256(query.encode("utf-8")).hexdigest()[:10],
            chat.get("model", config["initial_model"]),
            chat.get("thinking", "off"), prompt,
            epoch=int(chat.get("session_epoch", 0)),
        )))
    except Exception:
        LOG.exception("search summarization failed peer=%s query=%s", peer, query)
        return "搜索结果已获取，但归纳失败，请稍后重试。"


def classify_incoming_message(msg: dict, own_sender_id: int):
    if int(msg.get("sender_id") or 0) == own_sender_id:
        return None
    if is_visual_message(msg):
        return ("image", "emoji" if is_emoji_message(msg) else "image")
    return classify_message(msg, own_sender_id)


def apply_thinking_mode(chat: dict, thinking_level: str) -> str:
    if chat.get("model") != "qwen":
        return "请先使用 /qwen 切换到 Qwen。"
    chat["thinking"] = thinking_level
    return "已切换到思考模式。" if thinking_level == "high" else "已切换到快速模式。"


def compact_session_reply(config: dict, peer: str, chat: dict) -> str:
    """Handle /compact: reclaim this chat's OpenClaw context budget."""
    try:
        info = compact_openclaw_session(config, peer, int(chat.get("session_epoch", 0)))
    except Exception:
        LOG.exception("session compaction failed peer=%s", peer)
        return "上下文压缩失败，请稍后再试。"
    if not info.get("compacted"):
        return "当前会话上下文已经很紧凑，无需压缩。"
    return (f"上下文已压缩：{info.get('tokensBefore')} → "
            f"{info.get('tokensAfter')} tokens。")


def reset_session_reply(chat: dict) -> str:
    """Handle /reset: start a brand new OpenClaw session for this chat."""
    chat["session_epoch"] = int(chat.get("session_epoch", 0)) + 1
    return "已开启新会话，之前的上下文不再带入。"


def clear_chat_memory(state: dict, peer: str, chat: dict) -> str:
    """Forget only this private chat or group and start a fresh session epoch."""
    chat["session_epoch"] = int(chat.get("session_epoch", 0)) + 1
    chat.pop("daily_help_date", None)
    pending = state.get("group_visual_pending")
    if isinstance(pending, dict):
        pending.pop(peer, None)
    return "当前对话记忆已清除，可以重新开始。"


def split_humanized_reply(text: str, max_parts: int = 3) -> list[str]:
    clean_text = str(text or "").strip()
    if not clean_text:
        return [""]
    end_marks = set("。！？!?；;")
    closing_marks = set("”’」』】）》〕〉》)]}%")
    chunks, start, index = [], 0, 0
    while index < len(clean_text):
        char = clean_text[index]
        if char in end_marks:
            boundary = index + 1
            while boundary < len(clean_text) and clean_text[boundary] in closing_marks:
                boundary += 1
            chunk = clean_text[start:boundary].strip()
            if chunk:
                chunks.append(chunk)
            index = boundary
            while index < len(clean_text) and clean_text[index].isspace():
                index += 1
            start = index
            continue
        if char == "\n":
            chunk = clean_text[start:index].strip()
            if chunk:
                chunks.append(chunk)
            index += 1
            while index < len(clean_text) and clean_text[index].isspace():
                index += 1
            start = index
            continue
        index += 1
    tail = clean_text[start:].strip()
    if tail:
        chunks.append(tail)
    if len(chunks) <= max_parts:
        return chunks
    return ["".join(chunks[i * len(chunks) // max_parts:(i + 1) * len(chunks) // max_parts])
            for i in range(max_parts)]


def extract_reply(data: dict) -> str:
    if data.get("status") != "ok":
        raise ValueError("OpenClaw agent did not complete successfully")
    parts = [str(item.get("text") or "").strip() for item in data.get("result", {}).get("payloads", [])]
    reply = "\n\n".join(part for part in parts if part)
    if not reply:
        raise ValueError("OpenClaw agent returned an empty reply")
    return reply


def format_timed_reply(text: str, elapsed_seconds: float, model_alias: str,
                       thinking_level: str) -> str:
    if model_alias == "qwen":
        mode = "思考模式" if thinking_level == "high" else "快速模式"
    else:
        mode = "DeepSeek"
    clean_text = text.rstrip()
    output_characters = len(clean_text.replace("\r", "").replace("\n", ""))
    return (f"{clean_text}\n\n（{mode}｜输出文字：{output_characters} 字｜"
            f"耗时 {elapsed_seconds:.1f} 秒）")


def is_short_casual_prompt(prompt: str) -> bool:
    """Identify brief greetings/acknowledgements that should stay one message."""
    clean = str(prompt or "").strip()
    if not clean or len(clean) > 16 or "\n" in clean or "\r" in clean:
        return False
    if any(mark in clean for mark in "？?"): 
        return False
    task_markers = (
        "怎么", "为什么", "如何", "帮我", "分析", "解释", "比较", "配置", "修复",
        "报错", "步骤", "代码", "设置", "检查", "优化", "能不能", "可以吗", "介绍",
        "告诉", "多少", "是否", "请问", "总结", "生成", "写一个", "做一个", "切换",
        "增加", "移除", "搜索", "检索", "连接", "启动", "关闭",
        "什么", "谁", "哪",
    )
    return not any(marker in clean for marker in task_markers)


def prepare_model_prompt(prompt: str) -> str:
    """Add intent and length guidance to every prompt, with stricter rules for short chat."""
    clean = str(prompt or "").strip()
    instruction = CONVERSATION_STYLE_INSTRUCTION + USER_MEMORY_ISOLATION_INSTRUCTION
    if is_short_casual_prompt(clean):
        instruction += SHORT_CHAT_INSTRUCTION
    return clean + instruction


def sanitize_reply(text: str) -> str:
    """Remove leaked OpenClaw workspace/session context before sending to WeChat."""
    clean = str(text or "").strip()
    clean = MODEL_TRUNCATION_NOTICE.sub("", clean).strip()
    match = INTERNAL_CONTEXT_MARKER.search(clean)
    if match:
        clean = clean[:match.start()].rstrip()
        clean = re.sub(r"(?:[-\s]+)$", "", clean).rstrip()
    return clean or INTERNAL_CONTEXT_FALLBACK


def compact_reply_text(text: str, max_chars: int = 60, max_sentences: int = 2) -> str:
    """Keep compact-chat output within a deterministic sentence and character limit."""
    clean = str(text or "").strip()
    end_marks = set("。！？!?；;")
    closing_marks = set("”’」』】）》〕〉》)]}%")
    sentence_count = 0
    index = 0
    while index < len(clean):
        if clean[index] in end_marks:
            boundary = index + 1
            while boundary < len(clean) and clean[boundary] in closing_marks:
                boundary += 1
            sentence_count += 1
            if sentence_count >= max_sentences:
                clean = clean[:boundary].strip()
                break
            index = boundary
            continue
        index += 1
    if len(clean) > max_chars:
        clean = clean[:max_chars - 1].rstrip() + "…"
    return clean


def prepare_reply_parts(text: str, elapsed_seconds: float, model_alias: str,
                        thinking_level: str, humanized: bool,
                        compact: bool = False) -> list[str]:
    clean_text = text.rstrip()
    if compact:
        clean_text = compact_reply_text(clean_text)
    formatted = format_timed_reply(clean_text, elapsed_seconds, model_alias, thinking_level)
    if not humanized or compact:
        return [formatted]
    chunks = split_humanized_reply(clean_text)
    if len(chunks) <= 1:
        return [formatted]
    footer = formatted[len(clean_text):]
    chunks[-1] += footer
    return chunks


def was_sent(records: list[dict], baseline: int, own_sender_id: int, content: str) -> bool:
    return any(
        int(item.get("sort_seq") or 0) > baseline
        and int(item.get("sender_id") or 0) == own_sender_id
        and item.get("content") == content
        for item in records
    )


def plan_offline_batch(messages: list[dict], last_seq: int, own_sender_id: int) -> tuple[int, bool]:
    """Advance an offline watermark without inspecting message content."""
    newest = last_seq
    has_incoming = False
    for msg in messages:
        seq = int(msg.get("sort_seq") or 0)
        if seq <= last_seq:
            continue
        newest = max(newest, seq)
        sender_id = int(msg.get("sender_id") or 0)
        if sender_id not in (0, own_sender_id):
            has_incoming = True
    return newest, has_incoming


def resolve_search_name(db, peer: str) -> str:
    """Use a locally known display name only when it identifies one contact."""
    name = db.get_nickname(peer)
    if not name or name == peer:
        return peer
    exact = {
        hit["username"] for hit in db.search_contact(name)
        if name in (hit.get("remark"), hit.get("nick_name"))
    }
    return name if exact == {peer} else peer


def select_ai_window(windows: list[dict], ai_user: str, identify_process) -> int:
    identified = [(window, identify_process(window["pid"])) for window in windows]
    matches = [window["hwnd"] for window, account in identified if account == ai_user]
    if len(matches) != 1:
        if len(windows) == 1 and identified[0][1] is None:
            LOG.warning(
                "using the only visible WeChat window because process account identification is unavailable"
            )
            return windows[0]["hwnd"]
        raise RuntimeError(f"Expected one visible window for AI account, found {len(matches)}; keeping messages pending")
    return matches[0]


def find_ai_window(ai_user: str) -> int:
    from wechatauto.db import extract_master_key_from_cfg
    from wechatauto.demo_forward import find_main_windows

    def identify(pid: int) -> str | None:
        details = extract_master_key_from_cfg(pid)
        return details[2] if details else None

    return select_ai_window(find_main_windows(), ai_user, identify)


def resolve_db_dir(config: dict, detect_db_dir=None) -> str | None:
    """Resolve the WeChat database per Windows user when config requests auto mode."""
    configured = config.get("db_dir")
    if configured and str(configured).strip().lower() not in {"auto", "detect"}:
        return str(configured)
    if detect_db_dir is None:
        from wechatauto.db import auto_detect_db_dir
        detect_db_dir = auto_detect_db_dir
    detected = detect_db_dir()
    if not detected:
        raise RuntimeError("Could not auto-detect the WeChat database directory for this Windows user")
    LOG.info("using auto-detected WeChat database directory: %s", detected)
    return detected


def load_state(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def save_state(path: Path, state: dict) -> None:
    with STATE_SAVE_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp, path)


def session_key(config: dict, peer: str, epoch: int = 0) -> str:
    seed = peer if not epoch else f"{peer}#{epoch}"
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]
    return f"{config['session_key_prefix']}:{digest}"


def openclaw_session_key(config: dict, peer: str, epoch: int = 0) -> str:
    """Full gateway session key, as used by ``openclaw sessions`` commands."""
    return f"agent:{config['agent']}:{session_key(config, peer, epoch)}"


def compact_openclaw_session(config: dict, peer: str, epoch: int = 0,
                             timeout: float = 900.0) -> dict:
    """Ask the gateway to compact one session so its context fits the model budget.

    A session that grows past the gateway's prompt budget makes every turn run a
    compaction first; when that compaction hits its short per-turn deadline the turn
    quietly costs minutes. Running the same compaction out of band succeeds in
    seconds (measured: 26944 -> 10400 tokens in 4.2 s).
    """
    command = [
        "wsl.exe", "-d", "OpenClawGateway", "-u", "openclaw", "--",
        "openclaw", "sessions", "compact", openclaw_session_key(config, peer, epoch),
        "--agent", config["agent"], "--json", "--timeout", str(int(timeout * 1000)),
    ]
    result = subprocess.run(command, text=True, capture_output=True, encoding="utf-8",
                            timeout=timeout + 30, **openclaw_run_options())
    if result.returncode:
        raise RuntimeError(
            f"OpenClaw compaction exited {result.returncode}: {result.stderr[-200:]}")
    data = json.loads(result.stdout)
    if not data.get("ok"):
        raise RuntimeError("OpenClaw compaction did not report ok")
    return data.get("result") or {}


def should_compact_after_turn(elapsed_seconds: float,
                              threshold: float = COMPACT_AFTER_SECONDS) -> bool:
    """A pathologically slow turn means the session outgrew its context budget."""
    return elapsed_seconds >= threshold


def should_compact_before_turn(total_tokens: int, context_tokens: int,
                               ratio: float = 0.70) -> bool:
    """Return whether a session is close enough to its context limit to compact."""
    return total_tokens > 0 and context_tokens > 0 and total_tokens >= context_tokens * ratio


def get_openclaw_session_usage(config: dict, peer: str, epoch: int = 0,
                               timeout: float = 5.0) -> dict | None:
    """Read the gateway's token counters for one session without failing a turn."""
    command = [
        "wsl.exe", "-d", "OpenClawGateway", "-u", "openclaw", "--",
        "openclaw", "sessions", "--json", "--agent", config["agent"],
        "--limit", "all",
    ]
    try:
        result = subprocess.run(command, text=True, capture_output=True,
                                encoding="utf-8", timeout=timeout,
                                **openclaw_run_options())
        if result.returncode:
            return None
        sessions = json.loads(result.stdout).get("sessions", [])
        key = openclaw_session_key(config, peer, epoch)
        return next((item for item in sessions if item.get("key") == key), None)
    except (OSError, subprocess.SubprocessError, ValueError, TypeError):
        LOG.debug("session usage lookup failed peer=%s", peer, exc_info=True)
        return None


def maybe_auto_compact_before_turn(config: dict, peer: str, chat: dict) -> None:
    """Compact a session before inference when its token budget is nearly full."""
    usage = get_openclaw_session_usage(config, peer, int(chat.get("session_epoch", 0)))
    if not usage:
        return
    context_tokens = int(usage.get("contextTokens") or config.get("qwen_context_tokens", 32768))
    total_tokens = int(usage.get("totalTokens") or 0)
    ratio = float(config.get("auto_compact_ratio", 0.70))
    if not should_compact_before_turn(total_tokens, context_tokens, ratio):
        return
    try:
        info = compact_openclaw_session(config, peer, int(chat.get("session_epoch", 0)))
        LOG.info("pre-turn automatic compaction peer=%s tokens=%s->%s",
                 peer, info.get("tokensBefore", total_tokens), info.get("tokensAfter"))
    except Exception:
        LOG.warning("pre-turn automatic compaction failed peer=%s", peer, exc_info=True)


def maybe_auto_compact(config: dict, peer: str, chat: dict,
                       elapsed_seconds: float,
                       threshold: float = COMPACT_AFTER_SECONDS) -> None:
    """Reclaim context in the background so the next turn is fast again."""
    if not should_compact_after_turn(elapsed_seconds, threshold):
        return
    try:
        info = compact_openclaw_session(config, peer, int(chat.get("session_epoch", 0)))
    except Exception:
        LOG.warning("automatic session compaction failed peer=%s", peer, exc_info=True)
        return
    if info.get("compacted"):
        LOG.info("automatic session compaction peer=%s tokens=%s->%s",
                 peer, info.get("tokensBefore"), info.get("tokensAfter"))


def build_openclaw_command(config: dict, peer: str, model_alias: str,
                           thinking_level: str, prompt: str, epoch: int = 0) -> list[str]:
    command = [
        "wsl.exe", "-d", "OpenClawGateway", "--", "openclaw", "agent",
        "--agent", config["agent"],
        "--session-key", session_key(config, peer, epoch),
        "--model", config["models"][model_alias],
    ]
    if model_alias == "qwen":
        command.extend(["--thinking", thinking_level])
    command.extend(["--message", prompt, "--json", "--timeout", "180"])
    return command


def openclaw_run_options() -> dict:
    """Return windowless-spawn options for every child process the bridge starts.

    ``wsl.exe`` (OpenClaw) and the ``cmd.exe``/``powershell.exe`` probes used while
    locating the WeChat database are console applications. Spawned from this
    console-less bridge they would each be given a fresh console, which is exactly
    the black window that flashes on the desktop. ``CREATE_NO_WINDOW`` suppresses the
    allocation; stdin is redirected as well because a launcher without a console has
    nothing to inherit standard input from.
    """
    options: dict = {"stdin": subprocess.DEVNULL}
    if os.name == "nt":
        options["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return options


def apply_windowless_spawn() -> None:
    """Default every ``subprocess`` spawn to windowless, for bridge and libraries.

    wechatauto (copied into ``work/wechatauto-replica``) shells out to ``cmd.exe`` /
    ``powershell.exe`` when it has to locate the WeChat process and database. Those
    helpers predate the windowless bridge and never passed ``CREATE_NO_WINDOW``, so
    each of them flashed a console too. Defaulting it here keeps the fix in one place
    and still honours an explicit ``creationflags`` from any caller.
    """
    if os.name != "nt" or getattr(subprocess, "_windowless_default", False):
        return
    default_flag = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    original_run = subprocess.run
    original_popen_init = subprocess.Popen.__init__

    def run_without_window(*args, **kwargs):
        kwargs["creationflags"] = kwargs.get("creationflags", 0) | default_flag
        kwargs.setdefault("stdin", subprocess.DEVNULL)
        return original_run(*args, **kwargs)

    def popen_init_without_window(self, *args, **kwargs):
        kwargs["creationflags"] = kwargs.get("creationflags", 0) | default_flag
        kwargs.setdefault("stdin", subprocess.DEVNULL)
        return original_popen_init(self, *args, **kwargs)

    subprocess.run = run_without_window
    subprocess.Popen.__init__ = popen_init_without_window
    subprocess._windowless_default = True


def ask_openclaw(config: dict, peer: str, model_alias: str,
                 thinking_level: str, prompt: str, epoch: int = 0) -> str:
    maybe_auto_compact_before_turn(config, peer, {"session_epoch": epoch})
    command = build_openclaw_command(config, peer, model_alias, thinking_level, prompt,
                                     epoch=epoch)
    result = subprocess.run(command, text=True, capture_output=True, encoding="utf-8",
                            timeout=210, **openclaw_run_options())
    if result.returncode:
        raise RuntimeError(f"OpenClaw exited {result.returncode}: {result.stderr[-400:]}")
    data = json.loads(result.stdout)
    return extract_reply(data)


def use_hook_transport(config: dict) -> bool:
    """Return whether sending should use the local WeChat Hook HTTP API."""
    return str(config.get("send_mode", "gui")).strip().lower() == "hook"


def build_hook_request(config: dict, peer: str, reply: str) -> tuple[str, dict]:
    """Build the request accepted by WeChat-Hook's SendTextMsg endpoint."""
    base = str(config.get("hook_url", "http://127.0.0.1:30001")).rstrip("/")
    endpoint = base if base.lower().endswith("/sendtextmsg") else f"{base}/SendTextMsg"
    return endpoint, {"wxidorgid": peer, "msg": reply}




def send_wechat_hook(config: dict, peer: str, reply: str, at_wxid: str | None = None,
                     at_name: str | None = None) -> None:
    endpoint, payload = build_hook_request(config, peer, reply)
    if at_wxid and at_name and peer.endswith("@chatroom"):
        payload["msg"] = f"@{at_name} {reply}"
        payload["atlist"] = ("<msgsource><atuserlist>thexed," +
                              str(at_wxid) +
                              "</atuserlist><alnode><fr>1</fr></alnode></msgsource>")
        endpoint = endpoint.rsplit("/", 1)[0] + "/SendTextMsg_NoSrc"
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    timeout = float(config.get("hook_timeout_seconds", 5))
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"WeChat Hook HTTP {exc.code}: {detail[-300:]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"WeChat Hook unavailable: {exc.reason}") from exc
    if raw.strip():
        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            result = raw.strip()
        LOG.info("WeChat Hook send peer=%s response=%s", peer, result)


def send_wechat(db, peer: str, reply: str, own_sender_id: int, gui=None,
                config: dict | None = None, at_wxid: str | None = None,
                at_name: str | None = None) -> None:
    if config and use_hook_transport(config):
        before = db.get_messages(peer, limit=1)
        baseline = int(before[0]["sort_seq"]) if before else 0
        send_wechat_hook(config, peer, reply, at_wxid=at_wxid, at_name=at_name)
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            records = db.get_messages(peer, limit=8)
            if was_sent(records, baseline, own_sender_id, reply) or (
                at_name and was_sent(records, baseline, own_sender_id, f"@{at_name} {reply}")
            ):
                return
            time.sleep(1)
        raise RuntimeError("WeChat Hook send was not confirmed in the AI account database")

    from wechatauto.guia import WeChatGUI

    before = db.get_messages(peer, limit=1)
    baseline = int(before[0]["sort_seq"]) if before else 0
    previous_foreground = get_foreground_window()
    if gui is None:
        ai_user = db.get_self_info()["username"]
        gui = WeChatGUI(hwnd=find_ai_window(ai_user))
    send_error = None
    try:
        # The library assumes sender_id=2 for its own verifier, but this
        # account's local database uses sender_id=1. Verify explicitly below.
        search_name = resolve_search_name(db, peer)
        response = gui.send_msg(reply, search_name, verify=False)
        if response is None:
            send_error = "GUI sender returned no response"
        elif isinstance(response, dict) and response.get("success") is False:
            send_error = str(response.get("message") or "GUI sender reported failure")
    except Exception as exc:
        send_error = str(exc)
    try:
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            if was_sent(db.get_messages(peer, limit=8), baseline, own_sender_id, reply):
                return
            time.sleep(1)
        raise RuntimeError(f"WeChat send was not confirmed in the AI account database: {send_error}")
    finally:
        restore_foreground_window(previous_foreground)


def send_wechat_sequence(db, peer: str, replies: list[str], own_sender_id: int,
                         delay_seconds: float, config: dict | None = None) -> None:
    gui = None
    if not (config and use_hook_transport(config)):
        from wechatauto.guia import WeChatGUI
        ai_user = db.get_self_info()["username"]
        gui = WeChatGUI(hwnd=find_ai_window(ai_user))
    for index, reply in enumerate(replies):
        send_wechat(db, peer, reply, own_sender_id, gui=gui, config=config)
        if index + 1 < len(replies):
            time.sleep(delay_seconds)


def process_offline_messages(db, config: dict, state: dict, dry_run: bool = False) -> int:
    """Discard offline message bodies and send one availability notice per peer."""
    notified = 0
    own_user = state["own_user"]
    for session in db.get_sessions(limit=1000):
        peer = session["username"]
        if not is_direct_peer(peer, own_user):
            continue
        chat = state["chats"].get(peer)
        if chat is None:
            chat = {"last_seq": state["activated_at"] * 1000 - 1,
                    "model": config["initial_model"], "thinking": "off", "humanized": True}
            state["chats"][peer] = chat
        chat.setdefault("thinking", "off")
        has_incoming = False
        while True:
            messages = db.get_new_messages(peer, since_seq=chat["last_seq"], limit=100)
            if not messages:
                break
            newest, batch_incoming = plan_offline_batch(
                messages, chat["last_seq"], config["ai_sender_id"])
            if newest <= chat["last_seq"]:
                break
            chat["last_seq"] = newest
            has_incoming = has_incoming or batch_incoming
            if len(messages) < 100:
                break
        save_state(STATE_PATH, state)
        if not has_incoming:
            continue
        LOG.info("offline activity peer=%s latest_seq=%s; content discarded",
                 peer, chat["last_seq"])
        if not dry_run:
            send_wechat(db, peer, ONLINE_NOTICE, config["ai_sender_id"], config=config)
            LOG.info("online notice verified peer=%s", peer)
        notified += 1
    return notified


def notify_group_failure(db, config: dict, task: dict) -> None:
    """Tell an @-ed group member that the reply failed instead of silent waiting."""
    peer = task["peer"]
    if str(task.get("request") or "").strip().startswith("/"):
        return
    if not should_notify_group_failure(peer):
        return
    msg = task.get("msg") or {}
    raw_content = str(msg.get("content", ""))
    sender_match = re.match(r"^([^:\n]+):\s*", raw_content)
    sender_wxid = (sender_match.group(1).strip() if sender_match else
                   str(msg.get("sender_username") or "").strip())
    try:
        sender_name = resolve_group_sender_name(db, peer, sender_wxid)
    except Exception:
        sender_name = ""
    send_wechat(db, peer, GROUP_FAILURE_NOTICE, config["ai_sender_id"], config=config,
                at_wxid=sender_wxid or None, at_name=sender_name or sender_wxid or None)
    LOG.info("group failure notice sent peer=%s seq=%s", peer, msg.get("sort_seq"))


def process_group_task(db, config: dict, state: dict, task: dict) -> None:
    peer = task["peer"]
    msg = task["msg"]
    request_text = task["request"]
    chat = state["chats"][peer]
    started = time.monotonic()
    quote_text = task.get("quote_text")
    if quote_text and not task.get("quote_has_image") and task.get("quote_comment"):
        prompt = prepare_model_prompt(
            f"请评价下面这条群聊引用消息，结合其内容给出简洁、自然、客观的评论。"
            f"不要复述提示词，不要输出Markdown。\n引用消息：\n{quote_text}"
        )
        reply = sanitize_reply(ask_openclaw(
            config, peer, chat.get("model", "qwen"), chat.get("thinking", "off"), prompt,
            epoch=int(chat.get("session_epoch", 0)),
        ))
        reply = format_timed_reply(reply, time.monotonic() - started,
                                   chat.get("model", "qwen"), chat.get("thinking", "off"))
        send_wechat(db, peer, reply, config["ai_sender_id"], config=config)
        LOG.info("group quoted text comment reply verified peer=%s seq=%s chars=%s",
                 peer, msg.get("sort_seq"), len(reply))
        return
    if quote_text and not task.get("quote_has_image") and task.get("quote_search"):
        reply = summarize_search(config, peer, chat, quote_text)
        reply = format_timed_reply(reply, time.monotonic() - started,
                                   chat.get("model", "qwen"), chat.get("thinking", "off"))
        send_wechat(db, peer, reply, config["ai_sender_id"], config=config)
        LOG.info("group quoted text search reply verified peer=%s seq=%s chars=%s",
                 peer, msg.get("sort_seq"), len(reply))
        return
    visual_msg = msg
    if task.get("reference_latest_visual"):
        # Resolve the latest image at worker time so a queued request sees the
        # most recent completed media row, not just the text that mentioned it.
        history = db.get_messages(peer, limit=50)
        candidates = nearby_visual_messages(history, msg)
        if not candidates:
            candidates = [item for item in history
                          if int(item.get("sort_seq") or 0) < int(msg.get("sort_seq") or 0)
                          and is_visual_message(item)]
        if candidates:
            visual_msg = candidates[-1]
            LOG.info("group visual history matched peer=%s request_seq=%s image_seq=%s",
                     peer, msg.get("sort_seq"), visual_msg.get("sort_seq"))
        else:
            LOG.info("group visual history not found peer=%s request_seq=%s",
                     peer, msg.get("sort_seq"))
    if task.get("visual_search"):
        reply = summarize_quoted_visual_search(db, config, peer, chat, msg, request_text)
        reply = format_timed_reply(reply, time.monotonic() - started,
                                   chat.get("model", "qwen"), chat.get("thinking", "off"))
        send_wechat(db, peer, reply, config["ai_sender_id"], config=config)
        if request_text and not is_pure_visual_request(request_text):
            text_reply = sanitize_reply(ask_openclaw(
                config, peer, chat.get("model", "qwen"), chat.get("thinking", "off"),
                prepare_model_prompt(request_text), epoch=int(chat.get("session_epoch", 0))))
            send_wechat(db, peer, format_timed_reply(
                text_reply, time.monotonic() - started,
                chat.get("model", "qwen"), chat.get("thinking", "off")),
                config["ai_sender_id"], config=config)
        LOG.info("group visual search reply verified peer=%s seq=%s chars=%s",
                 peer, msg.get("sort_seq"), len(reply))
        return
    if is_visual_message(visual_msg):
        image_path = vision_path = None
        try:
            image_temp = RUNTIME_DIR / "vision_tmp"
            image_path = extract_visual(
                db, peer, int(visual_msg["local_id"]), visual_msg, image_temp,
                hook_url=str(config.get("hook_url") or ""),
            )
            vision_path = prepare_vision_image(image_path, image_temp)
            label = "动画表情" if is_emoji_message(visual_msg) else "图片"
            description = describe_image(
                vision_path,
                f"请先客观描述这个{label}可见的外形、颜色、结构和文字，再判断主体。"
                "不要仅凭模糊轮廓猜测，无法确认时明确说无法确认。只输出简洁事实。",
                config,
                model_alias=chat.get("model", "qwen"),
            )
            prompt = prepare_model_prompt(
                f"用户在群聊中请求识别{label}。视觉识别结果：\n{description}\n\n"
                "请只用一句自然、通顺的中文纯文本回复，最多60个中文字符。"
                "不要使用Markdown、项目符号、括号动作或连续标点，不要提及识别流程。"
            )
            reply = sanitize_reply(ask_openclaw(
                config, peer, chat.get("model", "qwen"), chat.get("thinking", "off"), prompt,
                epoch=int(chat.get("session_epoch", 0)),
            ))
            reply = compact_visual_reply(reply)
        except (ImageUnavailable, VisionError, OSError, KeyError):
            LOG.exception("group image processing failed peer=%s seq=%s", peer, msg.get("sort_seq"))
            reply = "图片已收到，但暂时无法识别。"
        finally:
            for path in {path for path in (vision_path, image_path) if path is not None}:
                try:
                    cleanup_image(path, RUNTIME_DIR / "vision_tmp")
                except (OSError, ValueError):
                    LOG.exception("group image cleanup failed path=%s", path)
        reply = format_timed_reply(reply, time.monotonic() - started,
                                   chat.get("model", "qwen"), chat.get("thinking", "off"))
        send_wechat(db, peer, reply, config["ai_sender_id"], config=config)
        LOG.info("group visual reply verified peer=%s seq=%s source_seq=%s chars=%s",
                 peer, msg.get("sort_seq"), visual_msg.get("sort_seq"), len(reply))
        return
    action = classify_message(
        {"type": "文本", "content": request_text, "sender_id": 999999},
        config["ai_sender_id"],
    )
    if action and action[0] == "model":
        chat["model"] = action[1]
        save_state(STATE_PATH, state)
        reply = f"已切换到 {action[1]}。"
    elif action and action[0] == "thinking":
        reply = apply_thinking_mode(chat, action[1])
        save_state(STATE_PATH, state)
    elif action and action[0] == "help":
        reply = render_daily_help()
    elif action and action[0] == "search":
        reply = summarize_search(config, peer, chat, action[1])
    elif action and action[0] == "compact":
        reply = compact_session_reply(config, peer, chat)
        save_state(STATE_PATH, state)
    elif action and action[0] == "reset":
        reply = reset_session_reply(chat)
        save_state(STATE_PATH, state)
    elif action and action[0] == "clear":
        reply = clear_chat_memory(state, peer, chat)
        save_state(STATE_PATH, state)
    else:
        if is_group_summary_request(request_text):
            history = db.get_messages(
                peer, limit=int(config.get("group_history_limit", GROUP_SUMMARY_HISTORY_LIMIT)))
            prompt = prepare_model_prompt(
                group_summary_prompt(history, request_text, config["ai_sender_id"])
            )
        else:
            prompt = prepare_model_prompt(request_text)
        reply = sanitize_reply(ask_openclaw(
            config, peer, chat["model"], chat.get("thinking", "off"), prompt,
            epoch=int(chat.get("session_epoch", 0)),
        ))
        maybe_auto_compact(config, peer, chat, time.monotonic() - started,
                           threshold=GROUP_COMPACT_AFTER_SECONDS)
    if action and action[0] in {"model", "thinking", "help", "compact", "reset", "clear"}:
        final_reply = reply
    elif action and action[0] == "search":
        # Search has two stages (SearXNG retrieval + model synthesis), so expose
        # the total wall-clock time just like ordinary model replies while keeping
        # the result as one message.
        final_reply = format_timed_reply(
            reply, time.monotonic() - started, chat["model"],
            chat.get("thinking", "off"),
        )
    else:
        final_reply = prepare_reply_parts(
            reply, time.monotonic() - started, chat["model"],
            chat.get("thinking", "off"), False, compact=False,
        )[-1]
    raw_content = str(msg.get("content", ""))
    sender_match = re.match(r"^([^:\n]+):\s*", raw_content)
    sender_wxid = (sender_match.group(1).strip() if sender_match else
                   str(msg.get("sender_username") or "").strip())
    sender_name = resolve_group_sender_name(db, peer, sender_wxid)
    send_wechat(db, peer, final_reply, config["ai_sender_id"], config=config,
                at_wxid=sender_wxid or None, at_name=sender_name or sender_wxid or None)
    LOG.info("group reply verified peer=%s seq=%s chars=%s",
             peer, msg.get("sort_seq"), len(final_reply))


def process_new_messages(db, config: dict, state: dict, dry_run: bool = False,
                         group_dispatcher: GroupTaskDispatcher | None = None) -> int:
    if not dry_run and not use_hook_transport(config):
        find_ai_window(state["own_user"])
    total = 0
    own_user = state["own_user"]
    for session in db.get_sessions(limit=1000):
        peer = session["username"]
        group_peer = is_group_peer(peer, own_user)
        if not is_direct_peer(peer, own_user) and not group_peer:
            continue
        chat = state["chats"].get(peer)
        if chat is None:
            # A chat created after activation must retain its first incoming message.
            chat = {"last_seq": state["activated_at"] * 1000 - 1,
                    "model": config["initial_model"], "thinking": "off", "humanized": True}
            state["chats"][peer] = chat
            save_state(STATE_PATH, state)
        chat.setdefault("thinking", "off")
        messages = db.get_new_messages(peer, since_seq=chat["last_seq"], limit=100)
        for msg in messages:
            seq = int(msg["sort_seq"])
            if seq <= chat["last_seq"]:
                continue
            chat["last_seq"] = seq
            save_state(STATE_PATH, state)
            if group_peer:
                # Group chats are completely paused during Tokyo quiet hours.
                # Advance the watermark so these messages are never replayed at 08:00.
                if is_tokyo_quiet_hours(message_tokyo_datetime(msg)):
                    LOG.info("quiet-hours group message ignored peer=%s seq=%s",
                             peer, seq)
                    continue
                if int(msg.get("sender_id") or 0) == config["ai_sender_id"]:
                    continue
                mention_names = config.get("group_mention_names") or ["大肥鱼", "DSH20260918"]
                raw_group_content = str(msg.get("content", ""))
                group_request = extract_group_mention(raw_group_content, mention_names)
                quote_request, quote_has_image = extract_group_quote_request(
                    raw_group_content, mention_names)
                quote_text = extract_group_quote_text(raw_group_content)
                if group_request is None and quote_request is not None:
                    group_request = quote_request
                sender_key = str(msg.get("sender_username") or msg.get("sender_id") or "")
                pending = chat.get("group_visual_pending")
                if group_request is None and pending:
                    matched_request = match_group_visual_pending(pending, msg)
                    if matched_request is not None:
                        chat.pop("group_visual_pending", None)
                        save_state(STATE_PATH, state)
                        if dry_run:
                            continue
                        if group_dispatcher is None:
                            raise RuntimeError("group dispatcher is required for live processing")
                        group_dispatcher.enqueue(peer, {
                            "peer": peer, "msg": dict(msg), "request": matched_request,
                        })
                        total += 1
                        continue
                    if int(msg.get("sort_seq") or 0) > int(pending.get("sort_seq") or 0) + GROUP_VISUAL_WINDOW_MS:
                        chat.pop("group_visual_pending", None)
                        save_state(STATE_PATH, state)
                if group_request is None:
                    continue
                if quote_text and (is_group_quote_comment_request(group_request) or
                                   is_group_quote_search_request(group_request)):
                    if dry_run:
                        continue
                    if group_dispatcher is None:
                        raise RuntimeError("group dispatcher is required for live processing")
                    group_dispatcher.enqueue(peer, {
                        "peer": peer, "msg": dict(msg), "request": group_request,
                        "quote_text": quote_text,
                        "quote_has_image": quote_has_image,
                        "quote_comment": is_group_quote_comment_request(group_request),
                        "quote_search": is_group_quote_search_request(group_request),
                    })
                    total += 1
                    LOG.info("group quoted text request peer=%s seq=%s request=%s",
                             peer, seq, group_request[:80])
                    continue
                if quote_has_image or is_group_visual_history_request(group_request):
                    if dry_run:
                        continue
                    if group_dispatcher is None:
                        raise RuntimeError("group dispatcher is required for live processing")
                    group_dispatcher.enqueue(peer, {
                        "peer": peer, "msg": dict(msg), "request": group_request,
                        "reference_latest_visual": True,
                        "visual_search": group_request.lower().startswith("/search"),
                    })
                    total += 1
                    LOG.info("group visual history request peer=%s seq=%s request=%s",
                             peer, seq, group_request[:80])
                    continue
                if is_group_visual_request(group_request) or not group_request:
                    chat["group_visual_pending"] = make_group_visual_pending(
                        sender_key, seq, group_request)
                    save_state(STATE_PATH, state)
                    LOG.info("group visual pending peer=%s seq=%s sender=%s request=%s",
                             peer, seq, sender_key, group_request[:80])
                    continue
                LOG.info("incoming group=%s seq=%s mention sender=%s request=%s",
                         peer, seq, msg.get("sender_username"), group_request[:80])
                if dry_run:
                    continue
                if group_dispatcher is None:
                    raise RuntimeError("group dispatcher is required for live processing")
                group_dispatcher.enqueue(peer, {
                    "peer": peer, "msg": dict(msg), "request": group_request,
                })
                total += 1
                continue
            if is_tokyo_quiet_hours(message_tokyo_datetime(msg)):
                quiet_date = message_tokyo_datetime(msg).date().isoformat()
                if not dry_run and chat.get("quiet_notice_date") != quiet_date:
                    send_wechat(db, peer, ONLINE_NOTICE, config["ai_sender_id"], config=config)
                    chat["quiet_notice_date"] = quiet_date
                    save_state(STATE_PATH, state)
                    LOG.info("quiet-hours offline notice verified peer=%s date=%s",
                             peer, quiet_date)
                continue
            action = classify_incoming_message(msg, config["ai_sender_id"])
            if action is None:
                continue
            kind, value = action
            started = time.monotonic()
            LOG.info("incoming peer=%s seq=%s kind=%s", peer, seq, kind)
            if not dry_run and kind != "help":
                previous_help_date = chat.get("daily_help_date")
                daily_help = claim_daily_help_notice(chat)
                if daily_help:
                    try:
                        send_wechat(db, peer, daily_help, config["ai_sender_id"], config=config)
                        save_state(STATE_PATH, state)
                        LOG.info("daily help verified peer=%s date=%s",
                                 peer, chat["daily_help_date"])
                    except Exception:
                        if previous_help_date is None:
                            chat.pop("daily_help_date", None)
                        else:
                            chat["daily_help_date"] = previous_help_date
                        save_state(STATE_PATH, state)
                        LOG.exception("daily help failed peer=%s; next message will retry", peer)
            if kind == "image":
                if chat["model"] == "deepseek" and not os.environ.get(str(config.get("deepseek_api_key_env", "DEEPSEEK_API_KEY")), "").strip():
                    reply_parts = ["DeepSeek V4.1 已支持识图，但本机尚未配置 DEEPSEEK_API_KEY。"]
                else:
                    image_path = None
                    vision_path = None
                    try:
                        image_temp = RUNTIME_DIR / "vision_tmp"
                        image_path = extract_visual(
                            db, peer, int(msg["local_id"]), msg, image_temp,
                            hook_url=str(config.get("hook_url") or ""),
                        )
                        vision_path = prepare_vision_image(image_path, image_temp)
                        media_label = "动画表情" if value == "emoji" else "图片"
                        description = describe_image(
                            vision_path,
                            f"请先客观描述这个{media_label}可见的外形、颜色、结构和文字，再判断主体。"
                            "不要仅凭模糊轮廓猜测，无法确认时明确说无法确认。只输出简洁事实。",
                            config,
                            model_alias=chat.get("model", "qwen"),
                        )
                        generation_started = time.monotonic()
                        prompt = prepare_model_prompt(
                            f"用户发送了一个{media_label}。视觉识别结果：\n{description}\n\n"
                            f"请根据{media_label}内容自然回复用户，不要提及识别流程。"
                        )
                        reply = sanitize_reply(ask_openclaw(
                            config, peer, chat.get("model", "qwen"), chat.get("thinking", "off"), prompt,
                            epoch=int(chat.get("session_epoch", 0)),
                        ))
                        reply_parts = prepare_reply_parts(
                            compact_visual_reply(reply),
                            time.monotonic() - generation_started, "qwen",
                            chat.get("thinking", "off"), chat.get("humanized", False),
                            compact=False,
                        )
                    except (ImageUnavailable, VisionError, OSError, KeyError) as exc:
                        LOG.warning("image processing failed peer=%s seq=%s: %s", peer, seq, exc)
                        reply_parts = ["图片已收到，但本地图片解密尚未就绪，暂时无法识别。文字消息仍可正常使用。"]
                    finally:
                        cleanup_paths = {path for path in (vision_path, image_path) if path is not None}
                        for cleanup_path in cleanup_paths:
                            try:
                                cleanup_image(cleanup_path, RUNTIME_DIR / "vision_tmp")
                            except (OSError, ValueError):
                                LOG.exception("image cleanup failed path=%s", cleanup_path)
            elif kind == "model":
                chat["model"] = value
                save_state(STATE_PATH, state)
                reply = f"已切换到 {value}。"
            elif kind == "thinking":
                reply = apply_thinking_mode(chat, value)
                save_state(STATE_PATH, state)
            elif kind == "help":
                reply = render_daily_help()
            elif kind == "search":
                if is_quoted_visual_request(msg.get("content", "")):
                    reply = summarize_quoted_visual_search(db, config, peer, chat, msg, value)
                else:
                    reply = summarize_search(config, peer, chat, value)
            elif kind == "compact":
                reply = compact_session_reply(config, peer, chat)
                save_state(STATE_PATH, state)
            elif kind == "reset":
                reply = reset_session_reply(chat)
                save_state(STATE_PATH, state)
            elif kind == "clear":
                reply = clear_chat_memory(state, peer, chat)
                save_state(STATE_PATH, state)
            elif dry_run:
                LOG.info("dry-run peer=%s seq=%s model=%s", peer, seq, chat["model"])
                continue
            else:
                if is_quoted_visual_request(msg.get("content", "")):
                    reply = summarize_quoted_visual_reply(db, config, peer, chat, msg, value)
                    reply = format_timed_reply(reply, time.monotonic() - started,
                                               chat.get("model", "qwen"), chat.get("thinking", "off"))
                    reply_parts = [reply]
                    generation_started = None
                    # The quoted image was handled as a visual request; do not
                    # feed the raw XML quote card into the normal chat prompt.
                    if not dry_run:
                        try:
                            send_wechat_sequence(
                                db, peer, reply_parts, config["ai_sender_id"],
                                float(config.get("humanized_delay_seconds", 1.0)),
                                config=config,
                            )
                            LOG.info("quoted visual reply verified peer=%s seq=%s model=%s chars=%s",
                                     peer, seq, chat.get("model", "qwen"), len(reply))
                        except Exception:
                            LOG.exception("quoted visual send failed peer=%s seq=%s", peer, seq)
                    continue
                generation_started = time.monotonic()
                compact_prompt = is_short_casual_prompt(value)
                quoted = extract_quoted_message_text(msg.get("content", ""))
                if quoted and quoted[1]:
                    model_prompt = prepare_model_prompt(
                        f"用户当前问题：{quoted[0]}\n\n用户引用的消息：\n{quoted[1]}\n\n"
                        "请优先理解并回答被引用的消息；如果引用内容是图片或媒体，说明当前只能依据可见引用信息回答。"
                    )
                else:
                    model_prompt = prepare_model_prompt(value)
                try:
                    reply = sanitize_reply(ask_openclaw(
                        config, peer, chat["model"], chat.get("thinking", "off"), model_prompt,
                        epoch=int(chat.get("session_epoch", 0)),
                    ))
                    generation_seconds = time.monotonic() - generation_started
                    LOG.info("generation peer=%s seq=%s model=%s compact=%s seconds=%.2f",
                             peer, seq, chat["model"], compact_prompt, generation_seconds)
                    reply_parts = prepare_reply_parts(
                        reply, generation_seconds, chat["model"],
                        chat.get("thinking", "off"), chat.get("humanized", False),
                        compact=compact_prompt)
                    maybe_auto_compact(config, peer, chat, generation_seconds)
                except Exception:
                    LOG.exception("agent failed for peer=%s seq=%s", peer, seq)
                    reply_parts = ["AI 暂时无法回复，请稍后重发。"]
            if dry_run:
                continue
            delivery_started = time.monotonic()
            try:
                if kind in ("model", "thinking", "help", "compact", "reset", "clear"):
                    reply_parts = [reply]
                elif kind == "search":
                    reply_parts = [format_timed_reply(
                        reply, time.monotonic() - started, chat["model"],
                        chat.get("thinking", "off"),
                    )]
                send_wechat_sequence(
                    db, peer, reply_parts, config["ai_sender_id"],
                    float(config.get("humanized_delay_seconds", 1.0)),
                    config=config,
                )
                LOG.info("reply verified peer=%s seq=%s parts=%s chars=%s delivery_seconds=%.2f",
                         peer, seq, len(reply_parts), sum(len(part) for part in reply_parts),
                         time.monotonic() - delivery_started)
            except Exception:
                LOG.exception("send failed for peer=%s seq=%s; message will not auto-retry", peer, seq)
            total += 1
    return total


def show_console_window(config: dict) -> None:
    """Hide the bridge's own console window unless config asks to keep it visible.

    ``start.ps1`` creates the process without a console window, so this matters when
    the bridge is started by hand in a terminal and then left running.
    """
    if os.name != "nt":
        return
    if config.get("show_console_window"):
        return
    try:
        handle = ctypes.windll.kernel32.GetConsoleWindow()
        if handle:
            ctypes.windll.user32.ShowWindow(handle, 0)
    except Exception:
        LOG.debug("unable to hide the console window", exc_info=True)


def install_crash_logging() -> None:
    """Make sure a crashing bridge leaves evidence instead of vanishing silently."""
    global CRASH_LOG_STREAM
    try:
        CRASH_LOG_STREAM = open(RUNTIME_DIR / "bridge.fault.log", "a", encoding="utf-8")
        faulthandler.enable(file=CRASH_LOG_STREAM)
    except OSError:
        LOG.warning("native fault logging unavailable", exc_info=True)

    def log_unhandled(exc_type, exc, traceback):
        LOG.critical("unhandled exception; bridge is exiting",
                     exc_info=(exc_type, exc, traceback))

    def log_thread_unhandled(args):
        name = args.thread.name if args.thread is not None else "unknown"
        LOG.error("unhandled thread exception thread=%s", name,
                  exc_info=(args.exc_type, args.exc_value, args.exc_traceback))

    sys.excepthook = log_unhandled
    threading.excepthook = log_thread_unhandled


def acquire_instance_lock() -> bool:
    """Allow only one bridge process to access the shared database cache."""
    global INSTANCE_LOCK_HANDLE
    INSTANCE_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    handle = open(INSTANCE_LOCK_PATH, "a+b")
    try:
        handle.seek(0)
        handle.write(b"0")
        handle.flush()
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    except (OSError, IOError):
        handle.close()
        return False
    INSTANCE_LOCK_HANDLE = handle
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--send-test", action="store_true")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    if not acquire_instance_lock():
        LOG.warning("another bridge instance already owns the database lock; exiting")
        return 0
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    show_console_window(config)
    LOG.setLevel(logging.INFO)
    LOG.propagate = False
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    file_handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
    file_handler.setFormatter(formatter)
    LOG.addHandler(file_handler)
    if config.get("show_console_window"):
        # Only keep a pipe to the parent console when the window stays visible.
        if sys.stdout is not None:
            stream_handler = logging.StreamHandler(sys.stdout)
            stream_handler.setFormatter(formatter)
            LOG.addHandler(stream_handler)
    apply_windowless_spawn()
    install_crash_logging()
    from wechatauto.db import WeChatDB

    while True:
        try:
            db = WeChatDB(
                db_dir=resolve_db_dir(config),
                account=config["ai_account_dir"],
                workdir=str(RUNTIME_DIR / "db_cache"),
            )
            ai_user = db.get_self_info().get("username")
            if not config["ai_account_dir"].startswith(ai_user):
                raise RuntimeError("Configured AI account does not match the logged-in database")
            break
        except Exception:
            LOG.exception("AI WeChat account not ready; retrying in 20 seconds")
            time.sleep(20)
    if args.send_test:
        send_wechat(
            db, config["main_peer"], "AI 小号电脑端发送测试：桥接已准备。",
            config["ai_sender_id"], config=config,
        )
        LOG.info("controlled test message verified")
        return 0
    prior = load_state(STATE_PATH) if STATE_PATH.exists() else {}
    if "chats" in prior:
        state = prior
    else:
        activated = int(time.time())
        chats = {}
        for session in db.get_sessions(limit=1000):
            peer = session["username"]
            if not is_direct_peer(peer, ai_user):
                continue
            latest = db.get_messages(peer, limit=1)
            chats[peer] = {"last_seq": int(latest[0]["sort_seq"]) if latest else activated * 1000 - 1,
                           "model": prior.get("model", config["initial_model"]) if peer == config["main_peer"] else config["initial_model"],
                           "thinking": "off", "humanized": True}
            if peer == config["main_peer"]:
                chats[peer]["last_seq"] = max(chats[peer]["last_seq"], int(prior.get("last_seq", 0)))
        state = {"activated_at": activated, "own_user": ai_user, "chats": chats}
    for chat in state.get("chats", {}).values():
        chat.setdefault("humanized", True)
    save_state(STATE_PATH, state)
    LOG.info("bridge ready direct_chats=%s deepseek_key=%s", len(state["chats"]),
             bool(os.environ.get(str(config.get("deepseek_api_key_env", "DEEPSEEK_API_KEY")), "").strip()))
    group_dbs = {}
    group_db_lock = threading.Lock()
    db_access_lock = threading.RLock()

    def handle_group_task(task: dict) -> None:
        peer = task["peer"]
        with group_db_lock:
            group_db = group_dbs.get(peer)
            if group_db is None:
                digest = hashlib.sha256(peer.encode("utf-8")).hexdigest()[:12]
                group_db = WeChatDB(
                    db_dir=resolve_db_dir(config),
                    account=config["ai_account_dir"],
                    workdir=str(RUNTIME_DIR / f"group_db_{digest}"),
                )
                group_dbs[peer] = group_db
        timed_out = threading.Event()
        finished = threading.Event()

        def timeout_notice() -> None:
            if finished.is_set():
                return
            timed_out.set()
            try:
                send_wechat(group_db, peer,
                            "图片识别超时，请稍后重发。",
                            config["ai_sender_id"], config=config)
                LOG.warning("group visual task timeout peer=%s seq=%s",
                            peer, task["msg"].get("sort_seq"))
            except Exception:
                LOG.exception("group timeout notice failed peer=%s", peer)

        timer = threading.Timer(float(config.get("group_visual_timeout_seconds", 45)),
                                timeout_notice)
        timer.daemon = True
        timer.start()
        try:
            # Main polling and group workers share the wechatauto cache layer.
            # Serialize DB access to avoid concurrent tmp->db replacements.
            with db_access_lock:
                process_group_task(group_db, config, state, task)
        except Exception:
            LOG.exception("group reply failed peer=%s seq=%s",
                          peer, task["msg"].get("sort_seq"))
            try:
                notify_group_failure(group_db, config, task)
            except Exception:
                LOG.exception("group failure notice could not be sent peer=%s", peer)
        finally:
            finished.set()
            timer.cancel()

    group_dispatcher = GroupTaskDispatcher(handle_group_task)
    was_offline = True
    try:
        while True:
            try:
                if not args.dry_run and not use_hook_transport(config):
                    find_ai_window(state["own_user"])
                if was_offline:
                    with db_access_lock:
                        notices = process_offline_messages(db, config, state, dry_run=args.dry_run)
                    LOG.info("online recovery complete notices=%s", notices)
                    was_offline = False
                else:
                    with db_access_lock:
                        process_new_messages(
                            db, config, state, dry_run=args.dry_run,
                            group_dispatcher=group_dispatcher,
                        )
            except Exception:
                was_offline = True
                LOG.exception("poll failed; retrying")
            if args.once:
                return 0
            time.sleep(config["poll_seconds"])
    finally:
        group_dispatcher.shutdown()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(0)
