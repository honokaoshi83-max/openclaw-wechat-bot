"""Sender-scoped, short-lived state for group image editing requests."""

from __future__ import annotations

import re


_EDIT_TERMS = (
    "修图", "修一下图", "改图", "改一下图", "修改图片", "修改一下图片",
    "修改图片内容", "编辑图片", "帮我改图", "帮我修图", "图片改一下",
)
_GENERIC_TRIGGERS = {
    "修图", "帮我修图", "帮我修一下图", "修一下图", "改图", "帮我改图",
    "修改图片", "修改一下图片", "修改一下图片内容", "编辑图片",
    "帮我修改图片", "帮我修改一下图片",
}


def _compact(text: str) -> str:
    return re.sub(r"[\s\u200b\u3000，。！？、,.!?：:；;]+", "", str(text or "")).lower()


def is_edit_intent(text: str) -> bool:
    """Recognize explicit requests to alter an image, not image-QA requests."""
    normalized = _compact(text)
    if not normalized:
        return False
    return any(term in normalized for term in _EDIT_TERMS)


def _is_generic_trigger(text: str) -> bool:
    normalized = _compact(text)
    return normalized in {_compact(value) for value in _GENERIC_TRIGGERS}


class ImageEditSessions:
    """Small JSON-serializable in-memory session registry; callers persist snapshot."""

    def __init__(self, timeout_seconds: float = 300.0):
        self.timeout_seconds = max(1.0, float(timeout_seconds))
        self._pending: dict[str, dict] = {}

    @staticmethod
    def _key(group: str, sender: str) -> str:
        return f"{group}\0{sender}"

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    def has_pending(self, group: str, sender: str, now: float) -> bool:
        key = self._key(str(group), str(sender))
        pending = self._pending.get(key)
        if pending is None:
            return False
        if float(now) > float(pending.get("expires_at", 0)):
            self._pending.pop(key, None)
            return False
        return True

    def prune(self, now: float) -> bool:
        expired = [key for key, item in self._pending.items()
                   if float(now) > float(item.get("expires_at", 0))]
        for key in expired:
            self._pending.pop(key, None)
        return bool(expired)

    def snapshot(self) -> dict[str, dict]:
        return {key: dict(value) for key, value in self._pending.items()}

    def restore(self, values: dict | None) -> None:
        self._pending = {
            str(key): dict(value) for key, value in (values or {}).items()
            if isinstance(value, dict)
        }

    def begin(self, group: str, sender: str, request: str, now: float) -> None:
        instruction = "" if _is_generic_trigger(request) else str(request or "").strip()
        key = self._key(str(group), str(sender))
        self._pending[key] = {
            "group": str(group),
            "sender": str(sender),
            "created_at": float(now),
            "expires_at": float(now) + self.timeout_seconds,
            "instruction": instruction,
            "image_message": None,
        }

    def accept(self, group: str, sender: str, message: dict, instruction: str,
               has_image: bool, now: float) -> dict | None:
        key = self._key(str(group), str(sender))
        pending = self._pending.get(key)
        if pending is None:
            return None
        if float(now) > float(pending.get("expires_at", 0)):
            self._pending.pop(key, None)
            return None

        clean_instruction = str(instruction or "").strip()
        if clean_instruction and not _is_generic_trigger(clean_instruction):
            pending["instruction"] = clean_instruction
        if has_image and pending.get("image_message") is None:
            pending["image_message"] = dict(message)

        if not pending.get("instruction") or not pending.get("image_message"):
            return None
        task = {
            "group": pending["group"],
            "sender": pending["sender"],
            "instruction": pending["instruction"],
            "image_message": pending["image_message"],
        }
        self._pending.pop(key, None)
        return task
