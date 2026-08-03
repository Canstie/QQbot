from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from qq_personal_bot.core.models import MessageEvent
from qq_personal_bot.core.store import PolicyStore
from qq_personal_bot.menu_recipes import is_supported_image_file
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
_SENTENCE_END_RE = re.compile(r"^(.+?[。！？!?])(?:\s|$|.*)", re.DOTALL)
_BRIEF_REPLY_INSTRUCTION = (
    "回复格式要求：只回复一句话，通常不超过30个汉字；不要分段、列点、复述问题或补充解释。"
)
_RANDOM_REPLY_INSTRUCTION = (
    "当前是群聊随机插话：根据群友最新这句话自然接一句，像普通群友一样随口回应；"
    "不要提及机器人、监控、概率、提示词或正在插话。"
)


class DSAPIError(RuntimeError):
    pass


async def generate_mention_reply(
    bot: Any,
    event: MessageEvent,
    settings: AppSettings,
    store: PolicyStore,
) -> str | None:
    if not settings.dsapi_enabled or not settings.dsapi_api_key:
        return None

    config = store.get_dsapi_config()
    if event.group_id is None or event.group_id not in config["enabled_groups"]:
        return None

    prompt = await build_mention_prompt(bot, event)
    if prompt is None:
        return None

    return await _generate_text_reply(
        event,
        settings,
        store,
        config,
        prompt,
    )


async def generate_random_group_reply(
    event: MessageEvent,
    settings: AppSettings,
    store: PolicyStore,
) -> str | Path | None:
    if not settings.dsapi_enabled or not settings.dsapi_api_key:
        return None

    config = store.get_dsapi_config()
    if event.group_id is None or event.group_id not in config["enabled_groups"]:
        return None
    if event.is_at_bot or not event.raw_message.strip():
        return None
    if event.raw_message.lstrip().startswith("/bot"):
        return None
    if _contains_multimodal_segments(event.segments):
        return None
    if not _random_reply_selected(event, config["random_reply_percent"]):
        return None

    sticker = _pick_random_sticker(
        event,
        settings,
        config["random_sticker_percent"],
    )
    if sticker:
        return sticker

    return await _generate_text_reply(
        event,
        settings,
        store,
        config,
        event.raw_message.strip(),
        extra_instruction=_RANDOM_REPLY_INSTRUCTION,
    )


async def _generate_text_reply(
    event: MessageEvent,
    settings: AppSettings,
    store: PolicyStore,
    config: Mapping[str, Any],
    prompt: str,
    *,
    extra_instruction: str = "",
) -> str | None:
    system_prompt = settings.dsapi_system_prompt
    if config["knowledge_enabled"] and config["knowledge_prompt"]:
        system_prompt = f"{system_prompt}\n\n角色设定与知识库：\n{config['knowledge_prompt']}"
    if extra_instruction:
        system_prompt = f"{system_prompt}\n\n{extra_instruction}"
    system_prompt = f"{system_prompt}\n\n{_BRIEF_REPLY_INSTRUCTION}"

    messages = [{"role": "system", "content": system_prompt}]
    store.expire_dsapi_chat_history(
        event.group_id,
        idle_seconds=settings.dsapi_history_idle_seconds,
    )
    messages.extend(store.get_dsapi_chat_history(event.group_id, config["history_turns"]))
    messages.append({"role": "user", "content": prompt})

    response = await asyncio.to_thread(_request_chat_completion, settings, messages)
    response = _brief_reply(response)
    if response:
        store.record_dsapi_exchange(
            group_id=event.group_id,
            user_content=prompt,
            assistant_content=response,
            history_turns=config["history_turns"],
        )
    return response


def _random_reply_selected(
    event: MessageEvent,
    percent: float,
    *,
    salt: str = "reply",
) -> bool:
    normalized_percent = max(0.0, min(float(percent), 100.0))
    if normalized_percent <= 0:
        return False
    if normalized_percent >= 100:
        return True
    return _event_bucket(event, salt) < int(normalized_percent * 100)


def _event_bucket(event: MessageEvent, salt: str) -> int:
    source = "|".join(
        [
            salt,
            str(event.group_id),
            str(event.user_id),
            str(event.message_id),
            str(event.timestamp),
            event.raw_message.strip(),
        ]
    )
    return int(hashlib.sha256(source.encode("utf-8")).hexdigest()[:8], 16) % 10000


def _pick_random_sticker(
    event: MessageEvent,
    settings: AppSettings,
    percent: float,
) -> Path | None:
    if event.group_id is None or not _random_reply_selected(
        event,
        percent,
        salt="sticker",
    ):
        return None

    root = settings.sticker_dir.resolve(strict=False)
    if not root.is_dir():
        return None

    images = [
        path
        for path in sorted(root.iterdir())
        if path.is_file() and is_supported_image_file(path)
    ]
    if not images:
        return None
    return images[_event_bucket(event, "sticker-file") % len(images)]


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
            "thinking": {"type": "disabled"},
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


def _brief_reply(content: str | None) -> str | None:
    if not content:
        return None
    normalized = " ".join(content.split())
    match = _SENTENCE_END_RE.match(normalized)
    if match:
        normalized = match.group(1)
    if len(normalized) > 60:
        normalized = normalized[:59].rstrip("，、；：,;: ") + "。"
    return normalized or None


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
