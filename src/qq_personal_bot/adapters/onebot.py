from __future__ import annotations

import time
from typing import Any

from qq_personal_bot.core.models import MessageEvent


def onebot_to_internal(event: Any, self_id: int | str) -> MessageEvent:
    segments = []
    text_parts: list[str] = []
    is_at_bot = bool(getattr(event, "to_me", False))
    message = getattr(event, "message", None)
    platform_raw_message = str(getattr(event, "raw_message", "") or getattr(event, "message", "") or "")

    if message is not None:
        for segment in message:
            segment_type = getattr(segment, "type", None)
            data = getattr(segment, "data", None)
            if segment_type is None and isinstance(segment, dict):
                segment_type = segment.get("type")
                data = segment.get("data", {})
            data = dict(data or {})
            segments.append({"type": segment_type, "data": data})
            if segment_type == "text":
                text_parts.append(str(data.get("text", "")))
            if segment_type == "at" and str(data.get("qq")) == str(self_id):
                is_at_bot = True

    reply = getattr(event, "reply", None)
    if reply is not None and not any(segment.get("type") == "reply" for segment in segments):
        reply_id = getattr(reply, "message_id", None) or getattr(reply, "real_id", None)
        reply_data: dict[str, Any] = {}
        if reply_id is not None:
            reply_data["id"] = reply_id
        reply_message = getattr(reply, "message", None)
        if reply_message is not None:
            reply_data["message"] = _message_segments(reply_message)
        if reply_data:
            segments.insert(0, {"type": "reply", "data": reply_data})

    raw_message = "".join(text_parts).strip()
    if not raw_message:
        raw_message = str(getattr(event, "raw_message", "") or getattr(event, "message", ""))

    group_id = getattr(event, "group_id", None)
    if group_id is not None:
        group_id = int(group_id)

    return MessageEvent(
        platform="onebot.v11",
        message_id=getattr(event, "message_id", ""),
        group_id=group_id,
        user_id=int(getattr(event, "user_id")),
        raw_message=raw_message,
        platform_raw_message=platform_raw_message,
        segments=tuple(segments),
        is_at_bot=is_at_bot,
        timestamp=float(getattr(event, "time", time.time())),
    )


def _message_segments(message: Any) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    try:
        iterator = iter(message)
    except TypeError:
        return segments

    for segment in iterator:
        segment_type = getattr(segment, "type", None)
        data = getattr(segment, "data", None)
        if segment_type is None and isinstance(segment, dict):
            segment_type = segment.get("type")
            data = segment.get("data", {})
        segments.append({"type": segment_type, "data": dict(data or {})})
    return segments
