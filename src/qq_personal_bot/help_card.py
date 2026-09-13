from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from PIL import Image, ImageDraw, ImageFont

from qq_personal_bot.settings import AppSettings

_CARD_VERSION = 1


@dataclass(frozen=True)
class HelpSection:
    title: str
    items: tuple[str, ...]


_KNOWN_LUA_COMMANDS = frozenset(
    {
        "help",
        "今日天气",
        "今日人品",
        "今日宜忌",
        "今日菜单",
        "抽群老婆",
        "换个老婆",
        "强娶",
        "存典",
        "爆典",
        "左对称",
        "右对称",
        "上对称",
        "下对称",
        "总结",
    }
)


def build_help_sections(
    settings: AppSettings,
    store: Any,
    *,
    is_admin: bool,
) -> tuple[HelpSection, ...]:
    """Return only commands and capabilities that are currently usable."""

    def enabled(*feature_ids: str) -> bool:
        return all(store.is_feature_enabled(feature_id) for feature_id in feature_ids)

    def lua(command: str, *feature_ids: str) -> bool:
        return enabled("lua.master", f"lua.command.{command}", *feature_ids)

    sections: list[HelpSection] = []

    def add(title: str, items: Iterable[tuple[bool, str]]) -> None:
        visible = tuple(text for allowed, text in items if allowed)
        if visible:
            sections.append(HelpSection(title, visible))

    add(
        "基础与日常",
        (
            (lua("help"), "~help  查看动态功能菜单"),
            (lua("今日天气"), "~今日天气 <地点>  查询天气"),
            (enabled("teacher.lookup"), "~查老师 <姓名> [课程]  查询教师评价"),
            (lua("今日人品"), "~今日人品  查看今日人品"),
            (lua("今日宜忌"), "~今日宜忌  查看今日宜忌"),
            (lua("今日菜单"), "~今日菜单  随机推荐今日吃什么"),
            (lua("今日菜单"), "吃什么 / csm / 今天吃什么  免前缀触发菜单"),
        ),
    )
    add(
        "群老婆",
        (
            (lua("抽群老婆"), "~抽群老婆  抽取今日双向绑定对象"),
            (lua("换个老婆"), "~换个老婆  解除双方关系并重新抽取"),
            (lua("强娶"), "~强娶 @群成员  指定今日群老婆"),
        ),
    )
    add(
        "图片与典图",
        (
            (lua("存典"), "~存典  引用图片保存为本群典图"),
            (lua("爆典"), "~爆典  随机发送本群典图"),
            (enabled("classics.forward_all"), "~爆典all  合并发送本群全部典图"),
            (enabled("gallery.random"), "~涩图  从下载图库随机发送图片"),
            (lua("左对称"), "~左对称  引用图片生成左对称图"),
            (lua("右对称"), "~右对称  引用图片生成右对称图"),
            (lua("上对称"), "~上对称  引用图片生成上对称图"),
            (lua("下对称"), "~下对称  引用图片生成下对称图"),
        ),
    )
    add(
        "群数据与菜单",
        (
            (lua("总结", "activity.record"), "~总结  生成今天的群聊速报长图"),
            (enabled("flows.menu_add"), "~添加菜单  按提示添加菜名和图片"),
            (enabled("flows.restaurant_add"), "~添加饭店  添加店名和招牌菜"),
            (enabled("flows.restaurant_pick"), "~今日饭店  随机抽一家本群饭店"),
            (
                enabled("flows.menu_add") or enabled("flows.restaurant_add"),
                "添加流程中发送“取消”可退出",
            ),
        ),
    )
    add(
        "AI 与卡片",
        (
            (enabled("ai.master", "ai.mention"), "@机器人  与 AI 对话"),
            (enabled("ai.master", "ai.random"), "随机插话  AI 偶尔参与普通群聊"),
            (enabled("cards.bilibili"), "B站分享  自动解析视频图片卡片"),
            (enabled("cards.xiaohongshu"), "小红书分享  自动提取笔记图片"),
            (enabled("cards.xiaoheihe"), "小黑盒分享  自动提取帖子图片"),
            (enabled("replies.fixed"), "固定回复  响应已配置的关键词规则"),
        ),
    )

    steam_base = bool(settings.steam_api_key) and enabled("steam.master", "steam.commands")
    add(
        "Steam",
        (
            (steam_base and enabled("steam.player_lookup"), "/steam list  查看本群监控玩家"),
            (
                steam_base and enabled("steam.player_lookup"),
                "/steam openbox <玩家>  查看玩家详情",
            ),
            (steam_base and enabled("steam.game_detail"), "/steam game <游戏>  查看游戏详情"),
            (steam_base and enabled("steam.price"), "/steam price <游戏>  查询商店价格"),
            (
                steam_base and enabled("steam.player_lookup"),
                "/steamwho @用户  查询已绑定玩家状态",
            ),
        ),
    )

    dynamic_commands = []
    if enabled("lua.master") and settings.lua_dir.is_dir():
        for path in sorted(settings.lua_dir.glob("*.lua"), key=lambda item: item.stem.casefold()):
            if (
                path.stem
                and path.stem not in _KNOWN_LUA_COMMANDS
                and enabled(f"lua.command.{path.stem}")
            ):
                dynamic_commands.append((True, f"~{path.stem}  扩展 Lua 指令"))
    add("扩展指令", dynamic_commands)

    if is_admin:
        add(
            "管理员命令",
            (
                (True, "/check  查看服务器 CPU、内存和磁盘"),
                (True, "/bot status  查看当前群策略"),
                (True, "/bot on|off [群号]  启用或停用群"),
                (True, "/bot aion|aioff [群号]  开关群 AI"),
                (True, "/bot aim list|<模型>  查看或切换模型"),
                (True, "/bot aik list|<序号>  查看或切换知识库"),
                (True, "/bot prefix list|add|remove  管理前缀"),
                (enabled("downloads.ingest"), "/download  下载引用聊天记录中的图片"),
                (enabled("downloads.overview"), "/download_overview  查看下载图库统计"),
                (steam_base, "/steam on|off  开关本群 Steam 监控"),
                (steam_base, "/steam addid|delid  管理监控玩家"),
                (steam_base, "/steam bind|unbind  管理 QQ 绑定"),
                (steam_base, "/steam achievement_on|off  开关成就通知"),
            ),
        )

    return tuple(sections)


def render_help_card(
    settings: AppSettings,
    store: Any,
    *,
    is_admin: bool,
) -> Path:
    sections = build_help_sections(settings, store, is_admin=is_admin)
    signature = hashlib.sha256(
        json.dumps(
            [_CARD_VERSION, [(section.title, section.items) for section in sections]],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()[:20]
    output_dir = settings.db_path.parent / "help_cache"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"help-{'admin' if is_admin else 'public'}-{signature}.png"
    if output_path.is_file():
        return output_path.resolve()

    width = 1200
    outer_padding = 36
    column_gap = 24
    column_width = (width - outer_padding * 2 - column_gap) // 2
    title_font = _font(settings, 48, bold=True)
    subtitle_font = _font(settings, 20)
    section_font = _font(settings, 28, bold=True)
    body_font = _font(settings, 21)
    footer_font = _font(settings, 18)
    measure = ImageDraw.Draw(Image.new("RGB", (1, 1)))

    cards = [
        _prepare_section(measure, section, section_font, body_font, column_width)
        for section in sections
    ]
    columns, column_heights = _balanced_columns(cards)

    header_height = 132
    footer_height = 72
    content_height = max(column_heights) - 18 if cards else 180
    height = header_height + content_height + footer_height + outer_padding * 2
    image = Image.new("RGB", (width, height), "#edf3fb")
    draw = ImageDraw.Draw(image)
    _paint_background(draw, width, height)

    header_box = (18, 18, width - 18, 104)
    draw.rounded_rectangle(header_box, radius=43, fill="#2764b6")
    draw.text((52, 30), "QQBot 功能说明", fill="#ffffff", font=title_font)
    subtitle = "管理员视图" if is_admin else "群员视图"
    subtitle_width = _text_width(draw, subtitle, subtitle_font)
    badge_left = width - 54 - subtitle_width - 38
    draw.rounded_rectangle((badge_left, 37, width - 42, 85), radius=24, fill="#ffffff")
    draw.text((badge_left + 19, 48), subtitle, fill="#2764b6", font=subtitle_font)
    for row in range(3):
        for column in range(9):
            draw.ellipse(
                (810 + column * 18, 29 + row * 18, 816 + column * 18, 35 + row * 18),
                fill="#8eb5e8",
            )

    content_top = header_height
    draw.rounded_rectangle(
        (18, content_top - 10, width - 18, content_top + content_height + 24),
        radius=28,
        fill="#dce9f8",
        outline="#b9cce5",
        width=2,
    )

    for column_index, column in enumerate(columns):
        x = outer_padding + column_index * (column_width + column_gap)
        y = content_top + 10
        for card in column:
            _draw_section(draw, x, y, column_width, card, section_font, body_font)
            y += int(card["height"]) + 18

    enabled_count = sum(len(section.items) for section in sections)
    footer = f"当前展示 {enabled_count} 项可用能力  ·  已关闭功能自动隐藏  ·  网页功能中心修改后立即生效"
    footer_width = _text_width(draw, footer, footer_font)
    draw.text(
        ((width - footer_width) // 2, height - 48),
        footer,
        fill="#607590",
        font=footer_font,
    )
    image.save(output_path, format="PNG", optimize=True)
    return output_path.resolve()


def _prepare_section(
    draw: ImageDraw.ImageDraw,
    section: HelpSection,
    section_font: ImageFont.ImageFont,
    body_font: ImageFont.ImageFont,
    width: int,
) -> dict[str, Any]:
    line_width = width - 72
    wrapped_items = [
        _wrap_text(draw, item, body_font, line_width)
        for item in section.items
    ]
    line_height = 31
    item_gap = 7
    body_height = sum(len(lines) * line_height + item_gap for lines in wrapped_items)
    height = 28 + _text_height(draw, section.title, section_font) + 18 + body_height + 20
    return {"section": section, "items": wrapped_items, "height": height}


def _balanced_columns(
    cards: list[dict[str, Any]],
) -> tuple[list[list[dict[str, Any]]], list[int]]:
    if len(cards) < 2:
        height = sum(int(card["height"]) + 18 for card in cards)
        return [list(cards), []], [height, 0]

    weights = [int(card["height"]) + 18 for card in cards]
    best_mask = 1
    best_difference: int | None = None
    for mask in range(1, 1 << len(cards)):
        if not mask & 1 or mask == (1 << len(cards)) - 1:
            continue
        left_height = sum(weight for index, weight in enumerate(weights) if mask & (1 << index))
        right_height = sum(weights) - left_height
        difference = abs(left_height - right_height)
        if best_difference is None or difference < best_difference:
            best_mask = mask
            best_difference = difference

    columns: list[list[dict[str, Any]]] = [[], []]
    heights = [0, 0]
    for index, card in enumerate(cards):
        column = 0 if best_mask & (1 << index) else 1
        columns[column].append(card)
        heights[column] += weights[index]
    return columns, heights


def _draw_section(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    width: int,
    card: dict[str, Any],
    section_font: ImageFont.ImageFont,
    body_font: ImageFont.ImageFont,
) -> None:
    height = int(card["height"])
    section: HelpSection = card["section"]
    draw.rounded_rectangle(
        (x, y, x + width, y + height),
        radius=22,
        fill="#f9fbfe",
        outline="#c8d8eb",
        width=2,
    )
    draw.rounded_rectangle((x + 18, y + 23, x + 25, y + 59), radius=4, fill="#2f72c8")
    draw.text((x + 38, y + 23), section.title, fill="#26384f", font=section_font)
    cursor_y = y + 76
    for lines in card["items"]:
        draw.ellipse((x + 24, cursor_y + 11, x + 30, cursor_y + 17), fill="#76a7df")
        for line in lines:
            draw.text((x + 42, cursor_y), line, fill="#45566e", font=body_font)
            cursor_y += 31
        cursor_y += 7


def _paint_background(draw: ImageDraw.ImageDraw, width: int, height: int) -> None:
    top = (244, 248, 253)
    bottom = (226, 237, 249)
    for y in range(height):
        ratio = y / max(1, height - 1)
        color = tuple(round(start + (end - start) * ratio) for start, end in zip(top, bottom))
        draw.line((0, y, width, y), fill=color)


def _font(settings: AppSettings, size: int, *, bold: bool = False) -> ImageFont.ImageFont:
    candidates = [
        settings.steam_font_path,
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
    for candidate in candidates:
        if candidate and candidate.is_file():
            try:
                return ImageFont.truetype(str(candidate), size=size)
            except OSError:
                continue
    return ImageFont.load_default(size=size)


def _wrap_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
    max_width: int,
) -> tuple[str, ...]:
    lines: list[str] = []
    current = ""
    for character in text:
        candidate = current + character
        if current and _text_width(draw, candidate, font) > max_width:
            lines.append(current.rstrip())
            current = character.lstrip()
        else:
            current = candidate
    if current:
        lines.append(current.rstrip())
    return tuple(lines or ("",))


def _text_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> int:
    bounds = draw.textbbox((0, 0), text, font=font)
    return bounds[2] - bounds[0]


def _text_height(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> int:
    bounds = draw.textbbox((0, 0), text, font=font)
    return bounds[3] - bounds[1]
