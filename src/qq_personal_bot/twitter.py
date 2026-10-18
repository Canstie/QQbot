from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

import httpx
from PIL import Image, UnidentifiedImageError

logger = logging.getLogger(__name__)
_STATUS_PATH = re.compile(
    r"/(?:[A-Za-z0-9_]{1,15}/status|i/(?:web/)?status)/(\d{1,22})"
    r"(?:/(?:photo|video)/\d+)?/?$",
)
_POST_HOSTS = {"x.com", "www.x.com", "mobile.x.com", "twitter.com", "www.twitter.com", "mobile.twitter.com"}
_MAX_METADATA_BYTES = 1_000_000
_MAX_IMAGE_BYTES = 12_000_000
_MAX_IMAGES = 20
_IMAGE_FORMATS = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp", "GIF": ".gif"}


class TwitterImageError(ValueError):
    """A user-facing failure without proxy credentials or upstream response bodies."""


@dataclass(frozen=True)
class CachedTwitterImages:
    directory: Path | None
    paths: tuple[Path, ...]

    def cleanup(self) -> None:
        if self.directory is not None:
            shutil.rmtree(self.directory, ignore_errors=True)


def twitter_status_url(value: str) -> str | None:
    """Normalize a status link without fetching user-controlled URLs."""
    try:
        parsed = urlsplit(value)
        match = _STATUS_PATH.fullmatch(parsed.path)
        if (parsed.scheme not in {"http", "https"} or parsed.hostname not in _POST_HOSTS
                or parsed.username or parsed.password or parsed.port not in (None, 80, 443) or not match):
            return None
        return f"https://x.com/i/status/{match[1]}"
    except ValueError:
        return None


def _original_photo_url(value: object) -> str:
    if not isinstance(value, str):
        raise TwitterImageError("推文图片地址异常，请稍后重试。")
    try:
        parsed = urlsplit(value)
        match = re.fullmatch(r"/media/([A-Za-z0-9_-]+)(?:\.(jpg|jpeg|png|webp|gif))?", parsed.path)
        if (parsed.scheme != "https" or parsed.hostname != "pbs.twimg.com"
                or parsed.username or parsed.password or parsed.port not in (None, 443) or not match):
            raise ValueError("invalid photo URL")
        image_format = match[2] or parse_qs(parsed.query).get("format", ["jpg"])[0]
        if image_format not in {"jpg", "jpeg", "png", "webp", "gif"}:
            raise ValueError("invalid image format")
        return urlunsplit(("https", "pbs.twimg.com", f"/media/{match[1]}",
                           urlencode({"format": image_format, "name": "orig"}), ""))
    except ValueError:
        raise TwitterImageError("推文图片地址异常，请稍后重试。") from None


def _photo_urls(payload: object, status_id: str) -> tuple[str, ...]:
    if not isinstance(payload, Mapping):
        raise TwitterImageError("推特解析接口返回格式异常，请稍后重试。")
    post = payload.get("tweet") or payload.get("status")
    if not isinstance(post, Mapping) or str(post.get("id", "")) != status_id:
        raise TwitterImageError("暂时无法获取这条推文，请确认链接有效且推文公开。")
    media = post.get("media") or {}
    if not isinstance(media, Mapping):
        raise TwitterImageError("推特解析接口返回格式异常，请稍后重试。")
    all_media, photos = media.get("all"), media.get("photos")
    entries = [item for item in all_media if isinstance(item, Mapping) and item.get("type") == "photo"] if isinstance(all_media, list) else []
    if isinstance(photos, list):
        entries.extend(photos)
    urls: list[str] = []
    seen: set[str] = set()
    for item in entries:
        if not isinstance(item, Mapping):
            raise TwitterImageError("推文图片列表异常，请稍后重试。")
        url = _original_photo_url(item.get("url"))
        identity = urlsplit(url).path
        if identity not in seen:
            seen.add(identity)
            urls.append(url)
    if len(urls) > _MAX_IMAGES:
        raise TwitterImageError(f"这条推文超过 {_MAX_IMAGES} 张图片，暂时无法一次发送。")
    return tuple(urls)


async def _fetch_photos(client: httpx.AsyncClient, status_id: str) -> tuple[str, ...]:
    failure = "推特解析接口暂不可用，请稍后重试。"
    for path in (f"/status/{status_id}", f"/2/status/{status_id}"):
        try:
            async with client.stream("GET", "https://api.fxtwitter.com" + path) as response:
                logger.info("Twitter metadata status=%s via_proxy=True", response.status_code)
                if response.status_code != 200:
                    failure = f"推特解析接口暂不可用（HTTP {response.status_code}），请稍后重试。"
                    continue
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > _MAX_METADATA_BYTES:
                        raise TwitterImageError("推特解析数据过大，暂时无法处理。")
            return _photo_urls(json.loads(body), status_id)
        except TwitterImageError as exc:
            failure = str(exc)
        except (httpx.RequestError, ValueError):
            logger.info("Twitter metadata outcome=request_failed via_proxy=True")
    raise TwitterImageError(failure)


async def _download_photo(
    client: httpx.AsyncClient, url: str, directory: Path, index: int,
) -> Path:
    path = directory / f"{index:02d}.part"
    for attempt in range(2):
        try:
            async with client.stream("GET", url, headers={"Accept": "image/*"}) as response:
                response.raise_for_status()
                if not response.headers.get("Content-Type", "").lower().startswith("image/"):
                    raise TwitterImageError("推文图片下载返回了非图片内容，请稍后重试。")
                size = 0
                with path.open("wb") as target:
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > _MAX_IMAGE_BYTES:
                            raise TwitterImageError("推文中有图片超过 12 MB，暂时无法发送。")
                        target.write(chunk)
            with Image.open(path) as image:
                extension = _IMAGE_FORMATS.get(image.format or "")
                if not extension or image.width * image.height > 40_000_000:
                    raise TwitterImageError("推文中有图片格式或尺寸不受支持。")
                image.verify()
            final_path = path.with_suffix(extension)
            path.rename(final_path)
            logger.info("Twitter image index=%s bytes=%s via_proxy=True", index, size)
            return final_path
        except (httpx.RequestError, httpx.HTTPStatusError) as exc:
            status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
            logger.info("Twitter image index=%s attempt=%s status=%s error=%s via_proxy=True",
                        index, attempt + 1, status, type(exc).__name__)
            if attempt or status is not None and status not in {408, 429, 500, 502, 503, 504}:
                break
    raise TwitterImageError("推文图片下载失败，请稍后重试。")


async def cache_twitter_images(source_url: str, *, proxy_url: str) -> CachedTwitterImages:
    """Resolve public post photos and download every image through the explicit proxy."""
    normalized = twitter_status_url(source_url)
    if normalized is None:
        raise TwitterImageError("请提供有效的 X/Twitter 推文链接。")
    if not proxy_url:
        raise TwitterImageError("推特图片解析尚未配置显式代理。")
    directory: Path | None = None
    completed = False
    try:
        async with asyncio.timeout(60), httpx.AsyncClient(
            proxy=proxy_url, trust_env=False, follow_redirects=False,
            timeout=httpx.Timeout(15, connect=6),
            limits=httpx.Limits(max_connections=1, max_keepalive_connections=1, keepalive_expiry=30),
            headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
        ) as client:
            urls = await _fetch_photos(client, normalized.rsplit("/", 1)[1])
            logger.info("Twitter resolved photos=%s via_proxy=True", len(urls))
            if not urls:
                return CachedTwitterImages(directory=None, paths=())
            directory = Path(tempfile.mkdtemp(prefix="qqbot-twitter-"))
            # The CDN is shared by every photo; reuse one TLS tunnel on unstable proxy routes.
            paths = []
            for index, url in enumerate(urls, 1):
                paths.append(await _download_photo(client, url, directory, index))
        completed = True
        return CachedTwitterImages(directory=directory, paths=tuple(paths))
    except TimeoutError:
        raise TwitterImageError("推特图片解析超时，请稍后重试。") from None
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError):
        raise TwitterImageError("推文图片读取失败，请稍后重试。") from None
    finally:
        if directory is not None and not completed:
            shutil.rmtree(directory, ignore_errors=True)
