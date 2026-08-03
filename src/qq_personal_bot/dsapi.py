from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from qq_personal_bot.core.models import MessageEvent
from qq_personal_bot.settings import AppSettings


_MULTIMODAL_SEGMENT_TYPES = {
    "file",
    "forward",
    "image",
    "json",
    "lightapp",
    "markdown",
    "marketface",
    "mface",
    "node",
    "record",
    "video",
    "xml",
}
_CQ_SEGMENT_RE = re.compile(r"\[CQ:([a-zA-Z0-9_-]+)(?:,[^\]]*)?\]")


class DSAPIError(RuntimeError):
    pass


async def generate_mention_reply(
    bot: Any,
    event: MessageEvent,
    settings: AppSettings,
) -> str | None:
    if not settings.dsapi_enabled or not settings.dsapi_api_key:
        return None

    prompt = await build_mention_prompt(bot, event)
    if prompt is None:
        return None

    messages = [
        {"role": "system", "content": settings.dsapi_system_prompt},
        {"role": "user", "content": prompt},
    ]
    return await asyncio.to_thread(_request_chat_completion, settings, messages)


async def build_mention_prompt(bot: Any, event: MessageEvent) -> str | None:
    if _contains_multimodal_segments(event.segments):
        return None

    current_text = event.raw_message.strip()
    quoted_segments = _embedded_reply_segments(event.segments)
    reply_id = _reply_message_id(event.segments)

    if quoted_segments is None and reply_id is not None:
        try:
            payload = await bot.call_api("get_msg", message_id=reply_id)
        except Exception as exc:
            raise DSAPIError(f"failed to read replied message: {exc}") from exc
        quoted_segments = _message_segments_from_payload(payload)

    quoted_text = ""
    if quoted_segments is not None:
        if _contains_multimodal_value(quoted_segments):
            return None
        quoted_text = _text_from_message(quoted_segments).strip()

    if not current_text and not quoted_text:
        return None
    if not quoted_text:
        return current_text

    user_instruction = current_text or "请回复这条被引用的消息。"
    return f"被引用的消息：\n{quoted_text}\n\n用户的问题或补充：\n{user_instruction}"


def _request_chat_completion(
    settings: AppSettings,
    messages: list[dict[str, str]],
) -> str | None:
    payload = json.dumps(
        {
            "model": settings.dsapi_model,
            "messages": messages,
            "max_tokens": settings.dsapi_max_tokens,
            "stream": False,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    request = Request(
        _chat_completions_url(settings.dsapi_base_url),
        data=payload,
        headers={
            "Authorization": f"Bearer {settings.dsapi_api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urlopen(request, timeout=settings.dsapi_timeout_seconds) as response:
            result = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise DSAPIError(f"HTTP {exc.code}") from exc
    except URLError as exc:
        raise DSAPIError(f"network error: {exc.reason}") from exc
    except TimeoutError as exc:
        raise DSAPIError("request timed out") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DSAPIError("invalid JSON response") from exc

    try:
        content = result["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise DSAPIError("response does not contain assistant content") from exc

    if not isinstance(content, str):
        raise DSAPIError("assistant content is not text")
    return content.strip() or None


def _chat_completions_url(base_url: str) -> str:
    normalized = base_url.rstrip("/")
    if normalized.endswith("/chat/completions"):
        return normalized
    return f"{normalized}/chat/completions"


def _contains_multimodal_segments(segments: Sequence[Mapping[str, Any]]) -> bool:
    for segment in segments:
        segment_type = str(segment.get("type", "")).lower()
        if segment_type in _MULTIMODAL_SEGMENT_TYPES:
            return True
        if segment_type == "reply":
            message = segment.get("data", {}).get("message")
            if message is not None and _contains_multimodal_value(message):
                return True
    return False


def _contains_multimodal_value(message: Any) -> bool:
    if isinstance(message, str):
        return any(
            match.group(1).lower() in _MULTIMODAL_SEGMENT_TYPES
            for match in _CQ_SEGMENT_RE.finditer(message)
        )
    if isinstance(message, Mapping):
        segment_type = str(message.get("type", "")).lower()
        if segment_type in _MULTIMODAL_SEGMENT_TYPES:
            return True
        data = message.get("data")
        nested = message.get("message")
        if nested is None and isinstance(data, Mapping):
            nested = data.get("message")
        return nested is not None and _contains_multimodal_value(nested)
    if isinstance(message, Sequence):
        return any(_contains_multimodal_value(item) for item in message)
    return False


def _embedded_reply_segments(segments: Sequence[Mapping[str, Any]]) -> Any | None:
    for segment in segments:
        if segment.get("type") == "reply":
            return segment.get("data", {}).get("message")
    return None


def _reply_message_id(segments: Sequence[Mapping[str, Any]]) -> int | str | None:
    for segment in segments:
        if segment.get("type") == "reply":
            return segment.get("data", {}).get("id")
    return None


def _message_segments_from_payload(payload: Any) -> Any | None:
    if not isinstance(payload, Mapping):
        return None
    data = payload.get("data")
    if isinstance(data, Mapping) and data.get("message") is not None:
        return data.get("message")
    if payload.get("message") is not None:
        return payload.get("message")
    if isinstance(data, Mapping):
        return data.get("raw_message")
    return payload.get("raw_message")


def _text_from_message(message: Any) -> str:
    if isinstance(message, str):
        return _CQ_SEGMENT_RE.sub("", message)
    if isinstance(message, Mapping):
        if message.get("type") == "text":
            return str(message.get("data", {}).get("text", ""))
        nested = message.get("message")
        return _text_from_message(nested) if nested is not None else ""
    if isinstance(message, Sequence):
        return "".join(_text_from_message(item) for item in message)
    return ""
