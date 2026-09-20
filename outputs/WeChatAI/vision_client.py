from __future__ import annotations

import base64
import json
import mimetypes
import urllib.error
import urllib.request
import os
from pathlib import Path


class VisionError(RuntimeError):
    pass


def build_vision_payload(image_bytes: bytes, prompt: str, model: str,
                         mime: str = "image/jpeg", max_tokens: int = 128) -> dict:
    encoded = base64.b64encode(image_bytes).decode("ascii")
    return {"model": model, "messages": [{"role": "user", "content": [
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}},
    ]}], "max_tokens": max_tokens, "temperature": 0.0}


def describe_image(path: Path, prompt: str, config: dict, model_alias: str = "qwen") -> str:
    image_path = Path(path)
    raw = image_path.read_bytes()
    if len(raw) > int(config.get("vision_max_bytes", 12 * 1024 * 1024)):
        raise VisionError("图片超过本地识别大小限制")
    mime = mimetypes.guess_type(image_path.name)[0] or "image/jpeg"
    if model_alias == "deepseek":
        model = str(config.get("deepseek_vision_model", "deepseek-flash"))
        endpoint = str(config.get("deepseek_vision_url", "https://api.deepseek.com/chat/completions"))
        api_key = os.environ.get(str(config.get("deepseek_api_key_env", "DEEPSEEK_API_KEY")), "").strip()
        if not api_key:
            raise VisionError("DeepSeek 图片接口未配置 DEEPSEEK_API_KEY")
    else:
        model = str(config["vision_model"])
        endpoint = str(config.get("vision_url", "http://127.0.0.1:8080/v1/chat/completions"))
        api_key = ""
    payload = build_vision_payload(raw, prompt, model, mime,
                                   max_tokens=int(config.get("vision_max_tokens", 256)))
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(endpoint, data=json.dumps(payload).encode(),
                                     headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=float(config.get("vision_timeout_seconds", 60))) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise VisionError(f"视觉服务不可用：{exc}") from exc
    try:
        message = data["choices"][0]["message"]
        text = str(message.get("content") or message.get("reasoning_content") or "").strip()
    except (KeyError, IndexError, TypeError) as exc:
        raise VisionError("视觉服务返回格式无效") from exc
    if not text:
        raise VisionError("本地视觉服务返回空结果")
    return text
