"""Measure llama-server prefill/decode speed directly (bypasses OpenClaw)."""
import json
import time
import urllib.request

URL = "http://127.0.0.1:8080/v1/chat/completions"


def chat(prompt, max_tokens=24):
    body = json.dumps({
        "model": "probe",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0.3,
    }).encode("utf-8")
    request = urllib.request.Request(URL, data=body, headers={"Content-Type": "application/json"})
    started = time.time()
    with urllib.request.urlopen(request, timeout=900) as response:
        data = json.loads(response.read().decode("utf-8"))
    wall = time.time() - started
    timings = data.get("timings") or {}
    content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
    return wall, timings, content


def main():
    long_prompt = "请阅读下面这段文字并只回复两个字：收到。\n" + ("这是一段用于测试预填充速度的填充文本。" * 300)
    for label, prompt, max_tokens in [
        ("short-a", "你好", 24),
        ("short-b", "你好", 24),
        ("long-8k", long_prompt, 8),
    ]:
        wall, timings, content = chat(prompt, max_tokens)
        prefill = timings.get("prompt_per_second")
        decode = timings.get("predicted_per_second")
        print(
            f"{label}: wall={wall:.2f}s "
            f"prompt_tokens={timings.get('prompt_n')} "
            f"prefill={round(prefill, 1) if prefill else None} tok/s "
            f"decoded={timings.get('predicted_n')} "
            f"decode={round(decode, 1) if decode else None} tok/s "
            f"out={content[:30]!r}"
        )


if __name__ == "__main__":
    main()
