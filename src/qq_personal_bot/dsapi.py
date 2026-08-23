from __future__ import annotations

import asyncio
import hashlib
import html
import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from qq_personal_bot.ai_models import is_vision_dsapi_model
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
_CQ_IMAGE_RE = re.compile(r"\[CQ:image(?:,([^\]]*))?\]", re.IGNORECASE)
_SENTENCE_END_RE = re.compile(r"^(.+?[。！？!?])(?:\s|$|.*)", re.DOTALL)
_RESPONSE_MODE_INSTRUCTIONS = {
    "short": "回复格式要求：只回复一句话，通常不超过30个汉字；不要分段、列点、复述问题或补充解释。",
    "normal": (
        "本知识库回复模式优先于前面的通用长度要求：完整回答用户问题，可使用1至3个短段落；"
        "识图时保留判断所需的关键细节，不要为了简短而省略重要信息。"
    ),
    "detailed": (
        "本知识库回复模式优先于前面的通用长度要求：进行充分分析并给出完整回答；可分段或列点，"
        "识图时说明关键视觉依据、不确定之处和结论。"
    ),
}
_RANDOM_REPLY_INSTRUCTION = (
    "当前是群聊随机插话：根据群友最新这句话自然接一句，像普通群友一样随口回应；"
    "不要提及机器人、监控、概率、提示词或正在插话。"
)
_RANDOM_CONTEXT_MESSAGE_LIMIT = 10
_MAX_VISION_IMAGES = 8
_MAX_INLINE_IMAGE_URL_CHARS = 44 * 1024 * 1024


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
    if not config["enabled"]:
        return None
    if event.group_id is None or event.group_id not in config["enabled_groups"]:
        return None

    active_knowledge = config.get("active_knowledge") or {}
    model = active_knowledge.get("model") or settings.dsapi_model
    prompt = await build_mention_prompt(
        bot,
        event,
        vision_enabled=is_vision_dsapi_model(model),
    )
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
    if not config["enabled"]:
        return None
    if event.group_id is None or event.group_id not in config["enabled_groups"]:
        return None
    if event.is_at_bot or not event.raw_message.strip():
        return None
    if event.raw_message.lstrip().startswith("/bot"):
        return None
    if _contains_multimodal_segments(event.segments):
        return None
    knowledge_id = int(config.get("active_knowledge_id") or 0)
    recent_context = store.get_dsapi_group_context(
        event.group_id,
        message_limit=_RANDOM_CONTEXT_MESSAGE_LIMIT,
        idle_seconds=settings.dsapi_history_idle_seconds,
        knowledge_id=knowledge_id,
    )
    store.record_dsapi_group_message(
        group_id=event.group_id,
        user_id=event.user_id,
        content=event.raw_message,
        message_limit=_RANDOM_CONTEXT_MESSAGE_LIMIT,
        knowledge_id=knowledge_id,
    )
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
        _build_random_group_prompt(event, recent_context),
        extra_instruction=_RANDOM_REPLY_INSTRUCTION,
        history_user_content=event.raw_message.strip(),
    )


def _build_random_group_prompt(
    event: MessageEvent,
    recent_context: Sequence[Mapping[str, Any]],
) -> str:
    latest = " ".join(event.raw_message.split())[:300]
    if not recent_context:
        return latest
    lines = [
        f"[QQ {int(item['user_id'])}] {' '.join(str(item['content']).split())[:300]}"
        for item in recent_context[-_RANDOM_CONTEXT_MESSAGE_LIMIT:]
    ]
    return (
        "群聊中当前消息之前的最近对话（从旧到新）：\n"
        + "\n".join(lines)
        + f"\n\n需要接话的最新消息：\n[QQ {event.user_id}] {latest}"
    )


async def _generate_text_reply(
    event: MessageEvent,
    settings: AppSettings,
    store: PolicyStore,
    config: Mapping[str, Any],
    prompt: str | list[dict[str, Any]],
    *,
    extra_instruction: str = "",
    history_user_content: str | None = None,
) -> str | None:
    active_knowledge = config.get("active_knowledge") or {}
    response_mode = str(active_knowledge.get("response_mode") or "short")
    system_prompt = settings.dsapi_system_prompt
    if config["knowledge_enabled"] and config["knowledge_prompt"]:
        system_prompt = f"{system_prompt}\n\n角色设定与知识库：\n{config['knowledge_prompt']}"
    if extra_instruction:
        system_prompt = f"{system_prompt}\n\n{extra_instruction}"
    response_instruction = _RESPONSE_MODE_INSTRUCTIONS.get(
        response_mode,
        _RESPONSE_MODE_INSTRUCTIONS["short"],
    )
    system_prompt = f"{system_prompt}\n\n{response_instruction}"

    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    knowledge_id = int(config.get("active_knowledge_id") or 0)
    store.expire_dsapi_chat_history(
        event.group_id,
        idle_seconds=settings.dsapi_history_idle_seconds,
        knowledge_id=knowledge_id,
    )
    messages.extend(
        store.get_dsapi_chat_history(
            event.group_id,
            config["history_turns"],
            knowledge_id=knowledge_id,
        )
    )
    messages.append({"role": "user", "content": prompt})

    response = await asyncio.to_thread(
        _request_chat_completion_with_fallback,
        settings,
        messages,
        model=active_knowledge.get("model") or settings.dsapi_model,
        max_tokens=active_knowledge.get("max_tokens") or settings.dsapi_max_tokens,
        thinking_enabled=bool(active_knowledge.get("thinking_enabled", False)),
        temperature=active_knowledge.get("temperature"),
    )
    response = _format_reply(response, response_mode)
    if response:
        store.record_dsapi_exchange(
            group_id=event.group_id,
            user_content=(
                _prompt_history_text(prompt)
                if history_user_content is None
                else history_user_content
            ),
            assistant_content=response,
            history_turns=config["history_turns"],
            knowledge_id=knowledge_id,
        )
    return response


def _request_chat_completion_with_fallback(
    settings: AppSettings,
    messages: list[dict[str, Any]],
    *,
    model: str,
    max_tokens: int,
    thinking_enabled: bool,
    temperature: float | None,
) -> str:
    try:
        response = _request_chat_completion(
            settings,
            messages,
            model=model,
            max_tokens=max_tokens,
            thinking_enabled=thinking_enabled,
            temperature=temperature,
        )
    except DSAPIError:
        if not thinking_enabled:
            raise
        response = _request_chat_completion(
            settings,
            messages,
            model=model,
            max_tokens=max_tokens,
            thinking_enabled=False,
            temperature=temperature,
        )
    if response is None and thinking_enabled:
        response = _request_chat_completion(
            settings,
            messages,
            model=model,
            max_tokens=max_tokens,
            thinking_enabled=False,
            temperature=temperature,
        )
    if response is None:
        raise DSAPIError("empty assistant content")
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


async def build_mention_prompt(
    bot: Any,
    event: MessageEvent,
    *,
    vision_enabled: bool = False,
) -> str | list[dict[str, Any]] | None:
    if _contains_multimodal_segments(event.segments, include_reply_content=False):
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
    image_sources: list[str] = []
    if quoted_segments is not None:
        if _contains_unsupported_multimodal_value(
            quoted_segments,
            allow_images=vision_enabled,
        ):
            return None
        quoted_text = _text_from_message(quoted_segments).strip()
        if vision_enabled:
            has_quoted_images = bool(_image_data_from_message(quoted_segments))
            image_sources = await _resolve_vision_image_sources(bot, quoted_segments)
            if has_quoted_images and not image_sources:
                raise DSAPIError("failed to resolve quoted image")

    if not current_text and not quoted_text and not image_sources:
        return None
    if not quoted_text and not image_sources:
        return current_text

    user_instruction = current_text or "请回复这条被引用的消息。"
    quoted_content = quoted_text or "（引用消息包含图片）"
    text_prompt = (
        f"被引用的消息：\n{quoted_content}\n\n用户的问题或补充：\n{user_instruction}"
    )
    if not image_sources:
        return text_prompt
    return [
        {"type": "text", "text": text_prompt},
        *[
            {
                "type": "image_url",
                "image_url": {"url": source, "detail": "auto"},
            }
            for source in image_sources
        ],
    ]


def _request_chat_completion(
    settings: AppSettings,
    messages: list[dict[str, Any]],
    *,
    model: str | None = None,
    max_tokens: int | None = None,
    thinking_enabled: bool = False,
    temperature: float | None = None,
) -> str | None:
    body: dict[str, Any] = {
        "model": model or settings.dsapi_model,
        "messages": messages,
        "max_tokens": max_tokens or settings.dsapi_max_tokens,
        "thinking": {"type": "enabled" if thinking_enabled else "disabled"},
        "stream": False,
    }
    if temperature is not None:
        body["temperature"] = float(temperature)
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
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


def _format_reply(content: str | None, response_mode: str) -> str | None:
    if str(response_mode).lower() == "short":
        return _brief_reply(content)
    if not content:
        return None
    normalized = content.strip()
    return normalized or None


def _chat_completions_url(base_url: str) -> str:
    normalized = base_url.rstrip("/")
    if normalized.endswith("/chat/completions"):
        return normalized
    return f"{normalized}/chat/completions"


def _contains_multimodal_segments(
    segments: Sequence[Mapping[str, Any]],
    *,
    include_reply_content: bool = True,
) -> bool:
    for segment in segments:
        segment_type = str(segment.get("type", "")).lower()
        if segment_type in _MULTIMODAL_SEGMENT_TYPES:
            return True
        if include_reply_content and segment_type == "reply":
            message = segment.get("data", {}).get("message")
            if message is not None and _contains_multimodal_value(message):
                return True
    return False


def _contains_unsupported_multimodal_value(
    message: Any,
    *,
    allow_images: bool,
) -> bool:
    if isinstance(message, str):
        return any(
            match.group(1).lower() in _MULTIMODAL_SEGMENT_TYPES
            and not (allow_images and match.group(1).lower() == "image")
            for match in _CQ_SEGMENT_RE.finditer(message)
        )
    if isinstance(message, Mapping):
        segment_type = str(message.get("type", "")).lower()
        if segment_type in _MULTIMODAL_SEGMENT_TYPES:
            return not (allow_images and segment_type == "image")
        data = message.get("data")
        nested = message.get("message")
        if nested is None and isinstance(data, Mapping):
            nested = data.get("message")
        return nested is not None and _contains_unsupported_multimodal_value(
            nested,
            allow_images=allow_images,
        )
    if isinstance(message, Sequence):
        return any(
            _contains_unsupported_multimodal_value(item, allow_images=allow_images)
            for item in message
        )
    return False


async def _resolve_vision_image_sources(bot: Any, message: Any) -> list[str]:
    entries = _image_data_from_message(message)
    sources: list[str] = []
    for data in entries:
        source = _direct_vision_image_source(data)
        if source is None:
            image_file = data.get("file") or data.get("file_id")
            if image_file:
                try:
                    payload = await bot.call_api("get_image", file=image_file)
                except Exception:
                    payload = None
                source = _direct_vision_image_source(payload)
        if source and source not in sources:
            sources.append(source)
        if len(sources) >= _MAX_VISION_IMAGES:
            break
    return sources


def _image_data_from_message(message: Any) -> list[Mapping[str, Any]]:
    if isinstance(message, str):
        entries: list[Mapping[str, Any]] = []
        for match in _CQ_IMAGE_RE.finditer(message):
            data: dict[str, str] = {}
            for item in (match.group(1) or "").split(","):
                key, separator, value = item.partition("=")
                if separator:
                    data[key.strip()] = html.unescape(value.strip())
            entries.append(data)
        return entries
    if isinstance(message, Mapping):
        if str(message.get("type", "")).lower() == "image":
            data = message.get("data")
            return [data] if isinstance(data, Mapping) else []
        nested = message.get("message")
        if nested is None:
            data = message.get("data")
            if isinstance(data, Mapping):
                nested = data.get("message")
        return _image_data_from_message(nested) if nested is not None else []
    if isinstance(message, Sequence):
        return [entry for item in message for entry in _image_data_from_message(item)]
    return []


def _direct_vision_image_source(value: Any) -> str | None:
    if not isinstance(value, Mapping):
        return None
    data = value.get("data")
    if isinstance(data, Mapping):
        value = data
    for key in ("url", "file", "path"):
        source = html.unescape(str(value.get(key) or "").strip())
        if source.startswith(("https://", "http://")) and len(source) <= 8192:
            return source
        if source.startswith("data:image/") and len(source) <= _MAX_INLINE_IMAGE_URL_CHARS:
            return source
        if source.startswith("base64://"):
            encoded = source.removeprefix("base64://")
            if len(encoded) <= _MAX_INLINE_IMAGE_URL_CHARS:
                return f"data:image/jpeg;base64,{encoded}"
    return None


def _prompt_history_text(prompt: str | list[dict[str, Any]]) -> str:
    if isinstance(prompt, str):
        return prompt
    text_parts = [
        str(item.get("text", "")).strip()
        for item in prompt
        if item.get("type") == "text" and str(item.get("text", "")).strip()
    ]
    image_count = sum(item.get("type") == "image_url" for item in prompt)
    suffix = f"\n[引用图片 {image_count} 张]" if image_count else ""
    return "\n".join(text_parts) + suffix


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
