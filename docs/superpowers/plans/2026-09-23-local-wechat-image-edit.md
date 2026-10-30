# Local WeChat Image Editing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a fully local, single-image editing flow to the WeChat bot using ComfyUI and InstructPix2Pix.

**Architecture:** Keep ComfyUI on localhost:8188 as an isolated on-demand backend. Add sender-scoped pending requests and one global image-edit queue to the bridge, submit a built-in ComfyUI workflow, then return the result with the existing Hook image-send endpoint. Preserve the current llama.cpp model and all unrelated chat behavior.

**Tech Stack:** Python 3.12 bridge, unittest, Windows AMD ComfyUI portable runtime, InstructPix2Pix FP16, local HTTP APIs.

**Spec:** `docs/superpowers/specs/2026-09-23-local-wechat-image-edit.md`

## Global Constraints

- Images are processed locally only; no cloud image API.
- Pending requests are keyed by group and sender and expire after 300 seconds.
- One image per request and one global edit at a time.
- Input image max 12 MiB; cap working resolution at 768 px on the longest edge.
- Never overwrite source images; output is a distinct file.
- Bind local image backend to `127.0.0.1`; keep ordinary chat independent.
- Do not edit existing user changes outside the named files or commit changes.

## Review Focus

- Expired requests or messages from another sender must never consume a pending edit.
- The first image must be frozen to its task; later images must not replace it.
- Untrusted local message content must not become a filesystem path or arbitrary workflow.
- ComfyUI timeouts, malformed history, and missing output must return a failure without hanging the bridge.
- Async Hook image sending must retain output files long enough for WeChat to read them.

### Task 1: Pending image-edit state and trigger recognition

**Files:** Create `outputs/WeChatAI/image_edit_state.py`, create `work/test_image_edit_state.py`.

- [x] Add tests for explicit edit trigger detection, sender/group isolation, expiry, requirement accumulation, and exactly-one-image completion.
- [x] Run tests and confirm they fail because module/functions do not exist.
- [x] Implement a small serializable state helper with injected time.
- [x] Rerun focused tests.

### Task 2: ComfyUI local API client

**Files:** Create `outputs/WeChatAI/image_edit_client.py`, create `work/test_image_edit_client.py`, add a versioned API workflow JSON under `outputs/WeChatAI/workflows/`.

- [x] Test safe image upload, workflow parameter injection, queue/poll/history flow, image retrieval, timeout, and missing-output handling with a local fake HTTP server.
- [x] Confirm focused tests fail before implementation.
- [x] Implement localhost-only client and fixed workflow; enforce size and image format bounds.
- [x] Rerun focused tests.

### Task 3: Bridge integration and image delivery

**Files:** Modify `outputs/WeChatAI/bridge.py`; extend `work/test_bridge.py`.

- [x] Test initial prompt, follow-up text/image pairing, request cleanup, no mention requirement for follow-ups, and image send payload.
- [x] Confirm failing tests first.
- [x] Add sender-scoped task state; do not route edit messages to normal OpenClaw text replies.
- [x] Add progress/failure replies, invoke image extraction, call local editor, and use Hook `/SendImgMsg` for the result.
- [x] Rerun bridge tests.

### Task 4: Isolated Windows runtime install and model download

**Files:** Create `outputs/WeChatAI/install_image_edit.ps1`, `outputs/WeChatAI/start_image_edit.ps1`, `outputs/WeChatAI/stop_image_edit.ps1`, update `outputs/WeChatAI/startup_backend.ps1`, add `outputs/WeChatAI/tests/test_image_edit_runtime.ps1`.

- [x] Install ComfyUI in a dedicated directory under `D:\AI\ComfyUI`; never modify llama.cpp or Ollama stores.
- [x] Install/use official Windows AMD runtime for `gfx1101` if supported by the current driver; do not run driver updates that require reboot.
- [x] Download the FP16 InstructPix2Pix checkpoint to ComfyUI's checkpoint folder and verify SHA256 where published.
- [x] Add idempotent localhost-only start/stop scripts. ComfyUI starts only for an image-edit request.
- [x] Validate AMD device detection and the actual local edit workflow.

### Task 5: Local edit smoke test and regression

**Files:** Runtime-only generated input/output under `work/wechat_ai_bridge_state/image_edit_*`.

- [x] Create a synthetic, non-person test image and make one simple edit.
- [x] Verify output decodes, dimensions, response time, and that source bytes remain unchanged.
- [x] Run relevant Python tests and PowerShell runtime checks.
- [ ] A real WeChat group edit remains to be user-tested.
