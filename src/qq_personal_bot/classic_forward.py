from __future__ import annotations

import asyncio
import hashlib
import logging
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from qq_personal_bot.classic_storage import ClassicStorageError, get_classic_storage
from qq_personal_bot.runtime import get_store

logger = logging.getLogger(__name__)
# Application limits for each independently sent record, not protocol limits.
MAX_BATCH_IMAGES = 100
_active_groups: set[int] = set()
_EXTENSIONS = {"image/jpeg": ".jpg", "image/png": ".png",
               "image/gif": ".gif", "image/webp": ".webp"}


def _cache_image(storage: Any, group_id: int, record: dict, directory: Path) -> Path:
    body = storage.read_image(group_id, record["object_key"])
    if len(body) != record["size_bytes"] or hashlib.sha256(body).hexdigest() != record["sha256"]:
        raise ClassicStorageError("群典图片校验失败")
    path = directory / f'{record["id"]}{_EXTENSIONS.get(record["content_type"], ".img")}'
    path.write_bytes(body)
    return path


async def _cache_image_async(storage: Any, group_id: int, record: dict, directory: Path) -> Path:
    task = asyncio.create_task(asyncio.to_thread(_cache_image, storage, group_id, record, directory))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        # to_thread keeps writing after cancellation; finish before deleting its directory.
        await asyncio.gather(task, return_exceptions=True)
        raise


def build_forward_nodes(paths: list[Path], self_id: str) -> list[dict]:
    return [{"type": "node", "data": {
        "user_id": str(self_id), "nickname": "本群典藏",
        "content": [{"type": "image", "data": {"file": path.resolve().as_uri()}}],
    }} for path in paths]


async def send_all_classics(
    group_id: int | None,
    self_id: str,
    send_forward: Callable[[list[dict]], Awaitable[None]],
    send_notice: Callable[[str], Awaitable[None]],
) -> None:
    if group_id is None:
        await send_notice("请在群聊中使用 ~爆典all。")
        return
    group_id = int(group_id)
    if group_id in _active_groups:
        await send_notice("本群典图正在整理发送中，请稍候。")
        return
    _active_groups.add(group_id)
    try:
        records = get_store().list_classic_images(group_id, limit=None)
        if not records:
            await send_notice("这个群还没有存过典，先发送 ~存典 存一张吧。")
            return
        delivery = ("将合并为一份聊天记录发送" if len(records) <= MAX_BATCH_IMAGES
                    else f"将按每批最多 {MAX_BATCH_IMAGES} 张分批发送")
        await send_notice(f"正在整理本群 {len(records)} 张典图，{delivery}，请稍候……")
        sent = 0
        sent_batches = 0
        failed = 0
        phase = "整理"
        try:
            storage = get_classic_storage()
            with tempfile.TemporaryDirectory(prefix="qqbot-classic-forward-") as directory:
                paths: list[Path] = []

                async def send_batch() -> None:
                    nonlocal sent, sent_batches, phase
                    if not paths:
                        return
                    phase = "发送"
                    logger.info("Classic forward batch: group=%s batch=%s images=%s",
                                group_id, sent_batches + 1, len(paths))
                    await send_forward(build_forward_nodes(paths, self_id))
                    sent += len(paths)
                    sent_batches += 1
                    phase = "整理"
                    # Release this batch only after the awaited send has completed.
                    for path in paths:
                        path.unlink(missing_ok=True)
                    paths.clear()

                for record in records:
                    if len(paths) >= MAX_BATCH_IMAGES:
                        await send_batch()
                    try:
                        path = await _cache_image_async(storage, group_id, record, Path(directory))
                    except Exception:
                        logger.exception("Classic forward read failed: group=%s image=%s", group_id, record["id"])
                        failed += 1
                        continue
                    paths.append(path)
                await send_batch()
        except Exception:
            # Do not automatically retry an ambiguous send: QQ may already have accepted it.
            logger.exception("Classic forward stopped: group=%s phase=%s confirmed_sent=%s batches=%s total=%s",
                             group_id, phase, sent, sent_batches, len(records))
            if phase == "发送":
                await send_notice(f"第 {sent_batches + 1} 批聊天记录发送失败或超时，未确认送达；"
                                  f"前 {sent_batches} 批已确认发送 {sent} 张，共 {len(records)} 张。"
                                  "已停止后续批次，请先检查群里是否已收到，避免重复发送。")
            else:
                await send_notice(f"典图整理中断，已确认发送 {sent} 张，共 {len(records)} 张，请稍后再试。")
            return
        if failed:
            await send_notice(f"本群典图已发送 {sent} 张，另有 {failed} 张读取失败。")
    finally:
        _active_groups.discard(group_id)
