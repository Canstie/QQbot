from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence
from urllib.parse import parse_qs, unquote, urlparse

_MINIAPP_PROMPT_PREFIX = "[QQ小程序]"
_URL_KEYS = (
    "qqdocurl",
    "jumpurl",
    "jump_url",
    "shareurl",
    "share_url",
    "weburl",
    "web_url",
    "url",
)
_TITLE_KEYS = ("title", "prompt", "desc")
_MEDIA_KEY_PARTS = ("icon", "image", "img", "preview", "cover", "avatar", "logo")
_URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)


@dataclass(frozen=True)
class MiniAppLink:
    title: str
    url: str


def extract_miniapp_link(segments: Sequence[Mapping[str, Any]]) -> MiniAppLink | None:
    for segment in segments:
        if str(segment.get("type", "")).lower() != "json":
            continue
        payload = _decode_json_payload(segment.get("data"))
        if payload is None or not _looks_like_miniapp(payload):
            continue

        url = _find_url(payload)
        if url is None:
            continue
        title = _find_title(payload) or "小程序分享"
        return MiniAppLink(title=title, url=url)
    return None


def format_miniapp_link(link: MiniAppLink) -> str:
    return f"标题：{link.title}\n链接：{link.url}"


def _decode_json_payload(data: Any) -> Mapping[str, Any] | None:
    value = data.get("data") if isinstance(data, Mapping) and "data" in data else data
    if isinstance(value, Mapping):
        return value
    if not isinstance(value, str):
        return None
    try:
        decoded = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return None
    return decoded if isinstance(decoded, Mapping) else None


def _looks_like_miniapp(payload: Mapping[str, Any]) -> bool:
    prompt = str(payload.get("prompt", ""))
    app = str(payload.get("app", "")).lower()
    meta = payload.get("meta")
    return (
        prompt.startswith(_MINIAPP_PROMPT_PREFIX)
        or "miniapp" in app
        or (app == "com.tencent.tuwen.lua" and isinstance(meta, Mapping) and "news" in meta)
        or (isinstance(meta, Mapping) and any(str(key).startswith("detail_") for key in meta))
    )


def _find_title(payload: Mapping[str, Any]) -> str | None:
    candidates: list[tuple[int, str]] = []
    for path, value in _walk(payload):
        key = path[-1].lower() if path else ""
        if key not in _TITLE_KEYS or not isinstance(value, str):
            continue
        title = _clean_title(value)
        if not title:
            continue
        priority = 0 if key == "title" else 1 if key == "prompt" else 2
        candidates.append((priority, title))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    return candidates[0][1]


def _find_url(payload: Mapping[str, Any]) -> str | None:
    candidates: list[tuple[int, str]] = []
    for path, value in _walk(payload):
        if not isinstance(value, str):
            continue
        key = path[-1].lower() if path else ""
        if any(part in key for part in _MEDIA_KEY_PARTS):
            continue
        priority = _URL_KEYS.index(key) if key in _URL_KEYS else len(_URL_KEYS)
        for match in _URL_RE.findall(html.unescape(value)):
            normalized = _normalize_url(match)
            if normalized is not None:
                candidates.append((priority, normalized))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    return candidates[0][1]


def _normalize_url(value: str) -> str | None:
    url = html.unescape(value).rstrip(".,;!?)，。；！？）]")
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None

    query = parse_qs(parsed.query)
    for key in ("url", "target", "redirect", "redirect_url"):
        nested_values = query.get(key)
        if not nested_values:
            continue
        nested = unquote(nested_values[0])
        nested_parsed = urlparse(nested)
        if nested_parsed.scheme in {"http", "https"} and nested_parsed.netloc:
            return nested
    return url


def _clean_title(value: str) -> str:
    title = value.strip()
    if title.startswith(_MINIAPP_PROMPT_PREFIX):
        title = title[len(_MINIAPP_PROMPT_PREFIX) :].strip()
    title = " ".join(title.split())
    return title[:200]


def _walk(value: Any, path: tuple[str, ...] = ()):
    if isinstance(value, Mapping):
        for key, child in value.items():
            child_path = (*path, str(key))
            yield child_path, child
            yield from _walk(child, child_path)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _walk(child, path)
