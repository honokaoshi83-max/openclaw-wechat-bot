"""Minimal local ComfyUI API client for an InstructPix2Pix edit workflow."""

from __future__ import annotations

import copy
import json
import mimetypes
import os
import random
import re
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path


class ImageEditError(RuntimeError):
    pass


class ComfyImageEditor:
    def __init__(self, base_url: str = "http://127.0.0.1:8188", *,
                 workflow_path: Path | None = None, input_dir: Path | None = None,
                 output_dir: Path | None = None, timeout_seconds: float = 300.0,
                 poll_interval: float = 0.5, max_input_bytes: int = 12 * 1024 * 1024,
                 max_output_bytes: int = 30 * 1024 * 1024, max_side: int = 768):
        parsed = urllib.parse.urlsplit(str(base_url).rstrip("/"))
        if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
                or parsed.username or parsed.password or parsed.path not in {"", "/"}
                or parsed.query or parsed.fragment):
            raise ImageEditError("图片编辑服务只能使用本机 HTTP 地址")
        self.base_url = str(base_url).rstrip("/")
        self.workflow_path = Path(workflow_path or Path(__file__).parent / "workflows" /
                                  "instruct_pix2pix_api.json")
        self.input_dir = Path(input_dir).resolve() if input_dir else None
        self.output_dir = Path(output_dir).resolve() if output_dir else None
        self.timeout_seconds = max(1.0, float(timeout_seconds))
        self.poll_interval = max(0.01, float(poll_interval))
        self.max_input_bytes = max(1024, int(max_input_bytes))
        self.max_output_bytes = max(1024, int(max_output_bytes))
        self.max_side = max(256, int(max_side))

    def _request(self, path: str, *, data: bytes | None = None,
                 content_type: str | None = None, timeout: float = 30.0) -> bytes:
        headers = {}
        if content_type:
            headers["Content-Type"] = content_type
        request = urllib.request.Request(self.base_url + path, data=data,
                                         headers=headers, method="POST" if data is not None else "GET")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read(self.max_output_bytes + 1)
        except urllib.error.HTTPError as exc:
            try:
                detail = exc.read(2048).decode("utf-8", errors="replace").strip()
            except OSError:
                detail = ""
            raise ImageEditError(f"本机图片编辑服务返回 HTTP {exc.code}：{detail or exc.reason}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ImageEditError(f"本机图片编辑服务请求失败：{exc}") from exc

    def _prepare_image(self, source: Path) -> tuple[Path, bool]:
        source = Path(source).resolve()
        if not source.is_file() or source.stat().st_size <= 0:
            raise ImageEditError("找不到待修改图片")
        if source.stat().st_size > self.max_input_bytes:
            raise ImageEditError("图片超过本地修图大小限制")
        from PIL import Image, ImageOps
        try:
            with Image.open(source) as opened:
                image = ImageOps.exif_transpose(opened).convert("RGB")
        except (OSError, ValueError) as exc:
            raise ImageEditError(f"图片格式无法读取：{exc}") from exc
        if max(image.size) > self.max_side:
            scale = self.max_side / max(image.size)
        else:
            scale = 1.0
        size = (max(64, round(image.width * scale / 8) * 8),
                max(64, round(image.height * scale / 8) * 8))
        if image.size != size:
            image = image.resize(size, Image.Resampling.LANCZOS)
        handle, name = tempfile.mkstemp(prefix="wechat_edit_", suffix=".png")
        os.close(handle)
        prepared = Path(name)
        image.save(prepared, format="PNG", optimize=True)
        return prepared, True

    def _upload(self, path: Path, filename: str) -> dict:
        boundary = "----CodexImageEdit" + uuid.uuid4().hex
        body = (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; "
            f"filename=\"{filename}\"\r\nContent-Type: image/png\r\n\r\n"
        ).encode("ascii") + path.read_bytes() + (
            f"\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"type\"\r\n\r\ninput\r\n"
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"overwrite\"\r\n\r\nfalse\r\n"
            f"--{boundary}--\r\n"
        ).encode("ascii")
        try:
            result = json.loads(self._request("/upload/image", data=body,
                                              content_type=f"multipart/form-data; boundary={boundary}"))
            if not isinstance(result, dict) or not result.get("name"):
                raise ValueError("missing uploaded image name")
            return result
        except (ValueError, json.JSONDecodeError) as exc:
            raise ImageEditError(f"图片上传到本机工作流失败：{exc}") from exc

    def _workflow(self, image_name: str, instruction: str, seed: int, prefix: str) -> dict:
        try:
            workflow = json.loads(self.workflow_path.read_text(encoding="utf-8"))
            workflow = copy.deepcopy(workflow)
            workflow["2"]["inputs"]["image"] = image_name
            workflow["3"]["inputs"]["text"] = instruction
            workflow["6"]["inputs"]["seed"] = int(seed)
            workflow["8"]["inputs"]["filename_prefix"] = prefix
            return workflow
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise ImageEditError(f"本地图片工作流配置无效：{exc}") from exc

    def _submit(self, workflow: dict) -> str:
        payload = json.dumps({"prompt": workflow}, ensure_ascii=False).encode("utf-8")
        try:
            result = json.loads(self._request("/prompt", data=payload,
                                              content_type="application/json", timeout=30))
            prompt_id = str(result.get("prompt_id") or "")
            if not prompt_id or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", prompt_id):
                raise ValueError("missing or invalid prompt id")
            return prompt_id
        except (ValueError, json.JSONDecodeError, AttributeError) as exc:
            raise ImageEditError(f"本机修图任务提交失败：{exc}") from exc

    def _wait_for_image(self, prompt_id: str) -> dict:
        deadline = time.monotonic() + self.timeout_seconds
        while time.monotonic() < deadline:
            quoted_id = urllib.parse.quote(prompt_id, safe="")
            try:
                result = json.loads(self._request(f"/history/{quoted_id}", timeout=10))
            except (json.JSONDecodeError, ValueError) as exc:
                raise ImageEditError(f"本机修图进度响应无效：{exc}") from exc
            entry = result.get(prompt_id) if isinstance(result, dict) else None
            if entry:
                status = entry.get("status") or {}
                if status.get("status_str") == "error" or status.get("completed") is False:
                    raise ImageEditError("本机模型执行图片修改失败")
                for nodes in (entry.get("outputs") or {}).values():
                    images = nodes.get("images") if isinstance(nodes, dict) else None
                    if images:
                        image = images[0]
                        if (not isinstance(image, dict) or image.get("type") != "output"
                                or not image.get("filename")):
                            raise ImageEditError("本机模型没有返回有效图片")
                        return image
            time.sleep(self.poll_interval)
        raise ImageEditError("本机修图超时，请稍后重试")

    def _download(self, image: dict, target: Path) -> None:
        filename = str(image.get("filename") or "")
        subfolder = str(image.get("subfolder") or "")
        if (Path(filename).name != filename or "\\" in filename or ".." in filename
                or Path(subfolder).is_absolute() or ".." in Path(subfolder).parts
                or "\\" in subfolder):
            raise ImageEditError("本机模型返回了不安全的图片路径")
        query = urllib.parse.urlencode({"filename": filename, "subfolder": subfolder,
                                        "type": "output"})
        data = self._request(f"/view?{query}", timeout=60)
        if not data or len(data) > self.max_output_bytes:
            raise ImageEditError("修图结果为空或超过大小限制")
        from PIL import Image
        try:
            with Image.open(__import__("io").BytesIO(data)) as image_data:
                image_data.verify()
        except (OSError, ValueError) as exc:
            raise ImageEditError(f"修图结果不是有效图片：{exc}") from exc
        target = Path(target).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    @staticmethod
    def _remove_from(directory: Path | None, name: str, subfolder: str = "") -> None:
        """Remove only generated Comfy files that resolve inside our configured dirs."""
        if directory is None or not name:
            return
        base = directory.resolve()
        candidate = (base / subfolder / name).resolve()
        try:
            candidate.relative_to(base)
        except ValueError:
            return
        if candidate.is_file():
            candidate.unlink(missing_ok=True)

    def edit(self, source: Path, instruction: str, target: Path) -> Path:
        instruction = str(instruction or "").strip()
        if not instruction:
            raise ImageEditError("还没有收到图片修改要求")
        if len(instruction) > 2000:
            raise ImageEditError("图片修改要求过长")
        prepared, remove_prepared = self._prepare_image(Path(source))
        upload_name = f"wechat_edit_{uuid.uuid4().hex}.png"
        prefix = f"wechat_edit_{uuid.uuid4().hex}"
        uploaded = None
        output = None
        try:
            uploaded = self._upload(prepared, upload_name)
            image_name = str(uploaded["name"])
            if Path(image_name).name != image_name or ".." in image_name or "\\" in image_name:
                raise ImageEditError("本机工作流返回了不安全的上传图片名称")
            workflow = self._workflow(image_name, instruction, random.SystemRandom().randrange(2**63), prefix)
            prompt_id = self._submit(workflow)
            output = self._wait_for_image(prompt_id)
            self._download(output, Path(target))
            return Path(target).resolve()
        finally:
            if uploaded:
                self._remove_from(self.input_dir, str(uploaded.get("name") or ""),
                                  str(uploaded.get("subfolder") or ""))
            if output:
                self._remove_from(self.output_dir, str(output.get("filename") or ""),
                                  str(output.get("subfolder") or ""))
            if remove_prepared:
                prepared.unlink(missing_ok=True)
