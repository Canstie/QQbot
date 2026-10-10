from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

from nonebot import logger

from qq_personal_bot.core.store import PolicyStore


@dataclass(frozen=True, slots=True)
class GroupActivityRecord:
    group_id: int
    user_id: int
    timestamp: float
    raw_message: str
    segments: tuple[Any, ...]
    message_id: int | str = ""
    record_activity: bool = True
    record_memory: bool = False
    platform_raw_message: str = ""
    display_name: str = ""
    bot_id: int | str = ""
    is_at_bot: bool = False
    is_bot_command: bool = False


class GroupActivityRecorder:
    """Buffer activity records so SQLite writes do not block message dispatch."""

    def __init__(
        self,
        store: PolicyStore,
        *,
        batch_size: int = 64,
        flush_interval_seconds: float = 0.05,
        queue_size: int = 512,
    ) -> None:
        self.store = store
        self.batch_size = max(1, int(batch_size))
        self.flush_interval_seconds = max(0.0, float(flush_interval_seconds))
        self.queue: asyncio.Queue[GroupActivityRecord] = asyncio.Queue(
            maxsize=max(1, int(queue_size))
        )
        self._worker: asyncio.Task[None] | None = None
        self._closed = False

    def enqueue(self, record: GroupActivityRecord) -> bool:
        if self._closed:
            return False
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._run(), name="qqbot-group-activity-writer")
        try:
            self.queue.put_nowait(record)
        except asyncio.QueueFull:
            logger.warning(
                f"Activity queue is full; dropping one record queue_size={self.queue.qsize()}"
            )
            return False
        return True

    async def flush(self) -> None:
        if self._worker is not None:
            await self.queue.join()

    async def close(self) -> None:
        self._closed = True
        await self.flush()
        if self._worker is None:
            return
        self._worker.cancel()
        with suppress(asyncio.CancelledError):
            await self._worker
        self._worker = None

    async def _run(self) -> None:
        while True:
            first = await self.queue.get()
            batch = [first]
            if self.flush_interval_seconds:
                await asyncio.sleep(self.flush_interval_seconds)
            while len(batch) < self.batch_size:
                try:
                    batch.append(self.queue.get_nowait())
                except asyncio.QueueEmpty:
                    break
            activities = [
                {
                    "group_id": record.group_id,
                    "user_id": record.user_id,
                    "timestamp": record.timestamp,
                    "raw_message": record.raw_message,
                    "segments": record.segments,
                    "message_id": record.message_id,
                    "platform_raw_message": record.platform_raw_message,
                    "display_name": record.display_name,
                    "bot_id": record.bot_id,
                    "is_at_bot": record.is_at_bot,
                    "is_bot_command": record.is_bot_command,
                }
                for record in batch
            ]
            try:
                activity_batch = [item for item, record in zip(activities, batch, strict=True) if record.record_activity]
                if activity_batch:
                    await asyncio.to_thread(self.store.record_group_message_activities, activity_batch)
            except Exception as exc:  # noqa: BLE001 - keep the writer alive
                logger.exception(f"Failed to persist group activity batch: {exc}")
            try:
                memory_batch = [item for item, record in zip(activities, batch, strict=True) if record.record_memory]
                if memory_batch:
                    await asyncio.to_thread(self.store.record_memory_messages, memory_batch)
            except Exception as exc:  # noqa: BLE001 - keep the writer alive
                logger.exception(f"Failed to persist group memory batch: {exc}")
            finally:
                for _ in batch:
                    self.queue.task_done()


_recorder: GroupActivityRecorder | None = None


def get_activity_recorder(store: PolicyStore) -> GroupActivityRecorder:
    global _recorder
    if _recorder is None or _recorder.store is not store or _recorder._closed:
        _recorder = GroupActivityRecorder(store)
    return _recorder


async def flush_group_activity() -> None:
    if _recorder is not None:
        await _recorder.flush()


async def close_group_activity() -> None:
    global _recorder
    if _recorder is not None:
        await _recorder.close()
        _recorder = None
