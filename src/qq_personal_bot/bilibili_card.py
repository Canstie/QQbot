from __future__ import annotations

import json
import re
import shutil
import tempfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.error import HTTPError
from urllib.parse import urljoin, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from PIL import Image, ImageDraw, ImageFont, ImageOps

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
)
_BILIBILI_API_URL = "https://api.bilibili.com/x/web-interface/view?bvid={}"
_BVID_RE = re.compile(r"(?:^|/)(BV[0-9A-Za-z]{10})(?:/|$)")
_MAX_METADATA_BYTES = 3_000_000
_MAX_COVER_BYTES = 12_000_000
_MAX_AVATAR_BYTES = 5_000_000
_MAX_IMAGE_PIXELS = 40_000_000
_CARD_SIZE = (1200, 920)
_COVER_SIZE = (1152, 648)
_PINK = (251, 114, 153)
_LIGHT_PINK = (255, 241, 246)
_TEXT = (24, 25, 28)
_MUTED_TEXT = (97, 102, 109)
_PLATFORM_TITLES = {"bilibili", "哔哩哔哩", "哔哩哔哩弹幕网"}
_BILIBILI_IMAGE_SUFFIXES = (".hdslb.com", ".biliimg.com")
_QQ_PREVIEW_SUFFIXES = (".ugcimg.cn", ".qpic.cn", ".qq.com")
_BOLD_FONT_PATHS = (
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "C:/Windows/Fonts/msyhbd.ttc",
    "C:/Windows/Fonts/msyh.ttc",
)
_REGULAR_FONT_PATHS = (
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/msyhbd.ttc",
)


@dataclass(frozen=True)
class BilibiliCardRequest:
    source_url: str
    fallback_title: str | None = None
    fallback_cover_url: str | None = None


@dataclass(frozen=True)
class BilibiliVideoMetadata:
    title: str | None
    cover_url: str | None
    author_name: str | None
    author_avatar_url: str | None


class _RestrictedRedirectHandler(HTTPRedirectHandler):
    def __init__(self, allowed_url: Callable[[str], bool]) -> None:
        super().__init__()
        self._allowed_url = allowed_url

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        candidate = urljoin(req.full_url, newurl)
        if not self._allowed_url(candidate):
            raise ValueError("redirected to an unsupported host")
        return super().redirect_request(req, fp, code, msg, headers, candidate)


def is_bilibili_share_url(url: str) -> bool:
    parsed = urlparse(str(url))
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"}:
        return False
    if host == "b23.tv" or host.endswith(".b23.tv"):
        return bool(parsed.path.strip("/"))
    return _is_bilibili_page_url(url) and _extract_bvid(url) is not None


def is_bilibili_preview_url(url: str) -> bool:
    return _normalized_allowed_image_url(url) is not None


def generate_bilibili_card(request: BilibiliCardRequest) -> Path | None:
    metadata: BilibiliVideoMetadata | None = None
    try:
        video_url = _resolve_bilibili_video_url(request.source_url)
        bvid = _extract_bvid(video_url)
        if bvid is None:
            raise ValueError("Bilibili video id was not found")
        metadata = _fetch_bilibili_metadata(bvid)
    except (OSError, ValueError, json.JSONDecodeError):
        metadata = None

    title = _clean_text(metadata.title if metadata else None, 200)
    if not title:
        title = _clean_text(request.fallback_title, 200) or "B站视频"
    author_name = _clean_text(metadata.author_name if metadata else None, 80)
    if not author_name:
        author_name = "哔哩哔哩"

    cover_urls = _unique_nonempty(
        (
            metadata.cover_url if metadata else None,
            request.fallback_cover_url,
        )
    )
    cover_body: bytes | None = None
    for cover_url in cover_urls:
        try:
            cover_body = _download_image_bytes(cover_url, max_bytes=_MAX_COVER_BYTES)
        except (OSError, ValueError):
            continue
        break
    if cover_body is None:
        return None

    avatar_body: bytes | None = None
    avatar_url = metadata.author_avatar_url if metadata else None
    if avatar_url:
        try:
            avatar_body = _download_image_bytes(avatar_url, max_bytes=_MAX_AVATAR_BYTES)
        except (OSError, ValueError):
            avatar_body = None

    directory = Path(tempfile.mkdtemp(prefix="qqbot-bilibili-"))
    output = directory / "card.jpg"
    try:
        _render_bilibili_card(
            cover_body=cover_body,
            title=title,
            author_name=author_name,
            avatar_body=avatar_body,
            output=output,
        )
        return output
    except (OSError, ValueError):
        shutil.rmtree(directory, ignore_errors=True)
        return None


def _resolve_bilibili_video_url(source_url: str) -> str:
    if not is_bilibili_share_url(source_url):
        raise ValueError("unsupported Bilibili share URL")
    if _extract_bvid(source_url) is not None:
        return source_url
    request = Request(
        source_url,
        headers={
            "User-Agent": _USER_AGENT,
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Range": "bytes=0-0",
        },
    )
    try:
        with _open_restricted(request, _is_bilibili_page_url, timeout=20) as response:
            final_url = response.geturl()
    except HTTPError as exc:
        final_url = exc.geturl()
    if not _is_bilibili_page_url(final_url) or _extract_bvid(final_url) is None:
        raise ValueError("Bilibili share did not resolve to a video")
    return final_url


def _fetch_bilibili_metadata(bvid: str) -> BilibiliVideoMetadata:
    if not re.fullmatch(r"BV[0-9A-Za-z]{10}", bvid):
        raise ValueError("invalid Bilibili video id")
    request = Request(
        _BILIBILI_API_URL.format(bvid),
        headers={"User-Agent": _USER_AGENT, "Referer": "https://www.bilibili.com/"},
    )
    with _open_restricted(request, _is_bilibili_api_url, timeout=20) as response:
        if not _is_bilibili_api_url(response.geturl()):
            raise ValueError("Bilibili API redirected to an unsupported host")
        body = response.read(_MAX_METADATA_BYTES + 1)
    if not body or len(body) > _MAX_METADATA_BYTES:
        raise ValueError("Bilibili metadata response is empty or too large")
    payload = json.loads(body.decode("utf-8"))
    if not isinstance(payload, Mapping) or payload.get("code") != 0:
        raise ValueError("Bilibili metadata request failed")
    data = payload.get("data")
    if not isinstance(data, Mapping):
        raise ValueError("Bilibili metadata is missing")
    owner = data.get("owner")
    owner = owner if isinstance(owner, Mapping) else {}
    return BilibiliVideoMetadata(
        title=_clean_text(data.get("title"), 200),
        cover_url=_normalized_bilibili_image_url(data.get("pic")),
        author_name=_clean_text(owner.get("name"), 80),
        author_avatar_url=_normalized_bilibili_image_url(owner.get("face")),
    )


def _download_image_bytes(url: str, *, max_bytes: int) -> bytes:
    normalized = _normalized_allowed_image_url(url)
    if normalized is None:
        raise ValueError("unsupported Bilibili image host")
    request = Request(
        normalized,
        headers={"User-Agent": _USER_AGENT, "Referer": "https://www.bilibili.com/"},
    )
    with _open_restricted(request, _is_allowed_card_image_url, timeout=20) as response:
        if not _is_allowed_card_image_url(response.geturl()):
            raise ValueError("Bilibili image redirected to an unsupported host")
        content_type = response.headers.get_content_type().lower()
        if content_type not in {"image/gif", "image/jpeg", "image/png", "image/webp"}:
            raise ValueError("unsupported Bilibili image content type")
        body = response.read(max_bytes + 1)
    if not body or len(body) > max_bytes:
        raise ValueError("Bilibili image is empty or too large")
    _load_image(body)
    return body


def _render_bilibili_card(
    *,
    cover_body: bytes,
    title: str,
    author_name: str,
    avatar_body: bytes | None,
    output: Path,
) -> None:
    canvas = Image.new("RGB", _CARD_SIZE, _PINK)
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle((24, 24, 1176, 896), radius=34, fill=(255, 255, 255))

    cover = ImageOps.fit(_load_image(cover_body), _COVER_SIZE, method=Image.Resampling.LANCZOS)
    cover_mask = Image.new("L", _COVER_SIZE, 0)
    ImageDraw.Draw(cover_mask).rounded_rectangle((0, 0, 1151, 647), radius=30, fill=255)
    canvas.paste(cover, (24, 24), cover_mask)

    title_font = _load_font(46, bold=True)
    author_font = _load_font(30, bold=False)
    badge_font = _load_font(24, bold=True)
    title_lines = _wrap_text(draw, title, title_font, max_width=1072, max_lines=2)
    line_y = 704
    for line in title_lines:
        draw.text((64, line_y), line, font=title_font, fill=_TEXT)
        line_y += 58

    avatar_size = 62
    avatar_xy = (64, 820)
    if avatar_body is not None:
        avatar = ImageOps.fit(
            _load_image(avatar_body),
            (avatar_size, avatar_size),
            method=Image.Resampling.LANCZOS,
        )
        avatar_mask = Image.new("L", (avatar_size, avatar_size), 0)
        ImageDraw.Draw(avatar_mask).ellipse((0, 0, avatar_size - 1, avatar_size - 1), fill=255)
        canvas.paste(avatar, avatar_xy, avatar_mask)
        draw.ellipse(
            (
                avatar_xy[0] - 2,
                avatar_xy[1] - 2,
                avatar_xy[0] + avatar_size + 1,
                avatar_xy[1] + avatar_size + 1,
            ),
            outline=_PINK,
            width=3,
        )
    else:
        draw.ellipse(
            (
                avatar_xy[0],
                avatar_xy[1],
                avatar_xy[0] + avatar_size,
                avatar_xy[1] + avatar_size,
            ),
            fill=_PINK,
        )
        placeholder = _author_initial(author_name)
        placeholder_font = _load_font(30, bold=True)
        _draw_centered_text(
            draw,
            placeholder,
            box=(avatar_xy[0], avatar_xy[1], avatar_size, avatar_size),
            font=placeholder_font,
            fill=(255, 255, 255),
        )

    display_author = _ellipsize_text(draw, author_name, author_font, max_width=720)
    draw.text((146, 833), display_author, font=author_font, fill=_MUTED_TEXT)
    badge_box = (1002, 824, 1136, 878)
    draw.rounded_rectangle(badge_box, radius=18, fill=_LIGHT_PINK)
    _draw_centered_text(
        draw,
        "BILIBILI",
        box=(badge_box[0], badge_box[1], badge_box[2] - badge_box[0], badge_box[3] - badge_box[1]),
        font=badge_font,
        fill=_PINK,
    )

    canvas.save(output, format="JPEG", quality=92, optimize=True, subsampling=1)


def _open_restricted(request: Request, allowed_url: Callable[[str], bool], *, timeout: int):
    if not allowed_url(request.full_url):
        raise ValueError("unsupported URL")
    opener = build_opener(_RestrictedRedirectHandler(allowed_url))
    return opener.open(request, timeout=timeout)


def _is_bilibili_page_url(url: str) -> bool:
    parsed = urlparse(str(url))
    host = (parsed.hostname or "").lower()
    return parsed.scheme in {"http", "https"} and (
        host == "b23.tv"
        or host.endswith(".b23.tv")
        or host == "bilibili.com"
        or host.endswith(".bilibili.com")
    )


def _is_bilibili_api_url(url: str) -> bool:
    parsed = urlparse(str(url))
    return (
        parsed.scheme == "https"
        and (parsed.hostname or "").lower() == "api.bilibili.com"
        and parsed.path.rstrip("/") == "/x/web-interface/view"
    )


def _is_allowed_card_image_url(url: str) -> bool:
    parsed = urlparse(str(url))
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and any(
        host == suffix[1:] or host.endswith(suffix)
        for suffix in (*_BILIBILI_IMAGE_SUFFIXES, *_QQ_PREVIEW_SUFFIXES)
    )


def _extract_bvid(url: str) -> str | None:
    parsed = urlparse(str(url))
    match = _BVID_RE.search(parsed.path)
    return match.group(1) if match is not None else None


def _normalized_bilibili_image_url(value: Any) -> str | None:
    normalized = _normalized_allowed_image_url(value)
    if normalized is None:
        return None
    host = (urlparse(normalized).hostname or "").lower()
    if not any(
        host == suffix[1:] or host.endswith(suffix) for suffix in _BILIBILI_IMAGE_SUFFIXES
    ):
        return None
    return normalized


def _normalized_allowed_image_url(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    parsed = urlparse(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    normalized = parsed._replace(scheme="https").geturl()
    return normalized if _is_allowed_card_image_url(normalized) else None


def _load_image(body: bytes) -> Image.Image:
    with Image.open(BytesIO(body)) as source:
        width, height = source.size
        if width <= 0 or height <= 0 or width * height > _MAX_IMAGE_PIXELS:
            raise ValueError("Bilibili image dimensions are invalid")
        source.load()
        return source.convert("RGB")


def _load_font(size: int, *, bold: bool) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = _BOLD_FONT_PATHS if bold else _REGULAR_FONT_PATHS
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size=size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def _wrap_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    *,
    max_width: int,
    max_lines: int,
) -> list[str]:
    normalized = " ".join(text.split()) or "B站视频"
    lines: list[str] = []
    remaining = normalized
    while remaining and len(lines) < max_lines:
        line = ""
        for char in remaining:
            candidate = line + char
            if line and _text_width(draw, candidate, font) > max_width:
                break
            line = candidate
        if not line:
            line = remaining[0]
        lines.append(line)
        remaining = remaining[len(line) :].lstrip()
    if remaining:
        lines[-1] = _ellipsize_text(draw, lines[-1] + remaining, font, max_width=max_width)
    return lines


def _ellipsize_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    *,
    max_width: int,
) -> str:
    normalized = " ".join(text.split())
    if _text_width(draw, normalized, font) <= max_width:
        return normalized
    suffix = "…"
    while normalized and _text_width(draw, normalized + suffix, font) > max_width:
        normalized = normalized[:-1]
    return normalized.rstrip() + suffix


def _draw_centered_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    *,
    box: tuple[int, int, int, int],
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    fill: tuple[int, int, int],
) -> None:
    left, top, width, height = box
    bounds = draw.textbbox((0, 0), text, font=font)
    text_width = bounds[2] - bounds[0]
    text_height = bounds[3] - bounds[1]
    draw.text(
        (
            left + (width - text_width) / 2 - bounds[0],
            top + (height - text_height) / 2 - bounds[1],
        ),
        text,
        font=font,
        fill=fill,
    )


def _text_width(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
) -> float:
    bounds = draw.textbbox((0, 0), text, font=font)
    return float(bounds[2] - bounds[0])


def _clean_text(value: Any, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(char for char in value.split() if char).strip()
    if not cleaned:
        return None
    return cleaned[:limit]


def _author_initial(author_name: str) -> str:
    for char in author_name:
        if char.isalnum() or "\u3400" <= char <= "\u9fff":
            return char.upper()
    return "B"


def _unique_nonempty(values: tuple[str | None, ...]) -> tuple[str, ...]:
    result: list[str] = []
    for value in values:
        if isinstance(value, str) and value.strip() and value not in result:
            result.append(value)
    return tuple(result)
