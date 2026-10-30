# WeChat Bot Master Switch Design

## Goal

Provide a visible, no-console desktop control that lets the user start or stop the entire WeChat bot stack, while retaining its current automatic startup at Windows sign-in and manual WeChat login.

## User-visible behavior

- A small control window opens at user sign-in and displays whether the bot stack is running, stopped, or starting/stopping.
- One clearly labeled toggle starts or stops the bot stack.
- The existing backend auto-start and one-minute recovery task remain enabled. An intentional stop applies for the current Windows boot only; the next boot starts the bot as it does today.
- The WeChat client is never launched or closed by the controller. The user continues to log in manually; the bridge waits for the AI WeChat Hook to become available.
- The controller and all scripts it launches run without a console window and without elevation.

## Components covered

The master switch controls the project's WeChat bridge, its watchdog, the local llama.cpp/Qwen server on port 8080, the OpenClaw Gateway user service in WSL, and the project's on-demand ComfyUI image-edit process when it is running. It must identify processes and services by the project's known executable paths, command lines, ports, or service identity; it must not terminate unrelated Python, Node, WSL, or Docker workloads. Docker Desktop itself and unrelated containers are outside scope.

## Startup and stop coordination

- Add a boot-scoped disabled marker under `work/wechat_ai_bridge_state`. It records the current Windows boot identity.
- `startup_backend.ps1` and `wait_for_ai_wechat.ps1` check the marker before starting or recovering services. If the marker matches this boot, they keep the stack stopped. A marker from an earlier boot is ignored so existing auto-start works on the next boot.
- The controller's Stop action writes the marker before stopping the watchdog and bridge, then stops only the covered backend services. Writing the marker first prevents the repeating startup task from racing and restarting a component during shutdown.
- The Start action removes the current-boot marker and invokes the existing startup/supervision flow. Bridge startup remains conditional on the AI WeChat Hook being available.
- If one stop action fails, the UI reports that component and leaves the marker in place so the watchdog cannot restart the stack unexpectedly.

## Implementation shape

- Build a small Windows GUI executable with a single main window and no terminal host. It shows the master toggle and concise component status.
- Keep lifecycle operations in focused PowerShell scripts so the existing scheduled task and controller use the same start/stop rules.
- Register a separate per-user logon task for the GUI. Do not replace, disable, or weaken the existing backend autostart and self-check task.
- Keep user settings, credentials, model configuration, WeChat data, and conversation memory untouched.

## Verification

- Unit or script-level checks cover current-boot marker matching, stale-marker behavior after reboot, and start/stop idempotency.
- Verify Stop prevents both the watchdog and the one-minute scheduled task from restarting project components.
- Verify Start restores the existing startup flow and does not open WeChat.
- Verify status correctly distinguishes a running backend with WeChat logged out (bridge waiting) from a fully connected bridge.
- Build and launch the GUI executable without a console window; test the toggle against the local stack and confirm unrelated processes are not affected.

## Non-goals

- Changing model routing, reply behavior, API configuration, or WeChat login behavior.
- Starting, stopping, or configuring Docker Desktop or unrelated containers.
- Making an intentional stop persist across a Windows reboot.
