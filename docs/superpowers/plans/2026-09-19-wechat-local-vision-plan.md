# 微信 AI 小号本地图片识别 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 在 Qwen 模式下从本地微信缓存读取图片并使用本机 Qwen3.5 视觉编码器生成回复，DeepSeek 模式只返回不支持图片的提示。

**Architecture:** 桥接继续从 `WeChatDB` 轮询消息；新增媒体适配层调用 `MediaDownloader` 解密图片，并新增本地 llama.cpp 视觉客户端。视觉结果转成文字后复用现有 OpenClaw 会话与人格；Hook 仍只负责发送文字，微信 GUI 不参与图片处理。

**Tech Stack:** Python 3.12、`wechatauto-replica`、llama.cpp OpenAI-compatible HTTP、PowerShell、现有 unittest。

**Spec:** `docs/superpowers/specs/2026-09-19-wechat-local-vision-design.md`

## Global Constraints

- 只有 Qwen 模式处理图片；DeepSeek 模式不读取、不解密图片。
- 图片和视觉请求始终留在本机，不调用微信 GUI、UIA、OCR 或图片预览窗口。
- 图片临时文件在处理成功或失败后删除。
- 图片处理失败不得阻塞后续文本消息。
- 保留现有文本 Hook 发送、每日帮助提示和回复限制。

## Review Focus

- DeepSeek 模式收到图片：测试桥接不调用媒体下载器，并发送精确切换提示。
- Qwen 模式收到没有缓存或无法解密的图片：测试发送明确失败提示并继续轮询。
- 视觉接口超时或返回非 2xx：测试临时文件清理和文本消息仍可处理。
- 图片消息和后续文本消息同一轮到达：测试消息序号推进且两类消息不互相丢失。
- llama.cpp 未加载 `mmproj` 或端口未监听：测试启动检查失败可记录，不启动重复桥接进程。

### Task 1: 本地视觉模型资源和启动链

**Files:**
- Modify: `outputs/WeChatAI/startup_backend.ps1`
- Modify: `outputs/WeChatAI/start.ps1`
- Modify: `outputs/WeChatAI/README.md`
- Create: `outputs/WeChatAI/tests/test_vision_runtime.ps1`

**Interfaces:**
- Produces a local `mmproj` path consumed by llama.cpp.
- Keeps llama server on `127.0.0.1:8080` and adds explicit `--mmproj <path>`.

- [ ] **Step 1: Write the failing runtime contract test.** Assert the startup script contains an explicit `--mmproj` argument, checks the file path, and never starts a second llama server when port 8080 is already listening.
- [ ] **Step 2: Run the test to verify it fails.** Run `powershell -ExecutionPolicy Bypass -File outputs/WeChatAI/tests/test_vision_runtime.ps1`; expected failure because the current startup script has no `mmproj` path.
- [ ] **Step 3: Download and pin the Qwen3.5 vision encoder.** Store the official `mmproj-Qwen3.5-9B-Uncensored-HauhauCS-Aggressive-BF16.gguf` under `C:\llama\models\`; record its URL and SHA256 in the README. Do not replace the existing main GGUF.
- [ ] **Step 4: Add explicit startup validation.** Make `startup_backend.ps1` fail with one log line when the encoder is missing, and pass `--mmproj` only when starting a new server.
- [ ] **Step 5: Run the contract test and inspect `llama-server --help`.** Expected PASS and the running command line must contain `--mmproj` after a controlled restart.

### Task 2: Local WeChat image extraction adapter

**Files:**
- Create: `outputs/WeChatAI/vision_media.py`
- Modify: `outputs/WeChatAI/bridge.py`
- Modify: `work/test_bridge.py`

**Interfaces:**
- `is_image_message(msg: dict) -> bool` recognizes local type 3 / `图片` without reading image content.
- `extract_image(db, peer: str, local_id: int, temp_dir: Path) -> Path` calls `MediaDownloader.download_image` and returns an existing file path or raises `ImageUnavailable`.
- `cleanup_image(path: Path) -> None` removes only files inside the bridge vision temp directory.

- [ ] **Step 1: Write failing unit tests.** Cover type 3 detection, missing media return, successful fake downloader path, and cleanup refusing a path outside the temp directory.
- [ ] **Step 2: Run the focused tests to verify failure.** Run `python -m unittest work.test_bridge.BridgeTests.test_image_media_adapter`; expected failure because the adapter does not exist.
- [ ] **Step 3: Implement the adapter.** Import `MediaDownloader` from the vendored replica, use the configured AI account database, create a dedicated temp directory, and never call GUI methods or open an image window.
- [ ] **Step 4: Run focused and full unit tests.** Expected PASS with all existing text behavior unchanged.

### Task 3: Local llama.cpp vision client

**Files:**
- Create: `outputs/WeChatAI/vision_client.py`
- Modify: `work/test_bridge.py`
- Modify: `outputs/WeChatAI/config.json`

**Interfaces:**
- `describe_image(path: Path, prompt: str, config: dict) -> str` posts an OpenAI-compatible multimodal request to `http://127.0.0.1:8080/v1/chat/completions` and returns non-empty text.
- `build_vision_payload(image_bytes: bytes, prompt: str, model: str) -> dict` produces a `data:image/...;base64,...` image URL and text content.

- [ ] **Step 1: Write failing tests.** Assert payload shape, MIME detection for PNG/JPEG, non-empty response extraction, and timeout/non-2xx errors.
- [ ] **Step 2: Run the focused tests to verify failure.** Expected failure because `vision_client.py` is missing.
- [ ] **Step 3: Implement the HTTP client.** Use only Python standard library, cap image bytes before encoding, use the configured local model id, and raise a typed error on timeout or malformed JSON.
- [ ] **Step 4: Run a real local probe.** Use a small local fixture image and the running llama server; record a successful description and confirm the request never leaves `127.0.0.1`.
- [ ] **Step 5: Run all tests.** Expected PASS.

### Task 4: Bridge routing and OpenClaw handoff

**Files:**
- Modify: `outputs/WeChatAI/bridge.py`
- Modify: `work/test_bridge.py`
- Modify: `outputs/WeChatAI/README.md`

**Interfaces:**
- `build_image_reply(config, chat, db, peer, msg) -> list[str]` returns DeepSeek notice or Qwen-generated reply parts.
- `DAILY_HELP` and existing text dispatch remain unchanged.

- [ ] **Step 1: Write failing tests.** Verify DeepSeek image dispatch never calls `extract_image` or `describe_image`, Qwen dispatch adds a visual description to the existing OpenClaw prompt, and media failures produce the exact configured fallback.
- [ ] **Step 2: Run focused tests to verify failure.** Expected failure because image routing is not implemented.
- [ ] **Step 3: Add image dispatch before text classification.** Advance the message sequence once, branch by current chat model, send the DeepSeek notice directly, or extract/describe the image and pass a bounded description into the existing Qwen session.
- [ ] **Step 4: Wrap media and vision calls in `try/finally`.** Always remove temporary files; log the failure without aborting the polling loop.
- [ ] **Step 5: Run focused and full tests.** Expected PASS with existing 34 tests plus the new image cases.
- [ ] **Step 6: Restart the bridge once and inspect logs.** Confirm `bridge ready` and no GUI window activation.

### Task 5: End-to-end acceptance and operational documentation

**Files:**
- Modify: `outputs/WeChatAI/README.md`
- Modify: `outputs/WeChatAI/CODEX_HANDOFF.md`

- [ ] **Step 1: Verify runtime prerequisites.** Check llama server command line includes `--mmproj`, OpenClaw is active, and the AI Hook remains on the configured port.
- [ ] **Step 2: Send a Qwen image test from the main WeChat account.** Confirm one text reply describes the image and the bridge log records media extraction, vision completion, and Hook delivery.
- [ ] **Step 3: Send a `/deepseek` command followed by an image.** Confirm only the DeepSeek unsupported-image message is returned and no vision request is logged.
- [ ] **Step 4: Send a normal text message after each image test.** Confirm text replies continue normally after both successful and failed image processing.
- [ ] **Step 5: Document the manual test result and rollback.** Record the mmproj path, model command, fallback behavior, and how to remove the vision argument if the local model server becomes unstable.
