from __future__ import annotations

from typing import Any

from nonebot.adapters.onebot.v11 import Event
from nonebot.exception import IgnoredException
from nonebot.message import event_preprocessor


def is_self_message(event: Any) -> bool:
    user_id = getattr(event, "user_id", None)
    self_id = getattr(event, "self_id", None)
    return user_id is not None and self_id is not None and str(user_id) == str(self_id)


@event_preprocessor
async def ignore_self_message(event: Event) -> None:
    if is_self_message(event):
        raise IgnoredException("ignore bot self message")
