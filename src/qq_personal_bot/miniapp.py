from __future__ import annotations

import asyncio
import html
import json
import re
import shutil
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import parse_qs, unquote, urlparse, urlunparse
from urllib.request import Request, urlopen

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
_INITIAL_STATE_MARKER = "window.__INITIAL_STATE__="
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
)
_MAX_PAGE_BYTES = 3_000_000
_MAX_IMAGE_BYTES = 12_000_000
_MAX_IMAGES = 18
_IMAGE_HOST_SUFFIXES = (
    ".xhscdn.com",
    ".xhsimg.com",
    ".ugcimg.cn",
    ".xiaohongshu.com",
)
_IMAGE_EXTENSIONS = {
    "image/gif": ".gif",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}


@dataclass(frozen=True)
class MiniAppLink:
    title: str
    url: str
    source_url: str


@dataclass(frozen=True)
class CachedMiniAppImages:
    directory: Path | None
    paths: tuple[Path, ...]

    def cleanup(self) -> None:
        if self.directory is not None:
            shutil.rmtree(self.directory, ignore_errors=True)


def extract_miniapp_link(segments: Sequence[Mapping[str, Any]]) -> MiniAppLink | None:
    for segment in segments:
        if str(segment.get("type", "")).lower() != "json":
            continue
        payload = _decode_json_payload(segment.get("data"))
        if payload is None or not _looks_like_miniapp(payload):
            continue

        urls = _find_url(payload)
        if urls is None:
            continue
        url, source_url = urls
        title = _find_title(payload) or "小程序分享"
        return MiniAppLink(title=title, url=url, source_url=source_url)
    return None


def format_miniapp_link(link: MiniAppLink) -> str:
    return f"标题：{link.title}\n链接：{link.url}"


async def cache_miniapp_images(link: MiniAppLink) -> CachedMiniAppImages:
    if not _is_xiaohongshu_page_url(link.source_url):
        return CachedMiniAppImages(directory=None, paths=())
    return await asyncio.to_thread(_cache_xiaohongshu_images, link.source_url)


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


def _find_url(payload: Mapping[str, Any]) -> tuple[str, str] | None:
    candidates: list[tuple[int, str, str]] = []
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
                candidates.append((priority, normalized, _source_url(match)))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    _, normalized, source = candidates[0]
    return normalized, source


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
            return _shorten_xiaohongshu_url(nested)
    return _shorten_xiaohongshu_url(url)


def _source_url(value: str) -> str:
    url = html.unescape(value).rstrip(".,;!?)，。；！？）]")
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    for key in ("url", "target", "redirect", "redirect_url"):
        nested_values = query.get(key)
        if nested_values:
            nested = unquote(nested_values[0])
            if _is_xiaohongshu_page_url(nested):
                return nested
    return url


def _shorten_xiaohongshu_url(url: str) -> str:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    is_xiaohongshu = host == "xiaohongshu.com" or host.endswith(".xiaohongshu.com")
    is_note = parsed.path.startswith(("/discovery/item/", "/explore/"))
    if not is_xiaohongshu or not is_note:
        return url
    return urlunparse(parsed._replace(scheme="https", params="", query="", fragment=""))


def _cache_xiaohongshu_images(source_url: str) -> CachedMiniAppImages:
    page, final_url = _fetch_page(source_url)
    image_urls = _extract_xiaohongshu_image_urls(page, final_url)
    if not image_urls:
        return CachedMiniAppImages(directory=None, paths=())

    directory = Path(tempfile.mkdtemp(prefix="qqbot-xhs-"))
    paths: list[Path] = []
    try:
        with ThreadPoolExecutor(max_workers=min(4, len(image_urls))) as executor:
            futures = {
                executor.submit(_download_image, url, directory, index, final_url): index
                for index, url in enumerate(image_urls, start=1)
            }
            downloaded: list[tuple[int, Path]] = []
            for future in as_completed(futures):
                try:
                    downloaded.append((futures[future], future.result()))
                except (OSError, ValueError):
                    continue
        paths = [path for _, path in sorted(downloaded)]
        if not paths:
            shutil.rmtree(directory, ignore_errors=True)
            return CachedMiniAppImages(directory=None, paths=())
        return CachedMiniAppImages(directory=directory, paths=tuple(paths))
    except Exception:
        shutil.rmtree(directory, ignore_errors=True)
        raise


def _fetch_page(url: str) -> tuple[str, str]:
    if not _is_xiaohongshu_page_url(url):
        raise ValueError("unsupported mini app page host")
    request = Request(
        url,
        headers={"User-Agent": _USER_AGENT, "Accept-Language": "zh-CN,zh;q=0.9"},
    )
    with urlopen(request, timeout=20) as response:
        final_url = response.geturl()
        if not _is_xiaohongshu_page_url(final_url):
            raise ValueError("mini app page redirected to an unsupported host")
        body = response.read(_MAX_PAGE_BYTES + 1)
    if len(body) > _MAX_PAGE_BYTES:
        raise ValueError("mini app page is too large")
    return body.decode("utf-8"), final_url


def _extract_xiaohongshu_image_urls(page: str, page_url: str) -> list[str]:
    start = page.find(_INITIAL_STATE_MARKER)
    if start < 0:
        return []
    start += len(_INITIAL_STATE_MARKER)
    end = page.find("</script>", start)
    if end < 0:
        return []
    raw_state = page[start:end].strip().rstrip(";")
    state = json.loads(raw_state.replace("undefined", "null"))

    if not isinstance(state, Mapping):
        return []
    note_state = state.get("note")
    if not isinstance(note_state, Mapping):
        return []
    detail_map = note_state.get("noteDetailMap", {})
    if not isinstance(detail_map, Mapping):
        return []
    note_id = _xiaohongshu_note_id(page_url)
    details = [detail_map[note_id]] if note_id in detail_map else list(detail_map.values())[:1]

    urls: list[str] = []
    for detail in details:
        if not isinstance(detail, Mapping):
            continue
        note = detail.get("note", detail)
        image_list = note.get("imageList", []) if isinstance(note, Mapping) else []
        if not isinstance(image_list, list):
            continue
        for image in image_list:
            url = _image_url(image)
            if url is not None and url not in urls:
                urls.append(url)
            if len(urls) >= _MAX_IMAGES:
                return urls
    return urls


def _image_url(image: Any) -> str | None:
    if not isinstance(image, Mapping):
        return None
    candidates = [image.get("urlDefault"), image.get("url")]
    info_list = image.get("infoList")
    if isinstance(info_list, list):
        candidates.extend(
            info.get("url")
            for info in info_list
            if isinstance(info, Mapping) and info.get("imageScene") == "WB_DFT"
        )
    for value in candidates:
        if isinstance(value, str) and _is_allowed_image_url(value):
            parsed = urlparse(value)
            return urlunparse(parsed._replace(scheme="https"))
    return None


def _download_image(url: str, directory: Path, index: int, referer: str) -> Path:
    if not _is_allowed_image_url(url):
        raise ValueError("unsupported image host")
    request = Request(
        url,
        headers={"User-Agent": _USER_AGENT, "Referer": referer},
    )
    with urlopen(request, timeout=20) as response:
        if not _is_allowed_image_url(response.geturl()):
            raise ValueError("image redirected to an unsupported host")
        content_type = response.headers.get_content_type().lower()
        extension = _IMAGE_EXTENSIONS.get(content_type)
        if extension is None:
            raise ValueError("unsupported image content type")
        body = response.read(_MAX_IMAGE_BYTES + 1)
    if not body or len(body) > _MAX_IMAGE_BYTES:
        raise ValueError("image is empty or too large")
    path = directory / f"{index:02d}{extension}"
    path.write_bytes(body)
    return path


def _is_xiaohongshu_page_url(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    return parsed.scheme in {"http", "https"} and (
        host == "xhslink.com"
        or host.endswith(".xhslink.com")
        or host == "xiaohongshu.com"
        or host.endswith(".xiaohongshu.com")
    )


def _is_allowed_image_url(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    return parsed.scheme in {"http", "https"} and any(
        host == suffix[1:] or host.endswith(suffix) for suffix in _IMAGE_HOST_SUFFIXES
    )


def _xiaohongshu_note_id(url: str) -> str:
    parts = [part for part in urlparse(url).path.split("/") if part]
    if len(parts) >= 3 and parts[-2] == "item":
        return parts[-1]
    if len(parts) >= 2 and parts[-2] == "explore":
        return parts[-1]
    return ""


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
