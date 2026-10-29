"""Shared font loading and text layout primitives for image cards."""

from __future__ import annotations

import math
import shutil
import subprocess
import unicodedata
from collections.abc import Iterable
from functools import lru_cache
from pathlib import Path

from PIL import ImageDraw, ImageFont


@lru_cache(maxsize=64)
def fallback_fonts(size: int) -> tuple[ImageFont.ImageFont, ...]:
    """Scalable emoji, symbols and math faces available on our Linux/Windows hosts."""
    candidates = (
        ("/usr/local/share/fonts/noto-emoji/NotoEmoji-wght.ttf", 0),
        ("C:/Windows/Fonts/seguiemj.ttf", 0),
        ("/usr/share/fonts/truetype/noto/NotoEmoji-Regular.ttf", 0),
        ("/usr/share/fonts/truetype/noto/NotoSansSymbols2-Regular.ttf", 0),
        ("/usr/share/fonts/truetype/noto/NotoSansSymbols-Regular.ttf", 0),
        ("/usr/share/fonts/truetype/noto/NotoSansMath-Regular.ttf", 0),
        ("C:/Windows/Fonts/seguisym.ttf", 0),
        ("C:/Windows/Fonts/cambria.ttc", 1),
        ("/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf", 0),
        ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 0),
        ("/usr/local/share/fonts/jetbrains-mono-nerd/JetBrainsMonoNerdFontMono-Regular.ttf", 0),
    )
    fonts = []
    for path, index in candidates:
        if Path(path).is_file():
            try:
                fonts.append(ImageFont.truetype(path, size=size, index=index))
            except OSError:
                continue
    return tuple(fonts)


def grapheme_clusters(text: str) -> list[str]:
    """Keep combining marks, ZWJ emoji, modifiers, keycaps and flags together."""
    clusters: list[str] = []
    current = ""
    regional_count = 0
    for char in text:
        extend = bool(current) and (
            _is_cluster_extender(char)
            or current.endswith("\u200d")
            or (_is_regional_indicator(char) and regional_count % 2 == 1)
        )
        if current and not extend:
            clusters.append(current)
            current = ""
            regional_count = 0
        current += char
        if _is_regional_indicator(char):
            regional_count += 1
        elif not _is_cluster_extender(char):
            regional_count = 0
    if current:
        clusters.append(current)
    return clusters


def _is_cluster_extender(char: str) -> bool:
    codepoint = ord(char)
    return (
        char == "\u200d"
        or unicodedata.category(char).startswith("M")
        or 0xFE00 <= codepoint <= 0xFE0F
        or 0x1F3FB <= codepoint <= 0x1F3FF
        or 0xE0020 <= codepoint <= 0xE007F
        or 0xE0100 <= codepoint <= 0xE01EF
    )


def _is_regional_indicator(char: str) -> bool:
    return 0x1F1E6 <= ord(char) <= 0x1F1FF


def font_supports(font: ImageFont.ImageFont, text: str) -> bool:
    return all(_is_ignorable_for_glyph_check(char) or _font_has_glyph(font, char) for char in text)


def _is_ignorable_for_glyph_check(char: str) -> bool:
    codepoint = ord(char)
    return (
        char == "\u200d"
        or 0xFE00 <= codepoint <= 0xFE0F
        or 0xE0020 <= codepoint <= 0xE007F
        or 0xE0100 <= codepoint <= 0xE01EF
    )


@lru_cache(maxsize=8192)
def _font_has_glyph(font: ImageFont.ImageFont, char: str) -> bool:
    if char.isspace():
        return True
    try:
        glyph = font.getmask(char, mode="L")
        missing = font.getmask("\U0010ffff", mode="L")
    except (OSError, ValueError):
        return True
    return glyph.size != missing.size or bytes(glyph) != bytes(missing)


def _font_size(font: ImageFont.ImageFont) -> int:
    return max(1, int(getattr(font, "size", 16)))


@lru_cache(maxsize=64)
def _resolved_font(path: str, size: int, index: int) -> ImageFont.ImageFont:
    return ImageFont.truetype(path, size=size, index=index)


@lru_cache(maxsize=256)
def _fontconfig_fallback(size: int, cluster: str) -> ImageFont.ImageFont | None:
    """Use installed script/CJK-extension fonts for characters outside the usual faces."""
    executable = shutil.which("fc-match")
    if executable is None:
        return None
    charset = " ".join(f"{ord(char):x}" for char in cluster if not _is_ignorable_for_glyph_check(char))
    if not charset:
        return None
    try:
        result = subprocess.run(
            [executable, "--format=%{file}\\n%{index}", f":charset={charset}"],
            check=True, capture_output=True, text=True, encoding="utf-8", timeout=1,
        )
        path, index = result.stdout.strip().split("\n", 1)
        font = _resolved_font(path, size, int(index))
        return font if font_supports(font, cluster) else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def font_runs(text: str, primary: ImageFont.ImageFont) -> list[tuple[str, ImageFont.ImageFont]]:
    fonts = (primary, *fallback_fonts(_font_size(primary)))
    runs: list[tuple[str, ImageFont.ImageFont]] = []
    for cluster in grapheme_clusters(str(text)):
        selected = next((font for font in fonts if font_supports(font, cluster)), None)
        if selected is None:
            selected = _fontconfig_fallback(_font_size(primary), cluster) or primary
        if runs and runs[-1][1] is selected:
            previous, _ = runs[-1]
            runs[-1] = (previous + cluster, selected)
        else:
            runs.append((cluster, selected))
    return runs


def _font_ascent(font: ImageFont.ImageFont) -> int:
    return font.getmetrics()[0] if hasattr(font, "getmetrics") else 0


def draw_text_with_fallback(
    draw: ImageDraw.ImageDraw, position: tuple[float, float], text: str, *,
    font: ImageFont.ImageFont, fill: str | tuple[int, int, int],
) -> None:
    x, y = position
    baseline = y + _font_ascent(font)
    for run, selected in font_runs(text, font):
        draw.text((x, baseline), run, font=selected, fill=fill, anchor="ls", embedded_color=True)
        x += float(draw.textlength(run, font=selected))


def text_bounds_with_fallback(
    draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont,
) -> tuple[int, int, int, int]:
    cursor = 0.0
    baseline = _font_ascent(font)
    bounds = []
    for run, selected in font_runs(text, font):
        bounds.append(draw.textbbox((cursor, baseline), run, font=selected, anchor="ls", embedded_color=True))
        cursor += float(draw.textlength(run, font=selected))
    if not bounds:
        return (0, 0, 0, 0)
    return (
        math.floor(min(bound[0] for bound in bounds)),
        math.floor(min(bound[1] for bound in bounds)),
        math.ceil(max(cursor, *(bound[2] for bound in bounds))),
        math.ceil(max(bound[3] for bound in bounds)),
    )


def text_width_with_fallback(
    draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont,
) -> int:
    return math.ceil(sum(float(draw.textlength(run, font=selected)) for run, selected in font_runs(text, font)))


def ellipsize_text_with_fallback(
    draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, *, max_width: int,
) -> str:
    normalized = " ".join(text.split())
    if text_width_with_fallback(draw, normalized, font) <= max_width:
        return normalized
    clusters = grapheme_clusters(normalized)
    while clusters and text_width_with_fallback(draw, "".join(clusters) + "…", font) > max_width:
        clusters.pop()
    return "".join(clusters).rstrip() + "…"


def draw_centered_text_with_fallback(
    draw: ImageDraw.ImageDraw, text: str, *, box: tuple[int, int, int, int],
    font: ImageFont.ImageFont, fill: str | tuple[int, int, int],
) -> None:
    left, top, width, height = box
    bounds = text_bounds_with_fallback(draw, text, font)
    text_width = bounds[2] - bounds[0]
    text_height = bounds[3] - bounds[1]
    draw_text_with_fallback(
        draw, (left + (width - text_width) / 2 - bounds[0],
               top + (height - text_height) / 2 - bounds[1]),
        text, font=font, fill=fill,
    )


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
