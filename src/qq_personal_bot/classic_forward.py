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
# Application limits for each nested record, not protocol limits or whole-archive limits.
MAX_BATCH_IMAGES = 20
MAX_BATCH_BYTES = 50 * 1024 * 1024
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
        await send_notice(f"正在整理本群 {len(records)} 张典图，请稍候……")
        prepared = 0
        failed = 0
        phase = "整理"
        try:
            storage = get_classic_storage()
            with tempfile.TemporaryDirectory(prefix="qqbot-classic-forward-") as directory:
                paths: list[Path] = []
                batch_bytes = 0
                batches: list[dict] = []

                def finish_batch() -> None:
                    nonlocal batch_bytes
                    if not paths:
                        return
                    number = len(batches) + 1
                    # LLBot encodes node content containing nodes as an embedded forward.
                    batches.append({"type": "node", "data": {
                        "user_id": str(self_id), "nickname": "本群典藏",
                        "source": f"本群典藏 · 第 {number} 批",
                        "summary": f"查看本批 {len(paths)} 张典图",
                        "content": build_forward_nodes(paths, self_id),
                    }})
                    # Files must survive until the single outer send has completed.
                    paths.clear()
                    batch_bytes = 0

                for record in records:
                    if paths and (len(paths) >= MAX_BATCH_IMAGES or
                                  batch_bytes + record["size_bytes"] > MAX_BATCH_BYTES):
                        finish_batch()
                    try:
                        path = await _cache_image_async(storage, group_id, record, Path(directory))
                    except Exception:
                        logger.exception("Classic forward read failed: group=%s image=%s", group_id, record["id"])
                        failed += 1
                        continue
                    paths.append(path)
                    prepared += 1
                    batch_bytes += record["size_bytes"]
                finish_batch()
                if batches:
                    phase = "发送"
                    logger.info("Classic nested forward: group=%s images=%s batches=%s",
                                group_id, prepared, [len(batch["data"]["content"]) for batch in batches])
                    await send_forward(batches)
        except Exception:
            # Do not automatically retry an ambiguous send: QQ may already have accepted it.
            logger.exception("Classic nested forward stopped: group=%s phase=%s prepared=%s total=%s",
                             group_id, phase, prepared, len(records))
            if phase == "发送":
                await send_notice(f"嵌套聊天记录发送失败或超时，未确认送达（已整理 {prepared} 张，共 {len(records)} 张）。"
                                  "请先检查群里是否已收到，避免重复发送。")
            else:
                await send_notice("典图整理失败，尚未发送聊天记录，请稍后再试。")
            return
        if failed:
            await send_notice(f"本群典图已发送 {prepared} 张，另有 {failed} 张读取失败。")
    finally:
        _active_groups.discard(group_id)
