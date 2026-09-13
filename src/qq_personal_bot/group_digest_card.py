from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from datetime import date as calendar_date
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from qq_personal_bot.settings import AppSettings

WIDTH = 1080
CANVAS_HEIGHT = 7200
PAGE_LEFT = 54
PAGE_RIGHT = WIDTH - 54
CONTENT_LEFT = 88
CONTENT_RIGHT = WIDTH - 88

BG = "#eaf0f7"
PAPER = "#ffffff"
INK = "#203047"
MUTED = "#77869a"
LINE = "#e6ebf1"
BLUE = "#3978ad"
DEEP_BLUE = "#285b88"
TEAL = "#4d9c9b"
ORANGE = "#e8a64b"
SOFT_BLUE = "#eef5fb"
SOFT_ORANGE = "#fff6e9"


def render_group_digest_card(
    settings: AppSettings,
    *,
    output_path: Path,
    group_name: str,
    date: str,
    summary: Mapping[str, Any],
    digest: Mapping[str, Any],
    names: Mapping[int, str],
    transcript_size: int,
    model: str,
) -> Path:
    canvas = Image.new("RGB", (WIDTH, CANVAS_HEIGHT), BG)
    draw = ImageDraw.Draw(canvas)
    fonts = _fonts(settings)

    draw.rounded_rectangle(
        (PAGE_LEFT + 5, 33, PAGE_RIGHT + 5, CANVAS_HEIGHT - 24),
        radius=28,
        fill="#dce4ed",
    )
    draw.rounded_rectangle(
        (PAGE_LEFT, 24, PAGE_RIGHT, CANVAS_HEIGHT - 34),
        radius=28,
        fill=PAPER,
    )

    y = 66
    y = _draw_header(draw, y, group_name, date, fonts)
    y = _draw_metrics(draw, y, summary, fonts)
    y = _draw_peak_ribbon(draw, y, summary, fonts)
    y = _draw_hourly_chart(draw, y, summary, fonts)
    y = _draw_atmosphere(draw, y, digest, fonts)
    y = _draw_topics(draw, y, digest, fonts)
    y = _draw_portraits(draw, y, digest, names, fonts)
    y = _draw_quotes(draw, y, digest, fonts)
    y = _draw_closing(draw, y, digest, fonts)
    y = _draw_footer(draw, y, date, transcript_size, model, fonts)

    final_height = min(CANVAS_HEIGHT, y + 50)
    canvas = canvas.crop((0, 0, WIDTH, final_height))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output_path, "PNG", optimize=True)
    return output_path.resolve()


def _fonts(settings: AppSettings) -> dict[str, ImageFont.ImageFont]:
    return {
        "hero": _font(settings, 42, bold=True),
        "title": _font(settings, 30, bold=True),
        "section": _font(settings, 25, bold=True),
        "body": _font(settings, 21),
        "body_bold": _font(settings, 21, bold=True),
        "small": _font(settings, 17),
        "small_bold": _font(settings, 17, bold=True),
        "metric": _font(settings, 32, bold=True),
        "utility": _utility_font(settings, 15),
        "quote": _font(settings, 23, bold=True),
    }


def _draw_header(
    draw: ImageDraw.ImageDraw,
    y: int,
    group_name: str,
    date: str,
    fonts: Mapping[str, ImageFont.ImageFont],
) -> int:
    avatar_box = (CONTENT_LEFT, y, CONTENT_LEFT + 86, y + 86)
    draw.rounded_rectangle(avatar_box, radius=24, fill=DEEP_BLUE)
    draw.ellipse((CONTENT_LEFT + 18, y + 18, CONTENT_LEFT + 68, y + 68), fill="#dcecf7")
    _center_text(draw, "群", (CONTENT_LEFT + 43, y + 43), fonts["title"], DEEP_BLUE)
    title = _fit_line(
        draw,
        _report_title(group_name, date),
        fonts["hero"],
        CONTENT_RIGHT - CONTENT_LEFT - 108,
    )
    draw.text(
        (CONTENT_LEFT + 108, y + 17),
        title,
        font=fonts["hero"],
        fill=INK,
    )
    return y + 106


def _draw_metrics(
    draw: ImageDraw.ImageDraw,
    y: int,
    summary: Mapping[str, Any],
    fonts: Mapping[str, ImageFont.ImageFont],
) -> int:
    metrics = (
        ("消息总数", summary.get("total_messages", 0), "条"),
        ("参与群友", summary.get("active_users", 0), "人"),
        ("文字总量", summary.get("total_text_chars", 0), "字"),
        ("图片投放", summary.get("total_images", 0), "张"),
    )
    gap = 14
    card_width = (CONTENT_RIGHT - CONTENT_LEFT - gap * 3) // 4
    for index, (label, value, unit) in enumerate(metrics):
        left = CONTENT_LEFT + index * (card_width + gap)
        right = left + card_width
        fill = SOFT_ORANGE if index == 0 else "#f7f9fc"
        draw.rounded_rectangle((left, y, right, y + 102), radius=14, fill=fill, outline=LINE)
        draw.text((left + 18, y + 17), label, font=fonts["small"], fill=MUTED)
        value_text = f"{int(value or 0):,}"
        draw.text((left + 18, y + 45), value_text, font=fonts["metric"], fill=INK)
        value_width = _text_width(draw, value_text, fonts["metric"])
        draw.text(
            (left + 22 + value_width, y + 58), unit, font=fonts["small"], fill=MUTED
        )
    return y + 128


def _draw_peak_ribbon(
    draw: ImageDraw.ImageDraw,
    y: int,
    summary: Mapping[str, Any],
    fonts: Mapping[str, ImageFont.ImageFont],
) -> int:
    peak = summary.get("peak_hour") or {}
    hour = int(peak.get("hour") or 0)
    count = int(peak.get("message_count") or 0)
    for x in range(CONTENT_LEFT, CONTENT_RIGHT):
        ratio = (x - CONTENT_LEFT) / max(1, CONTENT_RIGHT - CONTENT_LEFT)
        color = _blend(DEEP_BLUE, BLUE, ratio)
        draw.line((x, y, x, y + 108), fill=color)
    draw.rounded_rectangle(
        (CONTENT_LEFT, y, CONTENT_RIGHT, y + 108), radius=18, outline="#4f89b8", width=2
    )
    draw.text(
        (CONTENT_LEFT + 28, y + 18),
        "HIGHLIGHT TIME · 最活跃时段",
        font=fonts["small"],
        fill="#cae7fa",
    )
    draw.text(
        (CONTENT_LEFT + 28, y + 49),
        f"{hour:02d}:00—{(hour + 1) % 24:02d}:00",
        font=fonts["title"],
        fill="#ffffff",
    )
    note = f"这一小时贡献了 {count} 条消息"
    note_width = _text_width(draw, note, fonts["body"])
    draw.text((CONTENT_RIGHT - note_width - 28, y + 58), note, font=fonts["body"], fill="#ecf7ff")
    return y + 138


def _draw_hourly_chart(
    draw: ImageDraw.ImageDraw,
    y: int,
    summary: Mapping[str, Any],
    fonts: Mapping[str, ImageFont.ImageFont],
) -> int:
    y = _section_heading(draw, y, "24H 活跃轨迹", "HOURLY", fonts)
    chart_top = y + 14
    chart_bottom = chart_top + 190
    draw.rounded_rectangle(
        (CONTENT_LEFT, chart_top, CONTENT_RIGHT, chart_bottom + 42),
        radius=16,
        fill="#fbfcfe",
        outline=LINE,
    )
    counts = [int(value or 0) for value in summary.get("hourly_counts", [])][:24]
    counts.extend([0] * (24 - len(counts)))
    peak_hour = int((summary.get("peak_hour") or {}).get("hour") or -1)
    baseline = chart_bottom
    plot_left = CONTENT_LEFT + 24
    plot_right = CONTENT_RIGHT - 24
    bar_slot = (plot_right - plot_left) / 24
    max_count = max(counts) if counts else 0
    draw.line((plot_left, baseline, plot_right, baseline), fill=LINE, width=2)
    for hour, count in enumerate(counts):
        bar_height = 4 if max_count <= 0 else max(4, int(148 * count / max_count))
        left = int(plot_left + hour * bar_slot + 4)
        right = int(plot_left + (hour + 1) * bar_slot - 4)
        color = ORANGE if hour == peak_hour else TEAL
        draw.rounded_rectangle((left, baseline - bar_height, right, baseline), radius=4, fill=color)
        if count and (hour == peak_hour or count >= max_count * 0.7):
            label = str(count)
            _center_text(draw, label, ((left + right) // 2, baseline - bar_height - 12), fonts["utility"], MUTED)
        if hour % 2 == 0:
            _center_text(draw, f"{hour:02d}", ((left + right) // 2, baseline + 19), fonts["utility"], MUTED)
    return chart_bottom + 70


def _draw_atmosphere(
    draw: ImageDraw.ImageDraw,
    y: int,
    digest: Mapping[str, Any],
    fonts: Mapping[str, ImageFont.ImageFont],
) -> int:
    y = _section_heading(draw, y, "群聊氛围", "REVIEW", fonts)
    atmosphere = digest.get("atmosphere") or {}
    score = max(0, min(100, int(atmosphere.get("score") or 0)))
    label = str(atmosphere.get("label") or "平稳在线")
    comment = str(atmosphere.get("comment") or "今天的聊天节奏平稳，大家各自留下了一点声音。")
    height = 150
    draw.rounded_rectangle(
        (CONTENT_LEFT, y + 12, CONTENT_RIGHT, y + height), radius=16, fill="#fbfcfe", outline=LINE
    )
    ring = (CONTENT_LEFT + 28, y + 38, CONTENT_LEFT + 108, y + 118)
    draw.arc(ring, start=-90, end=269, fill="#dfe6ed", width=12)
    draw.arc(ring, start=-90, end=-90 + int(359 * score / 100), fill=TEAL, width=12)
    _center_text(draw, str(score), (CONTENT_LEFT + 68, y + 78), fonts["small_bold"], INK)
    draw.text((CONTENT_LEFT + 142, y + 34), label, font=fonts["title"], fill=INK)
    lines = _wrap_text(draw, comment, fonts["body"], CONTENT_RIGHT - CONTENT_LEFT - 190, max_lines=2)
    _draw_lines(draw, lines, CONTENT_LEFT + 143, y + 79, fonts["body"], MUTED, 31)
    return y + height + 28


def _draw_topics(
    draw: ImageDraw.ImageDraw,
    y: int,
    digest: Mapping[str, Any],
    fonts: Mapping[str, ImageFont.ImageFont],
) -> int:
    topics = list(digest.get("topics") or [])[:5]
    if not topics:
        return y
    y = _section_heading(draw, y, "今日话题", "TOPICS", fonts)
    for index, topic in enumerate(topics, 1):
        title = str(topic.get("title") or f"话题 {index}")
        summary = str(topic.get("summary") or "")
        people = topic.get("participants") or []
        if isinstance(people, str):
            people_text = people
        else:
            people_text = "、".join(str(item) for item in people[:5])
        lines = _wrap_text(draw, summary, fonts["body"], CONTENT_RIGHT - CONTENT_LEFT - 86, max_lines=4)
        height = 94 + len(lines) * 30 + (28 if people_text else 0)
        top = y + 12
        draw.rounded_rectangle((CONTENT_LEFT, top, CONTENT_RIGHT, top + height), radius=16, fill="#fbfcfe", outline=LINE)
        draw.rounded_rectangle((CONTENT_LEFT + 18, top + 18, CONTENT_LEFT + 54, top + 54), radius=10, fill=SOFT_BLUE)
        _center_text(draw, str(index), (CONTENT_LEFT + 36, top + 36), fonts["small_bold"], BLUE)
        draw.text((CONTENT_LEFT + 72, top + 18), title, font=fonts["body_bold"], fill=INK)
        _draw_lines(draw, lines, CONTENT_LEFT + 72, top + 56, fonts["body"], "#506177", 30)
        if people_text:
            draw.text(
                (CONTENT_LEFT + 72, top + height - 34),
                "在场：" + _clip(people_text, 48),
                font=fonts["small"],
                fill=TEAL,
            )
        y = top + height + 14
    return y + 18


def _draw_portraits(
    draw: ImageDraw.ImageDraw,
    y: int,
    digest: Mapping[str, Any],
    names: Mapping[int, str],
    fonts: Mapping[str, ImageFont.ImageFont],
) -> int:
    portraits = list(digest.get("portraits") or [])[:6]
    if not portraits:
        return y
    y = _section_heading(draw, y, "群友画像", "PORTRAITS", fonts)
    gap = 16
    card_width = (CONTENT_RIGHT - CONTENT_LEFT - gap) // 2
    for row_start in range(0, len(portraits), 2):
        row = portraits[row_start : row_start + 2]
        prepared = []
        for portrait in row:
            description = str(portrait.get("description") or "")
            lines = _wrap_text(draw, description, fonts["small"], card_width - 42, max_lines=4)
            prepared.append((portrait, lines, max(166, 106 + len(lines) * 25)))
        row_height = max(item[2] for item in prepared)
        for column, (portrait, lines, _) in enumerate(prepared):
            left = CONTENT_LEFT + column * (card_width + gap)
            top = y + 12
            draw.rounded_rectangle((left, top, left + card_width, top + row_height), radius=16, fill="#fbfcfe", outline=LINE)
            user_id = _safe_int(portrait.get("user_id"))
            name = str(portrait.get("name") or names.get(user_id, str(user_id or "群友")))
            color = _identity_color(user_id or row_start + column)
            draw.ellipse((left + 20, top + 20, left + 72, top + 72), fill=color)
            _center_text(draw, _initial(name), (left + 46, top + 46), fonts["body_bold"], "#ffffff")
            draw.text((left + 88, top + 17), _clip(name, 14), font=fonts["body_bold"], fill=INK)
            title = str(portrait.get("title") or "今日群友")
            draw.text((left + 88, top + 50), _clip(title, 18), font=fonts["small"], fill=BLUE)
            tags = portrait.get("tags") or []
            tags_text = " · ".join(str(tag) for tag in tags[:3]) if not isinstance(tags, str) else tags
            if tags_text:
                draw.text(
                    (left + 22, top + 86),
                    _clip(tags_text, 30),
                    font=fonts["small"],
                    fill=ORANGE,
                )
            _draw_lines(draw, lines, left + 22, top + 111, fonts["small"], "#56677c", 25)
        y += row_height + 16
    return y + 30


def _draw_quotes(
    draw: ImageDraw.ImageDraw,
    y: int,
    digest: Mapping[str, Any],
    fonts: Mapping[str, ImageFont.ImageFont],
) -> int:
    quotes = list(digest.get("quotes") or [])[:5]
    if not quotes:
        return y
    y = _section_heading(draw, y, "群聊金句", "QUOTES", fonts)
    for quote in quotes:
        name = str(quote.get("name") or "群友")
        quote_text = str(quote.get("quote") or "")
        comment = str(quote.get("comment") or "")
        quote_lines = _wrap_text(draw, quote_text, fonts["quote"], CONTENT_RIGHT - CONTENT_LEFT - 100, max_lines=3)
        comment_lines = _wrap_text(draw, comment, fonts["small"], CONTENT_RIGHT - CONTENT_LEFT - 100, max_lines=2)
        height = 74 + len(quote_lines) * 33 + (len(comment_lines) * 25 + 20 if comment_lines else 0)
        top = y + 12
        draw.rounded_rectangle((CONTENT_LEFT, top, CONTENT_RIGHT, top + height), radius=16, fill="#fffdf9", outline="#eee5d8")
        draw.ellipse((CONTENT_LEFT + 20, top + 20, CONTENT_LEFT + 68, top + 68), fill="#b96f4d")
        _center_text(draw, "言", (CONTENT_LEFT + 44, top + 44), fonts["small_bold"], "#ffffff")
        draw.text((CONTENT_LEFT + 84, top + 20), name, font=fonts["small_bold"], fill="#8f563c")
        _draw_lines(draw, quote_lines, CONTENT_LEFT + 84, top + 52, fonts["quote"], INK, 33)
        if comment_lines:
            divider_y = top + 62 + len(quote_lines) * 33
            draw.line((CONTENT_LEFT + 84, divider_y, CONTENT_RIGHT - 24, divider_y), fill="#eee3d7")
            _draw_lines(draw, comment_lines, CONTENT_LEFT + 84, divider_y + 13, fonts["small"], MUTED, 25)
        y = top + height + 14
    return y + 22


def _draw_closing(
    draw: ImageDraw.ImageDraw,
    y: int,
    digest: Mapping[str, Any],
    fonts: Mapping[str, ImageFont.ImageFont],
) -> int:
    closing = str(digest.get("closing") or "今天的群聊已装订成册，明天继续见。")
    lines = _wrap_text(draw, closing, fonts["body"], CONTENT_RIGHT - CONTENT_LEFT - 150, max_lines=4)
    height = max(126, 58 + len(lines) * 31)
    for x in range(CONTENT_LEFT, CONTENT_RIGHT):
        ratio = (x - CONTENT_LEFT) / max(1, CONTENT_RIGHT - CONTENT_LEFT)
        draw.line((x, y, x, y + height), fill=_blend(DEEP_BLUE, BLUE, ratio))
    draw.rounded_rectangle((CONTENT_LEFT, y, CONTENT_RIGHT, y + height), radius=18, outline="#4f89b8", width=2)
    draw.rounded_rectangle((CONTENT_LEFT + 22, y + 24, CONTENT_LEFT + 92, y + 94), radius=20, fill="#ddecf7")
    _center_text(draw, "结", (CONTENT_LEFT + 57, y + 59), fonts["title"], DEEP_BLUE)
    draw.text((CONTENT_LEFT + 116, y + 22), "今日收束", font=fonts["small_bold"], fill="#cce8fa")
    _draw_lines(draw, lines, CONTENT_LEFT + 116, y + 54, fonts["body"], "#ffffff", 31)
    return y + height + 28


def _draw_footer(
    draw: ImageDraw.ImageDraw,
    y: int,
    date: str,
    transcript_size: int,
    model: str,
    fonts: Mapping[str, ImageFont.ImageFont],
) -> int:
    labels = (
        ("记录日期", date),
        ("聊天文件", _format_bytes(transcript_size)),
        ("AI 总结", _clip(model, 28)),
    )
    gap = 12
    width = (CONTENT_RIGHT - CONTENT_LEFT - gap * 2) // 3
    for index, (label, value) in enumerate(labels):
        left = CONTENT_LEFT + index * (width + gap)
        draw.rounded_rectangle((left, y, left + width, y + 76), radius=14, fill="#f7f9fc", outline=LINE)
        _center_text(draw, label, (left + width // 2, y + 23), fonts["small"], MUTED)
        _center_text(draw, value, (left + width // 2, y + 51), fonts["small_bold"], INK)
    return y + 100


def _section_heading(
    draw: ImageDraw.ImageDraw,
    y: int,
    title: str,
    chip: str,
    fonts: Mapping[str, ImageFont.ImageFont],
) -> int:
    draw.rounded_rectangle((CONTENT_LEFT, y + 5, CONTENT_LEFT + 6, y + 35), radius=3, fill=ORANGE)
    draw.text((CONTENT_LEFT + 18, y), title, font=fonts["section"], fill=INK)
    chip_width = _text_width(draw, chip, fonts["utility"]) + 24
    draw.rounded_rectangle((CONTENT_RIGHT - chip_width, y + 4, CONTENT_RIGHT, y + 32), radius=14, fill=DEEP_BLUE)
    draw.text((CONTENT_RIGHT - chip_width + 12, y + 8), chip, font=fonts["utility"], fill="#ffffff")
    draw.line((CONTENT_LEFT, y + 48, CONTENT_RIGHT, y + 48), fill=LINE)
    return y + 55


def _font(settings: AppSettings, size: int, *, bold: bool = False) -> ImageFont.ImageFont:
    candidates = (
        settings.steam_font_path,
        Path(
            "/usr/local/share/fonts/sarasa-gothic/SarasaGothicSC-Bold.ttf"
            if bold
            else "/usr/local/share/fonts/sarasa-gothic/SarasaGothicSC-Regular.ttf"
        ),
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
    )
    for candidate in candidates:
        if candidate and candidate.is_file():
            return ImageFont.truetype(str(candidate), size=size)
    return ImageFont.load_default()


def _utility_font(settings: AppSettings, size: int) -> ImageFont.ImageFont:
    candidates = (
        Path(
            "/usr/local/share/fonts/jetbrains-mono-nerd/"
            "JetBrainsMonoNerdFontMono-SemiBold.ttf"
        ),
        Path("C:/Windows/Fonts/consola.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"),
    )
    for candidate in candidates:
        if candidate.is_file():
            return ImageFont.truetype(str(candidate), size=size)
    return _font(settings, size)


def _wrap_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
    max_width: int,
    *,
    max_lines: int | None = None,
) -> list[str]:
    normalized = str(text or "").replace("\r", "")
    lines: list[str] = []
    current = ""
    for char in normalized:
        if char == "\n":
            lines.append(current)
            current = ""
            continue
        candidate = current + char
        if current and _text_width(draw, candidate, font) > max_width:
            lines.append(current.rstrip())
            current = char.lstrip()
        else:
            current = candidate
    if current or not lines:
        lines.append(current.rstrip())
    if max_lines is not None and len(lines) > max_lines:
        lines = lines[:max_lines]
        tail = lines[-1]
        while tail and _text_width(draw, tail + "…", font) > max_width:
            tail = tail[:-1]
        lines[-1] = tail.rstrip() + "…"
    return lines


def _draw_lines(
    draw: ImageDraw.ImageDraw,
    lines: Sequence[str],
    x: int,
    y: int,
    font: ImageFont.ImageFont,
    fill: str,
    line_height: int,
) -> None:
    for index, line in enumerate(lines):
        draw.text((x, y + index * line_height), line, font=font, fill=fill)


def _text_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> int:
    bounds = draw.textbbox((0, 0), str(text), font=font)
    return int(bounds[2] - bounds[0])


def _center_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    center: tuple[int, int],
    font: ImageFont.ImageFont,
    fill: str,
) -> None:
    bounds = draw.textbbox((0, 0), str(text), font=font)
    width = bounds[2] - bounds[0]
    height = bounds[3] - bounds[1]
    draw.text((center[0] - width / 2, center[1] - height / 2 - bounds[1]), str(text), font=font, fill=fill)


def _identity_color(value: int) -> str:
    colors = ("#4b88b5", "#6a92a8", "#c07a58", "#6b9b82", "#9279a9", "#c09449")
    digest = hashlib.sha256(str(value).encode("utf-8")).digest()[0]
    return colors[digest % len(colors)]


def _initial(name: str) -> str:
    value = str(name or "群友").strip()
    return value[0] if value else "群"


def _clip(value: str, length: int) -> str:
    normalized = " ".join(str(value or "").split())
    return normalized if len(normalized) <= length else normalized[: max(1, length - 1)] + "…"


def _fit_line(
    draw: ImageDraw.ImageDraw,
    value: str,
    font: ImageFont.ImageFont,
    max_width: int,
) -> str:
    text = " ".join(str(value or "").split())
    if _text_width(draw, text, font) <= max_width:
        return text
    while text and _text_width(draw, text + "…", font) > max_width:
        text = text[:-1]
    return text.rstrip() + "…"


def _report_title(group_name: str, date: str) -> str:
    try:
        parsed = calendar_date.fromisoformat(str(date))
        date_label = f"{parsed.month}月{parsed.day}日"
    except ValueError:
        date_label = str(date)
    return f"{str(group_name).strip()}{date_label}总结"


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _format_bytes(size: int) -> str:
    value = max(0, int(size or 0))
    if value < 1024:
        return f"{value} B"
    if value < 1024 * 1024:
        return f"{value / 1024:.1f} KB"
    return f"{value / 1024 / 1024:.1f} MB"


def _blend(first: str, second: str, ratio: float) -> tuple[int, int, int]:
    ratio = max(0.0, min(1.0, float(ratio)))
    left = tuple(int(first[index : index + 2], 16) for index in (1, 3, 5))
    right = tuple(int(second[index : index + 2], 16) for index in (1, 3, 5))
    return tuple(int(a + (b - a) * ratio) for a, b in zip(left, right, strict=True))
