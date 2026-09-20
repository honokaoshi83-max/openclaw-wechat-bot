from __future__ import annotations

import html
import json
import re
import urllib.request
import urllib.parse
from pathlib import Path


class ImageUnavailable(RuntimeError):
    pass


def is_image_message(message: dict) -> bool:
    return str(message.get("type") or "") == "图片" or int(message.get("local_type") or 0) == 3


def _base_message_type(message: dict) -> int:
    value = int(message.get("local_type") or message.get("type_code") or 0)
    return value & 0xFFFFFFFF


def is_emoji_message(message: dict) -> bool:
    return (str(message.get("type") or "") == "动画表情"
            or _base_message_type(message) in {47, 11000})


def is_visual_message(message: dict) -> bool:
    return is_image_message(message) or is_emoji_message(message)


def _decode_with_hook(downloader, peer: str, local_id: int, temp_dir: Path,
                      hook_url: str) -> Path:
    row = downloader.db.get_message_row(peer, int(local_id), local_type=3)
    if not row:
        raise ImageUnavailable("找不到图片消息记录")
    digest = downloader._img_md5(row)
    if not digest:
        raise ImageUnavailable("图片消息中没有缓存标识")
    dat_path = downloader._find_dat(peer, digest, row["create_time"])
    if not dat_path:
        dat_path = downloader._find_dat(peer, digest, row["create_time"], thumbnail=True)
    if not dat_path:
        raise ImageUnavailable("微信图片缓存尚未落盘")
    output = temp_dir / f"{peer}_{local_id}_hook.jpg"
    payload = json.dumps({"src_path": dat_path, "dst_path": str(output)}).encode("utf-8")
    request = urllib.request.Request(
        hook_url.rstrip("/") + "/Decode_Pic",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            result = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise ImageUnavailable(f"Hook 图片解码请求失败：{exc}") from exc
    if int(result.get("ret", -1)) != 0 or not output.is_file() or output.stat().st_size <= 0:
        raise ImageUnavailable(f"Hook 图片解码失败：{result}")
    if output.read_bytes()[:4] == b"wxgf":
        jpg = downloader._wxgf_to_jpg(output.read_bytes())
        if not jpg:
            raise ImageUnavailable("微信图片为 wxgf 格式且无法转换")
        output.write_bytes(jpg)
    return output.resolve()


def extract_image(db, peer: str, local_id: int, temp_dir: Path, downloader_factory=None,
                  hook_url: str | None = None, hook_decoder=None) -> Path:
    temp_dir.mkdir(parents=True, exist_ok=True)
    if downloader_factory is None:
        from wechatauto.media import MediaDownloader
        downloader_factory = lambda source_db, save_dir: MediaDownloader(
            source_db, save_dir=save_dir
        )

    try:
        downloader = downloader_factory(db, str(temp_dir))
        # A full process-memory scan can block the message loop for several
        # minutes. Normal bot replies must stay responsive: use an already
        # derived/persisted key and fail fast when the key is not available.
        if hasattr(downloader, "_scan_aes_key"):
            downloader._scan_aes_key = lambda *args, **kwargs: None
        try:
            path = downloader.download_image(peer, int(local_id))
        except RuntimeError:
            decoder = hook_decoder or _decode_with_hook
            if not hook_url and hook_decoder is None:
                raise
            return Path(decoder(downloader, peer, int(local_id), temp_dir, hook_url)).resolve()
    except (RuntimeError, ValueError) as exc:
        raise ImageUnavailable(str(exc)) from exc
    if not path or not Path(path).is_file() or Path(path).stat().st_size <= 0:
        raise ImageUnavailable("微信图片缓存不存在或无法解密")
    return Path(path).resolve()


def _write_visual_bytes(data: bytes, output_base: Path, downloader=None) -> Path:
    if data[:3] == b"\xff\xd8\xff":
        suffix = ".jpg"
    elif data[:4] == b"\x89PNG":
        suffix = ".png"
    elif data[:3] == b"GIF":
        suffix = ".gif"
    elif data[:4] == b"wxgf":
        converted = downloader._wxgf_to_jpg(data) if downloader is not None else None
        if not converted:
            raise ImageUnavailable("微信表情是暂不支持的 wxgf/WXAM 动画格式")
        data, suffix = converted, ".jpg"
    else:
        raise ImageUnavailable("无法识别微信表情缓存格式")
    output = output_base.with_suffix(suffix)
    output.write_bytes(data)
    return output.resolve()


def _download_emoji_bytes(url: str, max_bytes: int = 12 * 1024 * 1024) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            data = response.read(max_bytes + 1)
    except Exception as exc:
        raise ImageUnavailable(f"微信表情资源下载失败：{exc}") from exc
    if len(data) > max_bytes:
        raise ImageUnavailable("微信表情资源超过识别大小限制")
    return data


def extract_emoji_from_content(content: str, output_base: Path,
                               fetch_bytes=None) -> Path:
    """Fetch the official WeChat CDN asset referenced by decompressed emoji XML."""
    match = re.search(r'cdnurl\s*=\s*"([^"]+)"', str(content or ""), re.I)
    if not match:
        raise ImageUnavailable("动画表情消息没有可用资源地址")
    url = html.unescape(match.group(1)).strip()
    parsed = urllib.parse.urlparse(url)
    hostname = (parsed.hostname or "").lower()
    trusted = (hostname.endswith(".qq.com") or hostname == "qq.com"
               or hostname.endswith(".qpic.cn") or hostname == "qpic.cn"
               or hostname.endswith(".weixin.qq.com") or hostname == "weixin.qq.com")
    if parsed.scheme not in {"http", "https"} or not trusted:
        raise ImageUnavailable("动画表情资源地址不受信任")
    fetch = fetch_bytes or _download_emoji_bytes
    data = fetch(url)
    return _write_visual_bytes(data, Path(output_base))


def extract_emoji(db, peer: str, local_id: int, temp_dir: Path,
                  downloader_factory=None, message_content: str = "") -> Path:
    """Extract an animation sticker from the local cache without opening WeChat."""
    temp_dir.mkdir(parents=True, exist_ok=True)
    if downloader_factory is None:
        from wechatauto.media import MediaDownloader
        downloader_factory = lambda source_db, save_dir: MediaDownloader(
            source_db, save_dir=save_dir
        )
    downloader = downloader_factory(db, str(temp_dir))
    if hasattr(downloader, "_scan_aes_key"):
        downloader._scan_aes_key = lambda *args, **kwargs: None
    row = None
    for message_type in (47, 11000):
        row = db.get_message_row(peer, int(local_id), local_type=message_type)
        if row:
            break
    if not row:
        row = db.get_message_row(peer, int(local_id))
    if not row:
        raise ImageUnavailable("找不到动画表情消息记录")
    digest = downloader._img_md5(row)
    if not digest:
        match = re.search(r'\bmd5\s*=\s*"([0-9a-fA-F]{32})"',
                          str(message_content or ""), re.I)
        digest = match.group(1).lower() if match else None
    if not digest:
        return extract_emoji_from_content(
            message_content, temp_dir / f"{peer}_{local_id}_emoji")
    cache_path = downloader._find_dat(peer, digest, int(row.get("create_time") or 0))
    if not cache_path:
        cache_path = downloader._find_dat(
            peer, digest, int(row.get("create_time") or 0), thumbnail=True)
    if not cache_path:
        account_dir = Path(getattr(getattr(downloader, "db", None), "account_dir", ""))
        if account_dir.is_dir():
            candidates = list(account_dir.glob(f"cache/*/Emoticon/*/{digest}"))
            cache_path = str(candidates[0]) if candidates else None
    if cache_path:
        try:
            raw = Path(cache_path).read_bytes()
            if raw[:4] in {b"wxgf", b"\x89PNG"} or raw[:3] in {b"GIF", b"\xff\xd8\xff"}:
                data = raw
            else:
                data = downloader.decrypt_image(cache_path)
            return _write_visual_bytes(
                data, temp_dir / f"{peer}_{local_id}_emoji", downloader)
        except (RuntimeError, ValueError, OSError, ImageUnavailable):
            pass
    return extract_emoji_from_content(
        message_content, temp_dir / f"{peer}_{local_id}_emoji")


def extract_visual(db, peer: str, local_id: int, message: dict, temp_dir: Path,
                   downloader_factory=None, hook_url: str | None = None,
                   hook_decoder=None) -> Path:
    if is_emoji_message(message):
        return extract_emoji(
            db, peer, local_id, temp_dir, downloader_factory,
            message_content=str(message.get("content") or ""),
        )
    return extract_image(db, peer, local_id, temp_dir, downloader_factory,
                         hook_url=hook_url, hook_decoder=hook_decoder)


def prepare_vision_image(path: Path, temp_dir: Path) -> Path:
    """Turn an animated GIF/WebP into a compact three-frame PNG contact sheet."""
    from PIL import Image

    source = Path(path).resolve()
    try:
        with Image.open(source) as image:
            frame_count = int(getattr(image, "n_frames", 1))
            if frame_count <= 1:
                return source
            # A single representative frame keeps sticker recognition fast.
            # The caller can opt into a three-frame contact sheet when needed.
            indices = [0] if frame_count > 1 else [0]
            frames = []
            for index in indices:
                image.seek(index)
                frames.append(image.convert("RGB").copy())
    except (OSError, ValueError) as exc:
        raise ImageUnavailable(f"动画表情解码失败：{exc}") from exc
    width = max(frame.width for frame in frames)
    height = max(frame.height for frame in frames)
    sheet = Image.new("RGB", (width * len(frames), height), "white")
    for index, frame in enumerate(frames):
        sheet.paste(frame, (index * width, 0))
    output = Path(temp_dir).resolve() / f"{source.stem}_frames.png"
    sheet.save(output, format="PNG")
    return output


def cleanup_image(path: Path, temp_dir: Path) -> None:
    target = Path(path).resolve()
    root = Path(temp_dir).resolve()
    if root not in target.parents:
        raise ValueError("refusing to delete a file outside the vision temp directory")
    if target.is_file():
        target.unlink()
