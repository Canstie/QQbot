from __future__ import annotations

import asyncio
import hashlib
import logging
import secrets
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from qq_personal_bot.download_storage import DownloadStorageError, get_download_storage
from qq_personal_bot.runtime import get_store


logger = logging.getLogger(__name__)
MAX_IMAGE_BYTES = 50 * 1024 * 1024
EXTENSIONS = {"image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif",
              "image/webp": ".webp", "image/bmp": ".bmp", "image/avif": ".avif"}


def _cache_image(storage: Any, record: dict, directory: Path) -> Path:
    if record["size_bytes"] > MAX_IMAGE_BYTES:
        raise DownloadStorageError("图片超过50 MiB限制")
    path = directory / (record["sha256"] + EXTENSIONS.get(record["content_type"], ".img"))
    response = storage.get_image(record["object_key"])
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("wb") as output:
            for chunk in response.stream(64 * 1024):
                size += len(chunk)
                if size > MAX_IMAGE_BYTES:
                    raise DownloadStorageError("图片超过50 MiB限制")
                digest.update(chunk)
                output.write(chunk)
        if size != record["size_bytes"] or digest.hexdigest() != record["sha256"]:
            raise DownloadStorageError("图库图片校验失败")
        return path
    except Exception as exc:
        raise DownloadStorageError("图库图片读取失败") from exc
    finally:
        try:
            response.close()
        finally:
            response.release_conn()


async def _cache_image_async(storage: Any, record: dict, directory: Path) -> Path:
    # A cancelled to_thread call keeps running: wait for the writer before removing its directory.
    task = asyncio.create_task(asyncio.to_thread(_cache_image, storage, record, directory))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await asyncio.gather(task, return_exceptions=True)
        raise


async def send_random_gallery(send_image: Callable[[Path], Awaitable[None]]) -> str | None:
    store = get_store()
    # Keep the short reservation transaction synchronous so cancellation cannot lose its result.
    records = store.reserve_download_images(secrets.randbelow(5) + 1)
    if not records:
        return "图库暂无图片，请管理员使用 /download 添加。"
    unsent = {item["id"]: item for item in records}
    sent = 0
    try:
        storage = get_download_storage()
        with tempfile.TemporaryDirectory(prefix="qqbot-gallery-") as directory:
            for record in records:
                try:
                    path = await _cache_image_async(storage, record, Path(directory))
                except (DownloadStorageError, OSError) as exc:
                    logger.warning("Gallery read failed for image %s: %s", record["id"], exc)
                    continue
                try:
                    await send_image(path)
                except Exception:
                    logger.exception("Gallery send failed for image %s", record["id"])
                    break
                sent += 1
                unsent.pop(record["id"])
                path.unlink(missing_ok=True)
    except (DownloadStorageError, OSError) as exc:
        logger.warning("Gallery unavailable: %s", exc)
    finally:
        store.release_download_images(list(unsent.values()))
    if sent == 0:
        return "图库图片暂时无法读取或发送，请稍后再试。"
    if unsent:
        return f"已发送 {sent} 张，部分图片读取或发送失败，请稍后再试。"
    return None
