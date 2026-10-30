# Qwen-Image-2.1 UC Q4_K_M 本地部署

部署日期：2026-10-06。

## 本地位置

部署目录：`D:\AI\QwenImage21`。

使用说明：`D:\AI\QwenImage21\README.md`。

模型：`D:\AI\QwenImage21\models`。

生成结果：`D:\AI\QwenImage21\output`。

## 已安装组件

| 组件 | 文件 | 大小 |
| --- | --- | --- |
| 生成模型 | qwen-image-2.1-UC-Q4_K_M.gguf | 4.60 GB |
| 文本编码器 | Qwen3VL-8B-Instruct-Q4_K_M.gguf | 5.03 GB |
| 编辑视觉编码器 | mmproj-Qwen3VL-8B-Instruct-F16.gguf | 1.16 GB |
| VAE | qwen_image_2.1_vae_bf16.safetensors | 0.68 GB |

全部模型已按 Hugging Face LFS SHA256 校验，固定下载版本及摘要见部署目录的 manifest.json。

运行程序：stable-diffusion.cpp `master-929-3f8527a`，Windows Vulkan。
压缩包 SHA256 与 GitHub release digest 相符：`60e6850d650417409f18c2170ab5e27335db96da70cd3d1ad1e930bcffc2fd35`。

## 显卡与共用方式

实测识别到 AMD Radeon RX 7700 XT，使用 Vulkan0。
文字编码在 CPU 上运行，图像扩散与 VAE 使用独立显卡，启用内存卸载、Flash Attention 和 VAE 分块。

生成脚本使用 Bot 已有的 GPU 锁，在出图期间暂停本地千问文字后端。
完成、失败或超时后会释放锁并触发 `Codex-WeChat-AI-Bridge` 计划任务恢复文字后端。
本次部署未开放新的微信绘图指令，现有 image_edit_enabled=false 设置保留。

## 运行

```powershell
python D:\AI\QwenImage21\generate.py "A cute blue whale on a sunny beach, colorful illustration"
```

```powershell
python D:\AI\QwenImage21\generate.py "Change the background to a sunny beach" --reference "D:\path\input.png"
```

默认 512×512、20 步。可通过 --prompt-file 输入 UTF-8 中文描述。
本地任务日志、输出 JSON 包含耗时、参数及使用文件，便于复现。

## 实测结果

- 文生图成功：512×512，20 步，种子 42，完整脚本耗时 48.45 秒。
- 输出 deployment-test.png，视觉检查为蓝鲸插画，符合提示中的主要内容。
- 参考图编辑成功：512×512，20 步，种子 42，完整脚本耗时 72.92 秒。
- 输出 deployment-edit-test.png，视觉检查确认鲸鱼变为橙色，构图与海水背景基本保留。
- 测试结束后千问 /health 返回 status=ok，/v1/models 返回原有 9B 文字模型；GPU 锁已释放，图片进程已退出。

## 限制

“Uncensored”是作者命名，作者说明使用原版基础权重；本次验证只测试正常插画和修改功能，没有验证所有敏感提示词的输出行为。
仍适用 Qwen Research License。单次测试耗时不代表所有分辨率、复杂提示或参考图数量的表现。

## 来源

- https://huggingface.co/abenzerps/Qwen-Image-2.1-Uncensored-GGUF
- https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct-GGUF
- https://github.com/leejet/stable-diffusion.cpp/blob/master/docs/qwen_image_2.1.md
