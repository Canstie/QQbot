from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import html
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from uuid import uuid4

from nonebot import logger, on_command
from nonebot.adapters.onebot.v11 import Bot, MessageEvent
from nonebot.matcher import Matcher

from qq_personal_bot.menu_recipes import resolve_local_source
from qq_personal_bot.runtime import get_settings, get_store

download = on_command("download", priority=5, block=True)

_CHINA_TZ = timezone(timedelta(hours=8))
_MAX_IMAGE_BYTES = 50 * 1024 * 1024
_DOWNLOAD_CONCURRENCY = 4
_HASH_NAME_RE = re.compile(r"^[0-9a-f]{64}$")
_CQ_SEGMENT_RE = re.compile(r"\[CQ:(image|forward),([^\]]+)\]")
_DIRECT_SOURCE_SCHEMES = frozenset({"http", "https", "file"})
_IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".gif", ".webp"})


@dataclass(frozen=True)
class DownloadStats:
    total: int
    succeeded: int
    skipped: int
    failed: int
    directory: Path


@dataclass
class _CollectedImages:
    images: list[dict[str, Any]]
    errors: list[str]


class DownloadInputError(ValueError):
    pass


async def _handle_download(matcher: Matcher, bot: Bot, event: MessageEvent) -> None:
    if not get_store().is_admin(int(event.user_id)):
        await matcher.finish("权限不足：仅管理员可使用 /download。")

    try:
        collected = await _collect_referenced_images(bot, event)
    except DownloadInputError as exc:
        await matcher.finish(str(exc))
        return

    if not collected.images and collected.errors:
        await matcher.finish("读取引用的聊天记录失败，请确认记录仍可访问后重试。")
        return

    sources = await asyncio.gather(
        *(_resolve_image_source(bot, image) for image in collected.images)
    )
    day = datetime.now(_CHINA_TZ).strftime("%Y%m%d")
    stats = await _download_image_sources(
        list(sources),
        root=get_settings().download_image_dir,
        day=day,
    )
    await matcher.finish(_format_download_result(stats))


@download.handle()
async def handle_download(matcher: Matcher, bot: Bot, event: MessageEvent) -> None:
    await _handle_download(matcher, bot, event)


async def _collect_referenced_images(bot: Bot, event: Any) -> _CollectedImages:
    embedded_message, reply_id = _referenced_message(event)
    if embedded_message is None and reply_id is None:
        direct_message = getattr(event, "message", None)
        if _contains_segment_type(direct_message, "forward"):
            embedded_message = direct_message
        else:
            raise DownloadInputError("请引用一条聊天记录后发送 /download。")

    images: list[dict[str, Any]] = []
    forward_ids: list[str] = []
    errors: list[str] = []
    if embedded_message is not None:
        _scan_message_payload(embedded_message, images, forward_ids)

    if not images and not forward_ids and reply_id is not None:
        try:
            payload = await bot.call_api("get_msg", message_id=reply_id)
        except Exception as exc:
            logger.warning(f"Download: get_msg failed for {reply_id}: {exc}")
            errors.append(str(exc))
        else:
            _scan_message_payload(payload, images, forward_ids)

    seen_forward_ids: set[str] = set()
    cursor = 0
    while cursor < len(forward_ids):
        forward_id = forward_ids[cursor]
        cursor += 1
        if not forward_id or forward_id in seen_forward_ids:
            continue
        seen_forward_ids.add(forward_id)
        try:
            payload = await bot.call_api("get_forward_msg", id=forward_id)
        except Exception as exc:
            logger.warning(f"Download: get_forward_msg failed for {forward_id}: {exc}")
            errors.append(str(exc))
            continue
        _scan_message_payload(payload, images, forward_ids)

    return _CollectedImages(images=images, errors=errors)


def _referenced_message(event: Any) -> tuple[Any | None, int | str | None]:
    reply = getattr(event, "reply", None)
    if reply is not None:
        reply_id = getattr(reply, "message_id", None) or getattr(reply, "real_id", None)
        message = getattr(reply, "message", None)
        if message is not None or reply_id is not None:
            return message, reply_id

    for segment in _as_segments(getattr(event, "message", None)):
        if segment.get("type") != "reply":
            continue
        data = segment.get("data")
        if not isinstance(data, Mapping):
            continue
        reply_id = _first_value(data, "id", "message_id", "real_id")
        return data.get("message"), reply_id
    return None, None


def _contains_segment_type(value: Any, segment_type: str) -> bool:
    return any(segment.get("type") == segment_type for segment in _as_segments(value))


def _scan_message_payload(
    value: Any,
    images: list[dict[str, Any]],
    forward_ids: list[str],
) -> None:
    if value is None:
        return
    if isinstance(value, str):
        _scan_cq_message(value, images, forward_ids)
        return
    if isinstance(value, Mapping):
        segment_type = str(value.get("type") or "")
        data = value.get("data")
        if segment_type:
            normalized_data = dict(data) if isinstance(data, Mapping) else {}
            if segment_type == "image":
                images.append(normalized_data)
                return
            if segment_type == "forward":
                forward_id = _first_value(normalized_data, "id", "resid")
                if forward_id is not None:
                    forward_ids.append(str(forward_id))
                return
            if segment_type == "node":
                for key in ("content", "message", "messages"):
                    _scan_message_payload(normalized_data.get(key), images, forward_ids)
                return
        for key in ("message", "messages", "content"):
            _scan_message_payload(value.get(key), images, forward_ids)
        return

    if isinstance(value, (bytes, bytearray)):
        return
    try:
        iterator = iter(value)
    except TypeError:
        return
    for item in iterator:
        if isinstance(item, Mapping):
            _scan_message_payload(item, images, forward_ids)
            continue
        segment_type = getattr(item, "type", None)
        data = getattr(item, "data", None)
        if segment_type is not None:
            _scan_message_payload(
                {"type": segment_type, "data": dict(data or {})},
                images,
                forward_ids,
            )


def _scan_cq_message(
    message: str,
    images: list[dict[str, Any]],
    forward_ids: list[str],
) -> None:
    for match in _CQ_SEGMENT_RE.finditer(message):
        segment_type = match.group(1)
        data: dict[str, str] = {}
        for item in match.group(2).split(","):
            key, separator, value = item.partition("=")
            if separator:
                data[key.strip()] = html.unescape(value.strip())
        if segment_type == "image":
            images.append(data)
        else:
            forward_id = _first_value(data, "id", "resid")
            if forward_id is not None:
                forward_ids.append(str(forward_id))


def _as_segments(message: Any) -> list[dict[str, Any]]:
    if message is None or isinstance(message, (str, bytes, bytearray)):
        return []
    result: list[dict[str, Any]] = []
    try:
        iterator = iter(message)
    except TypeError:
        return result
    for segment in iterator:
        if isinstance(segment, Mapping):
            result.append({"type": segment.get("type"), "data": segment.get("data", {})})
            continue
        segment_type = getattr(segment, "type", None)
        if segment_type is not None:
            result.append(
                {"type": segment_type, "data": dict(getattr(segment, "data", {}) or {})}
            )
    return result


def _first_value(data: Mapping[str, Any], *keys: str) -> Any | None:
    for key in keys:
        value = data.get(key)
        if value is not None and str(value).strip():
            return value
    return None


async def _resolve_image_source(bot: Bot, image: Mapping[str, Any]) -> str | None:
    for key in ("url", "path", "file"):
        source = _normalized_source(image.get(key))
        if source and _is_direct_image_source(source):
            return source

    image_file = _first_value(image, "file", "file_id")
    if image_file is None:
        return None
    try:
        payload = await bot.call_api("get_image", file=image_file)
    except Exception as exc:
        logger.warning(f"Download: get_image failed for {image_file}: {exc}")
        return None

    if isinstance(payload, Mapping):
        for key in ("url", "path", "file"):
            source = _normalized_source(payload.get(key))
            if source:
                return source
    return None


def _normalized_source(value: Any) -> str:
    return html.unescape(str(value or "").strip())


def _is_direct_image_source(source: str) -> bool:
    if source.startswith("base64://"):
        return True
    parsed = urlparse(source)
    if parsed.scheme in _DIRECT_SOURCE_SCHEMES:
        return True
    return Path(source).is_absolute()


async def _download_image_sources(
    sources: list[str | None],
    *,
    root: Path,
    day: str,
) -> DownloadStats:
    root = Path(root)
    target_directory = root / day
    await asyncio.to_thread(target_directory.mkdir, parents=True, exist_ok=True)
    existing_hashes = await asyncio.to_thread(_load_existing_hashes, root)
    semaphore = asyncio.Semaphore(_DOWNLOAD_CONCURRENCY)
    save_lock = asyncio.Lock()

    async def process(source: str | None) -> str:
        if not source:
            return "failed"
        try:
            async with semaphore:
                body = await asyncio.to_thread(_read_image_source, source)
            suffix = _image_suffix(body)
            if suffix is None:
                return "failed"
            digest = hashlib.sha256(body).hexdigest()
            async with save_lock:
                if digest in existing_hashes or _hash_file_exists(root, digest):
                    existing_hashes.add(digest)
                    return "skipped"
                target_path = target_directory / f"{digest}{suffix}"
                await asyncio.to_thread(_write_image_atomically, target_path, body)
                existing_hashes.add(digest)
                return "succeeded"
        except Exception as exc:
            logger.warning(f"Download: image download failed for {source}: {exc}")
            return "failed"

    results = await asyncio.gather(*(process(source) for source in sources))
    return DownloadStats(
        total=len(sources),
        succeeded=results.count("succeeded"),
        skipped=results.count("skipped"),
        failed=results.count("failed"),
        directory=target_directory,
    )


def _read_image_source(source: str) -> bytes:
    if source.startswith("base64://"):
        try:
            body = base64.b64decode(source.removeprefix("base64://"), validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("invalid base64 image") from exc
        return _bounded_image(body)

    parsed = urlparse(source)
    if parsed.scheme in {"http", "https"}:
        request = Request(source, headers={"User-Agent": "qq-personal-bot/0.1"})
        with urlopen(request, timeout=30) as response:
            return _bounded_image(response.read(_MAX_IMAGE_BYTES + 1))

    source_path = resolve_local_source(source, parsed=parsed).resolve(strict=True)
    with source_path.open("rb") as image_file:
        return _bounded_image(image_file.read(_MAX_IMAGE_BYTES + 1))


def _bounded_image(body: bytes) -> bytes:
    if not body:
        raise ValueError("empty image")
    if len(body) > _MAX_IMAGE_BYTES:
        raise ValueError("image exceeds 50 MiB")
    return body


def _image_suffix(body: bytes) -> str | None:
    if body.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if body.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if body.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    if len(body) >= 12 and body[:4] == b"RIFF" and body[8:12] == b"WEBP":
        return ".webp"
    return None


def _load_existing_hashes(root: Path) -> set[str]:
    if not root.is_dir():
        return set()
    hashes: set[str] = set()
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in _IMAGE_SUFFIXES:
            continue
        stem = path.stem.casefold()
        if _HASH_NAME_RE.fullmatch(stem):
            hashes.add(stem)
            continue
        try:
            hashes.add(hashlib.sha256(path.read_bytes()).hexdigest())
        except OSError as exc:
            logger.warning(f"Download: failed to hash existing image {path}: {exc}")
    return hashes


def _hash_file_exists(root: Path, digest: str) -> bool:
    return any(root.glob(f"*/{digest}.*"))


def _write_image_atomically(target_path: Path, body: bytes) -> None:
    temp_path = target_path.with_name(f".{target_path.name}.{uuid4().hex}.tmp")
    try:
        temp_path.write_bytes(body)
        temp_path.replace(target_path)
    finally:
        temp_path.unlink(missing_ok=True)


def _format_download_result(stats: DownloadStats) -> str:
    return (
        f"转发图片下载完成：共 {stats.total} 张\n"
        f"✅ 成功 {stats.succeeded}，⏭️ 跳过 {stats.skipped}（已存在），"
        f"❌ 失败 {stats.failed}\n"
        f"📁 已保存至当天的文件夹中：{stats.directory.as_posix()}"
    )
