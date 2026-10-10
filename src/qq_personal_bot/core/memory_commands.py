from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from qq_personal_bot.core.person_evaluation import is_person_evaluation_request
from qq_personal_bot.replies import direct_lua_command

_SLASH_COMMAND = re.compile(r"^/(?:bot|qqbot|check|d|dov|dimg|rm|steam|steamwho|在干嘛)(?=\s|$)")
_CQ_SEGMENT = re.compile(r"\[CQ:[^\]]+\]")
_CQ_AT = re.compile(r"\[CQ:at,qq=(\d+)(?:,[^\]]*)?\]")


def is_memory_command(
    message: Mapping[str, Any], *, prefixes: Sequence[str], bot_ids: Sequence[int | str] = (),
) -> bool:
    """Exclude bot requests; keyword-triggered automatic replies remain ordinary conversation."""
    if message.get("is_bot_command") or message.get("is_at_bot"):
        return True
    bots = {str(value) for value in bot_ids}
    if str(message.get("user_id")) in bots:
        return True
    segments = message.get("segments") or ()
    text_parts = []
    mentions = set()
    for segment in segments:
        if not isinstance(segment, Mapping) or not isinstance(segment.get("data"), Mapping):
            continue
        data = segment["data"]
        if segment.get("type") == "text":
            text_parts.append(str(data.get("text") or ""))
        elif segment.get("type") == "at":
            mentions.add(str(data.get("qq") or ""))
        elif segment.get("type") == "reply" and str(data.get("user_id")) in bots:
            return True
    raw = str(message.get("platform_raw_message") or message.get("raw_message")
              or message.get("content") or "")
    mentions.update(_CQ_AT.findall(raw))
    if mentions.intersection(bots):
        return True
    text = ("".join(text_parts) if text_parts else _CQ_SEGMENT.sub("", raw)).strip()
    if any(prefix and text.startswith(prefix) for prefix in prefixes) or _SLASH_COMMAND.match(text):
        return True
    if direct_lua_command(text) is not None:
        return True
    return bool(is_person_evaluation_request(text) and any(
        target.isdigit() and int(target) > 0 and target not in bots for target in mentions
    ))
