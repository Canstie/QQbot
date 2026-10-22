"""Shared font loading and text layout primitives for image cards."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from PIL import ImageDraw, ImageFont


def load_font_candidates(
    candidates: Iterable[str | Path | tuple[Path | None, int] | None],
    size: int,
    *,
    default_size: int | None = None,
) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in candidates:
        path, index = candidate if isinstance(candidate, tuple) else (candidate, 0)
        if not path:
            continue
        try:
            return ImageFont.truetype(str(path), size=size, index=index)
        except OSError:
            continue
    return ImageFont.load_default(size=default_size)


def load_font(
    size: int, *, bold: bool = False, preferred_path: Path | None = None
) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        preferred_path,
        Path("C:/Windows/Fonts/msyhbd.ttc" if bold else "C:/Windows/Fonts/msyh.ttc"),
        Path(
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"
            if bold
            else "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
        ),
        Path(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
            if bold
            else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        ),
    ]
    return load_font_candidates(candidates, size, default_size=size)


def ellipsize_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    *,
    max_width: int,
) -> str:
    normalized = " ".join(text.split())
    if text_width(draw, normalized, font) <= max_width:
        return normalized
    suffix = "…"
    while normalized and text_width(draw, normalized + suffix, font) > max_width:
        normalized = normalized[:-1]
    return normalized.rstrip() + suffix


def draw_centered_text(
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


def wrap_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
    max_width: int,
) -> tuple[str, ...]:
    lines: list[str] = []
    current = ""
    for character in text:
        candidate = current + character
        if current and text_width(draw, candidate, font) > max_width:
            lines.append(current.rstrip())
            current = character.lstrip()
        else:
            current = candidate
    if current:
        lines.append(current.rstrip())
    return tuple(lines or ("",))


def text_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> int:
    bounds = draw.textbbox((0, 0), text, font=font)
    return bounds[2] - bounds[0]


def text_height(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> int:
    bounds = draw.textbbox((0, 0), text, font=font)
    return bounds[3] - bounds[1]
