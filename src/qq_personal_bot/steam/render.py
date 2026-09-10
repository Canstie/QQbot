from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont, ImageOps

from qq_personal_bot.settings import AppSettings

_BACKGROUND = "#101b2d"
_PANEL = "#182a43"
_TEXT = "#f4f8ff"
_MUTED = "#9bb0cb"
_START = "#67e8b5"
_END = "#ffb36a"
_NETWORK = "#73b7ff"


def render_status_card(
    settings: AppSettings,
    *,
    event_type: str,
    player: dict[str, Any],
    session: dict[str, Any],
    alias: str = "",
    cover_image: bytes | None = None,
) -> Path:
    output_dir = Path("data/steam_cache/cards")
    output_dir.mkdir(parents=True, exist_ok=True)
    signature = hashlib.sha256(
        f"{event_type}:{session['session_key']}:{alias}".encode()
    ).hexdigest()[:20]
    output_path = output_dir / f"{signature}.png"
    if output_path.is_file():
        return output_path

    image = Image.new("RGB", (960, 420), _BACKGROUND)
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((34, 34, 926, 386), radius=28, fill=_PANEL)
    text_width = 790
    if cover_image:
        try:
            cover = Image.open(BytesIO(cover_image)).convert("RGB")
            cover = ImageOps.fit(cover, (370, 352), method=Image.Resampling.LANCZOS)
            cover.putalpha(185)
            image.paste(cover, (556, 34), cover)
            draw.rounded_rectangle((556, 34, 926, 386), radius=28, outline=_PANEL, width=3)
            text_width = 430
        except (OSError, ValueError):
            pass
    accent = {"start": _START, "end": _END, "network_recovered": _NETWORK}.get(
        event_type, _NETWORK
    )
    draw.rounded_rectangle((34, 34, 52, 386), radius=9, fill=accent)

    title_font = _font(settings, 44, bold=True)
    body_font = _font(settings, 30)
    small_font = _font(settings, 22)
    label = {
        "start": "开始游戏",
        "end": "结束游戏",
        "network_recovered": "已恢复游戏",
    }.get(event_type, "Steam 状态")
    display_name = alias or player.get("persona_name") or player.get("steam_id") or "Steam 玩家"
    game_name = session.get("game_name") or session.get("game_id") or "未知游戏"
    draw.text((88, 72), label, fill=accent, font=small_font)
    draw.text((88, 114), _fit_text(draw, str(display_name), title_font, text_width), fill=_TEXT, font=title_font)
    draw.text((88, 196), _fit_text(draw, str(game_name), body_font, text_width), fill=_TEXT, font=body_font)

    if event_type == "end" and session.get("ended_at"):
        seconds = max(0, float(session["ended_at"]) - float(session["started_at"]))
        detail = f"本次游玩 {_duration(seconds)}"
    else:
        timestamp = float(session.get("started_at") or 0)
        detail = f"会话开始于 {_clock(timestamp)}" if timestamp else "状态刚刚更新"
    draw.text((88, 290), detail, fill=_MUTED, font=small_font)
    draw.text((754, 342), "STEAM / LIVE", fill=accent, font=small_font)
    image.save(output_path, optimize=True)
    return output_path


def _font(settings: AppSettings, size: int, *, bold: bool = False) -> ImageFont.ImageFont:
    candidates = [
        settings.steam_font_path,
        Path("C:/Windows/Fonts/msyhbd.ttc" if bold else "C:/Windows/Fonts/msyh.ttc"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc" if bold else "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ]
    for candidate in candidates:
        if candidate and candidate.is_file():
            try:
                return ImageFont.truetype(str(candidate), size=size)
            except OSError:
                continue
    return ImageFont.load_default(size=size)


def _fit_text(draw: ImageDraw.ImageDraw, value: str, font: ImageFont.ImageFont, width: int) -> str:
    text = value
    while text and draw.textbbox((0, 0), text, font=font)[2] > width:
        text = text[:-1]
    return text if text == value else f"{text[:-1]}…"


def _duration(seconds: float) -> str:
    minutes = max(1, round(seconds / 60))
    hours, remaining = divmod(minutes, 60)
    if hours:
        return f"{hours} 小时 {remaining} 分钟"
    return f"{remaining} 分钟"


def _clock(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, UTC).astimezone().strftime("%H:%M")
