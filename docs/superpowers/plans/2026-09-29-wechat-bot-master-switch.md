# WeChat Bot Master Switch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a no-console Windows desktop switch that controls the WeChat bot stack while preserving current backend autostart and manual WeChat login.

**Architecture:** A shared PowerShell module stores an intentional-stop marker tied to the current Windows boot and provides status checks. Existing startup/recovery scripts honor that marker; a scoped lifecycle script starts or stops only this project's bridge, watchdog, Qwen server, OpenClaw Gateway, and active ComfyUI process. A small C# WinForms executable invokes the lifecycle script and opens at user sign-in.

**Tech Stack:** Windows PowerShell 5.1-compatible scripts; C# WinForms compiled with the installed .NET Framework C# compiler; PowerShell contract tests.

**Spec:** `docs/superpowers/specs/2026-09-29-wechat-bot-master-switch-design.md`

## Global Constraints

- Keep the existing `Codex-WeChat-AI-Bridge` backend autostart and one-minute recovery task enabled.
- The intentional-stop marker is valid only for its recorded Windows boot; a subsequent boot returns to the existing autostart behavior.
- Never start or close WeChat; the bridge waits for the user's manual AI-account login.
- Do not stop unrelated Python, Node, WSL, Docker, or other processes; limit actions to project-identified services.
- Do not change model routing, credentials, WeChat data, conversation memory, or unrelated dirty workspace files.

## Review Focus

- **Stale stop marker after reboot:** startup must ignore it when the Windows boot identity changes. Test matching and non-matching boot identities.
- **Scheduled startup racing with Stop:** startup and watchdog must both honor a current-boot stop marker. Test each entry point while stopped.
- **WeChat Hook unavailable on Start:** backend may start, but the bridge must remain waiting and WeChat must not launch. Test the existing supervisor contract.
- **Unrelated process using a known port:** Stop must not kill a listener unless its executable and command line identify the project process. Test with a process fixture or mocked process inventory.
- **Partial shutdown failure:** keep the stop marker set and report the failed component. Test the controller response to a failed lifecycle action.

---

### Task 1: Boot-scoped master-switch state

**Files:**
- Create: `outputs/WeChatAI/master_switch.psm1`
- Test: `outputs/WeChatAI/tests/test_master_switch_state.ps1`

**Interfaces:**
- `Get-BotBootIdentity() -> string`: returns the current OS `LastBootUpTime` in a stable invariant format.
- `Test-BotDisabledThisBoot([string]$RuntimeDirectory) -> bool`: true only when the stop marker matches the current boot identity.
- `Set-BotDisabledThisBoot([string]$RuntimeDirectory) -> void`
- `Clear-BotDisabledThisBoot([string]$RuntimeDirectory) -> void`

- [x] **Step 1: Write failing state tests** for no marker, current-boot marker, stale marker, and clearing the marker in a temporary runtime directory.
- [x] **Step 2: Run** `powershell.exe -NoProfile -ExecutionPolicy Bypass -File outputs/WeChatAI/tests/test_master_switch_state.ps1`; confirm it fails because the module functions do not exist.
- [x] **Step 3: Implement** the four exported functions. Store only the boot identity in a JSON marker under the supplied runtime directory; do not touch project credentials or configuration.
- [x] **Step 4: Re-run** the state test and require all four cases to pass.

### Task 2: Make existing autostart and recovery honor Stop

**Files:**
- Modify: `outputs/WeChatAI/startup_backend.ps1`
- Modify: `outputs/WeChatAI/wait_for_ai_wechat.ps1`
- Test: `outputs/WeChatAI/tests/test_master_switch_guards.ps1`

**Interfaces:**
- Consumes the state functions exported by `master_switch.psm1`.

- [x] **Step 1: Write failing guard tests** asserting both scripts load the shared module and return/exit before starting or restarting any component when the marker matches this boot; assert neither script starts WeChat.
- [x] **Step 2: Run** `powershell.exe -NoProfile -ExecutionPolicy Bypass -File outputs/WeChatAI/tests/test_master_switch_guards.ps1`; confirm the new guard assertions fail.
- [x] **Step 3: Implement** an early guard in `startup_backend.ps1` and a loop guard in `wait_for_ai_wechat.ps1`; on intentional Stop, the watchdog exits and cleans only its own lock file.
- [x] **Step 4: Re-run** the guard test plus `outputs/WeChatAI/tests/test_backend_autostart.ps1` and require all to pass.

### Task 3: Scoped lifecycle and status commands

**Files:**
- Create: `outputs/WeChatAI/manage_bot_stack.ps1`
- Test: `outputs/WeChatAI/tests/test_manage_bot_stack.ps1`

**Interfaces:**
- `manage_bot_stack.ps1 -Action Status|Start|Stop`
- Status emits structured JSON with `disabled`, `hook`, `bridge`, `qwen`, `openclaw`, `imageEdit`, and `errors` fields.

- [x] **Step 1: Write failing tests** for status schema, Start clearing the marker and invoking the existing startup flow, and Stop writing the marker before stopping components.
- [x] **Step 2: Run** `powershell.exe -NoProfile -ExecutionPolicy Bypass -File outputs/WeChatAI/tests/test_manage_bot_stack.ps1`; confirm it fails on the absent script/actions.
- [x] **Step 3: Implement** Start/Stop/Status with process identity checks. Stop only the bridge/watchdog, the configured `C:\llama\...\llama-server.exe`, the `openclaw-gateway.service` in distro `OpenClawGateway`, and the configured dedicated ComfyUI command line. Do not terminate an unknown listener based only on its port. Keep the marker set and return component-specific errors if any stop fails.
- [x] **Step 4: Re-run** the lifecycle test using temporary runtime state and mocked process/service operations; require action order and process-scoping assertions to pass.

### Task 4: Build the visible no-console control executable

**Files:**
- Create: `outputs/WeChatAI/controller/BotSwitch.cs`
- Create: `outputs/WeChatAI/build_bot_switch.ps1`
- Test: `outputs/WeChatAI/tests/test_bot_switch_build.ps1`

**Interfaces:**
- Executable: `outputs/WeChatAI/BotSwitch.exe`
- The window has one Start/Stop toggle and a status area for every status field; refresh status on a timer and after actions.
- The executable invokes `manage_bot_stack.ps1` with `-WindowStyle Hidden`, parses its JSON, and never opens a console window.

- [x] **Step 1: Write failing build/UI contract checks** for the expected source, executable path, toggle labels, hidden child-process launch, and status rendering.
- [x] **Step 2: Run** `powershell.exe -NoProfile -ExecutionPolicy Bypass -File outputs/WeChatAI/tests/test_bot_switch_build.ps1`; confirm it fails because the source/build script do not exist.
- [x] **Step 3: Implement** a compact WinForms window and build script using the installed .NET Framework compiler, referencing `System.Windows.Forms` and `System.Drawing`; disable the toggle during an action and show any lifecycle error in the window.
- [x] **Step 4: Re-run** the build test; require the executable to exist and be a Windows GUI-subsystem executable with no console host.
- [x] **Step 5: Launch the executable once** to verify the window renders, refreshes status, and exits cleanly without a terminal window.

### Task 5: Launch the control window at sign-in and document use

**Files:**
- Create: `outputs/WeChatAI/install_bot_switch_autostart.ps1`
- Modify: `outputs/WeChatAI/README.md`
- Test: `outputs/WeChatAI/tests/test_bot_switch_autostart.ps1`

**Interfaces:**
- A separate per-user logon task named `Codex-WeChat-AI-Control` launches `BotSwitch.exe` interactively.
- Existing task `Codex-WeChat-AI-Bridge` stays enabled and keeps its current hidden backend action and one-minute recovery trigger.

- [x] **Step 1: Write failing registration tests** for the separate GUI task and preservation of the existing backend task's state, action, and triggers.
- [x] **Step 2: Run** `powershell.exe -NoProfile -ExecutionPolicy Bypass -File outputs/WeChatAI/tests/test_bot_switch_autostart.ps1`; confirm the registration contract is not present.
- [x] **Step 3: Implement** idempotent registration for the GUI task and update README instructions for the visible toggle, boot-scoped Stop, manual WeChat login, and task removal.
- [x] **Step 4: Re-run** the test against the active Task Scheduler and require the existing backend task contract to remain unchanged.
- [x] **Step 5: Run all WeChatAI PowerShell tests** and perform read-only checks of the registered task definitions before enabling the new GUI task.

## Plan self-review

- Every design requirement maps to Tasks 1–5; no Docker behavior or unrelated processes are included.
- State, guard, lifecycle, GUI, and autostart tasks use consistent module and script paths.
- Failure handling, boot transitions, manual WeChat login, no-console behavior, and unrelated-process protection are covered by named tests.
- Tests avoid stopping the user's active bot stack; live service shutdown and restart are not part of automated verification.
- The existing `test_update_block.ps1` fails in `block_wechat_updates.ps1` while probing a protected profile path; the remaining PowerShell tests pass. This pre-existing updater test and implementation were left untouched.
