from __future__ import annotations

from typing import Any

import pytest

from qq_personal_bot.activity import GroupActivityRecord, GroupActivityRecorder


class RecordingStore:
    def __init__(self) -> None:
        self.batches: list[list[dict[str, Any]]] = []

    def record_group_message_activities(self, activities: list[dict[str, Any]]) -> int:
        self.batches.append(activities)
        return len(activities)


@pytest.mark.asyncio
async def test_activity_recorder_batches_without_blocking_handler() -> None:
    store = RecordingStore()
    recorder = GroupActivityRecorder(store, batch_size=8, flush_interval_seconds=0)

    for message_id in (1, 2):
        assert recorder.enqueue(
            GroupActivityRecord(
                group_id=123,
                user_id=456,
                timestamp=1_000.0 + message_id,
                raw_message=f"消息 {message_id}",
                segments=(),
                message_id=message_id,
            )
        )

    await recorder.flush()
    await recorder.close()

    assert len(store.batches) == 1
    assert [item["message_id"] for item in store.batches[0]] == [1, 2]
