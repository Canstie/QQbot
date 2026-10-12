from __future__ import annotations

import hashlib
import json
import os
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw

from qq_personal_bot.mhwa.calculate import ELEMENT_NAMES, STATUS_NAMES
from qq_personal_bot.settings import AppSettings
from qq_personal_bot.steam.render import _font

WIDTH = 1440
MAX_HEIGHT = 2400
MARGIN = 48
CONTENT_WIDTH = WIDTH - MARGIN * 2
_CARD_VERSION = 1
_BG = "#17140f"
_PANEL = "#211c14"
_HEADER = "#332919"
_LINE = "#4c3d23"
_TEXT = "#eee1c8"
_MUTED = "#b5a68c"
_GOLD = "#eab94f"


@dataclass
class Table:
    title: str
    headers: list[str]
    rows: list[list[object]]
    weights: list[float] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    meat: bool = False


def display(value: object) -> str:
    if value is None or value == "":
        return "—"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def skill_tables(skill: dict) -> list[Table]:
    rows = []
    for level in skill["levels"]:
        description = level["description"] or "资料未提供效果说明"
        description = description.replace("%攻击伤口", "%\n攻击伤口")
        if level["special"]:
            description += f"\n{level['special']}"
        rows.append([f"Lv.{level['level']}", description])
    tables = [Table("全部等级效果", ["等级", "效果说明"], rows, [1, 7])]
    if skill["id"] == "HunterSkill_062":
        effects = {
            (r["level"], r["condition"]): r["value"]
            for r in skill["effects"]
            if r["effect"] == "affinity"
        }
        rows = []
        for level in skill["levels"]:
            number = level["level"]
            weak = effects.get((number, "weak_spot"))
            wound = effects.get((number, "wound"))
            if weak is not None and wound is not None:
                rows.append([f"Lv.{number}", f"+{weak}%", f"+{wound}%", f"+{weak + wound}%"])
        tables.append(
            Table(
                "会心率速查",
                ["等级", "有效部位", "伤口额外", "攻击伤口合计"],
                rows,
                notes=["攻击伤口合计为有效部位与伤口额外效果之和。未计入其他会心来源。"],
            )
        )
    return tables


def part_label(part: dict, *, stages: bool = False) -> str:
    label = part["part"]
    if part["state"] != "普通":
        label += f" · {part['state']}"
    if stages:
        label += f" · 阶段{part['stage']}"
    return label


def quest_tables(quest: dict) -> list[Table]:
    targets = quest["targets"]
    if not targets:
        return [Table("任务目标", ["说明"], [["资料没有提供目标怪物。"]])]
    summary = []
    groups: OrderedDict[tuple, list[dict]] = OrderedDict()
    for data in targets:
        monster, target = data["monster"], data["target"]
        kind = ("普通", "历战", "历战王")[target["legendary"]]
        health_range = (
            "固定"
            if data["hp_min"] == data["hp_max"]
            else f"{data['hp_min']:,}～{data['hp_max']:,}"
        )
        summary.append(
            [f"{monster['name']} · 第{target['ordinal']}只", kind, f"{data['hp']:,}", health_range]
        )
        key = (monster["id"], target["difficulty_id"], target["legendary"], target["health_adjust"])
        groups.setdefault(key, []).append(data)
    tables = [
        Table(
            "任务目标 · 单人血量",
            ["目标", "个体类型", "基准血量", "浮动范围"],
            summary,
            [3, 1.4, 1.5, 2.6],
            ["浮动血量同时列出基准值和范围；以下耐久与耐性均已按本任务单人倍率换算。"],
        )
    ]
    for same in groups.values():
        data = same[0]
        monster, target = data["monster"], data["target"]
        kind = ("普通", "历战", "历战王")[target["legendary"]]
        prefix = f"{monster['name']} · {kind}"
        if len(groups) > 1 or len(same) > 1:
            prefix += " · 第" + "、".join(str(d["target"]["ordinal"]) for d in same) + "只"
        normal = [r for r in data["hitzones"] if r["group"] != "伤口"]
        wounds = [r for r in data["hitzones"] if r["group"] == "伤口"]
        if normal:
            rows, notes = [], []
            for row in normal:
                state = row["state"]
                if row["group"] not in {"普通", "历战王"}:
                    state = f"{row['group']} · {state}"
                rows.append([row["part"], state, *row["values"]])
                if row["note"]:
                    note = f"{row['part']}（{state}）：{row['note']}"
                    if note not in notes:
                        notes.append(note)
            tables.append(
                Table(
                    prefix + " / 肉质",
                    ["部位", "状态", *ELEMENT_NAMES],
                    rows,
                    [1.7, 2.5, *([0.75] * 9)],
                    notes,
                    meat=True,
                )
            )
        if wounds:
            tables.append(
                Table(
                    prefix + " / 伤口肉质",
                    ["部位", *ELEMENT_NAMES],
                    [[r["part"], *r["values"]] for r in wounds],
                    [2.3, *([1] * 9)],
                    meat=True,
                )
            )
        if data["parts"]:
            stages = any(p["stage"] > 1 for p in data["parts"])
            tables.append(
                Table(
                    prefix + " / 部位硬直、破坏与伤口",
                    [
                        "部位 / 条件",
                        "硬直",
                        "破坏",
                        "打出绽伤\n血量",
                        "绽伤血量",
                        "伤口血量",
                        "伤口数",
                    ],
                    [[part_label(p, stages=stages), *p["values"]] for p in data["parts"]],
                    [2.5, *([1] * 6)],
                )
            )
        if data["legendary_parts"]:
            stages = any(p["stage"] > 1 for p in data["legendary_parts"])
            tables.append(
                Table(
                    prefix + " / 历战伤口",
                    ["部位 / 条件", "绽伤血量", "伤口血量", "伤口数"],
                    [[part_label(p, stages=stages), *p["values"]] for p in data["legendary_parts"]],
                    [3, 1.4, 1.4, 1],
                )
            )
        if data["blowouts"]:
            tables.append(
                Table(
                    prefix + " / 炸膛",
                    ["部位", "炸膛1", "炸膛2", "炸膛3", "炸膛4"],
                    [[p["part"], *p["values"]] for p in data["blowouts"]],
                    [2, 1, 1, 1, 1],
                )
            )
        if data["conditions"]:
            tables.append(
                Table(
                    prefix + " / 异常状态",
                    ["项目", *STATUS_NAMES],
                    [[p["label"], *p["values"]] for p in data["conditions"]],
                    [1.6, *([1] * 10)],
                    [
                        "持续时间单位为秒；初始耐性、增量与上限分别应用各自倍率。— 表示资料未提供，0 为原始零值。"
                    ],
                )
            )
    return tables


def wrap(draw: ImageDraw.ImageDraw, text: str, font, width: int) -> list[str]:
    result = []
    for paragraph in text.split("\n"):
        line = ""
        for char in paragraph:
            if line and draw.textlength(line + char, font=font) > width:
                result.append(line)
                line = char
            else:
                line += char
        result.append(line)
    return result


def render_cards(
    settings: AppSettings,
    output_dir: Path,
    *,
    title: str,
    subtitle: str,
    tables: list[Table],
    metadata: dict,
    identity: str,
) -> list[Path]:
    signature = hashlib.sha256(
        json.dumps(
            [_CARD_VERSION, metadata["seed_hash"], identity, str(settings.steam_font_path)],
            ensure_ascii=False,
        ).encode()
    ).hexdigest()[:24]
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = output_dir / f"{signature}.json"
    if manifest.is_file():
        cached = [output_dir / name for name in json.loads(manifest.read_text(encoding="utf-8"))]
        if cached and all(p.is_file() for p in cached):
            return cached
    fonts = {
        "title": _font(settings, 44, bold=True),
        "heading": _font(settings, 28, bold=True),
        "body": _font(settings, 24),
        "header": _font(settings, 24, bold=True),
        "small": _font(settings, 21),
    }
    measure = ImageDraw.Draw(Image.new("RGB", (WIDTH, 1)))
    title_lines = wrap(measure, title, fonts["title"], CONTENT_WIDTH)
    subtitle_lines = wrap(measure, subtitle, fonts["small"], CONTENT_WIDTH)
    top = 44 + len(title_lines) * 58 + 14 + len(subtitle_lines) * 30 + 32
    bottom = MAX_HEIGHT - 80
    pages: list[list[tuple | str]] = [[]]
    page_heights = [top]
    for table in tables:
        weights = table.weights or [1] * len(table.headers)
        widths = [int(CONTENT_WIDTH * w / sum(weights)) for w in weights]
        widths[-1] += CONTENT_WIDTH - sum(widths)
        headers = [
            wrap(measure, h, fonts["header"], width - 20)
            for h, width in zip(table.headers, widths, strict=True)
        ]
        header_height = max(len(lines) for lines in headers) * 32 + 22
        rows = []
        for values in table.rows:
            cells = [
                wrap(measure, display(value), fonts["body"], width - 22)
                for value, width in zip(values, widths, strict=True)
            ]
            rows.append((cells, max(len(lines) for lines in cells) * 32 + 22, values))
        notes = []
        for note in table.notes:
            notes.extend(wrap(measure, note, fonts["small"], CONTENT_WIDTH - 24))
        position = 0
        continuation = False
        while position < len(rows):
            heading = 54 + header_height
            if page_heights[-1] + heading + rows[position][1] + 24 > bottom:
                pages.append([])
                page_heights.append(top)
            selected = []
            height = heading
            while position < len(rows):
                row = rows[position]
                if selected and page_heights[-1] + height + row[1] + 24 > bottom:
                    break
                if row[1] + heading > bottom - top:
                    raise ValueError("资料单行过长，无法生成可读图片。")
                selected.append(row)
                height += row[1]
                position += 1
            chunk_notes = notes if position == len(rows) else []
            pages[-1].append((table, continuation, widths, headers, header_height, selected))
            page_heights[-1] += height + 24
            continuation = True
            for line in chunk_notes:
                if page_heights[-1] + 29 > bottom:
                    pages.append([])
                    page_heights.append(top)
                pages[-1].append(line)
                page_heights[-1] += 29
            if chunk_notes:
                page_heights[-1] += 14
    paths = []
    for index, chunks in enumerate(pages, 1):
        height = page_heights[index - 1] + 76
        image = Image.new("RGB", (WIDTH, height), _BG)
        draw = ImageDraw.Draw(image)
        y = 44
        for line in title_lines:
            draw.text((MARGIN, y), line, font=fonts["title"], fill=_TEXT)
            y += 58
        y += 14
        for line in subtitle_lines:
            draw.text((MARGIN, y), line, font=fonts["small"], fill=_MUTED)
            y += 30
        draw.line((MARGIN, y + 14, WIDTH - MARGIN, y + 14), fill=_GOLD, width=2)
        y = top
        for chunk in chunks:
            if isinstance(chunk, str):
                draw.text((MARGIN + 12, y), chunk, font=fonts["small"], fill=_MUTED)
                y += 29
                continue
            table, continuation, widths, headers, header_height, rows = chunk
            draw.rectangle((MARGIN, y + 7, MARGIN + 4, y + 36), fill=_GOLD)
            heading = table.title + ("（续）" if continuation else "")
            draw.text((MARGIN + 18, y), heading, font=fonts["heading"], fill=_GOLD)
            y += 54
            all_rows = [(headers, header_height, table.headers), *rows]
            for row_index, (cells, row_height, originals) in enumerate(all_rows):
                fill = _HEADER if row_index == 0 else (_PANEL if row_index % 2 else "#1b1711")
                draw.rectangle((MARGIN, y, WIDTH - MARGIN, y + row_height), fill=fill)
                x = MARGIN
                font = fonts["header"] if row_index == 0 else fonts["body"]
                for column, (lines, width) in enumerate(zip(cells, widths, strict=True)):
                    color = _TEXT
                    value = originals[column]
                    if table.meat and row_index and isinstance(value, (int, float)):
                        first_value = len(table.headers) - len(ELEMENT_NAMES)
                        threshold = 45 if column - first_value < 3 else 20
                        if (
                            column >= first_value
                            and column < len(table.headers) - 1
                            and value >= threshold
                        ):
                            color = _GOLD
                    text_y = y + (row_height - len(lines) * 32) // 2
                    for line in lines:
                        align_left = row_index > 0 and column < (
                            2 if table.meat and len(table.headers) == 11 else 1
                        )
                        if len(table.headers) == 2 and column == 1:
                            align_left = True
                        text_x = (
                            x + 11
                            if align_left
                            else x + (width - draw.textlength(line, font=font)) / 2
                        )
                        draw.text((text_x, text_y), line, font=font, fill=color)
                        text_y += 32
                    draw.line((x, y, x, y + row_height), fill=_LINE)
                    x += width
                draw.line((MARGIN, y + row_height, WIDTH - MARGIN, y + row_height), fill=_LINE)
                y += row_height
            y += 24
        footer = f"怪物猎人荒野 · 数据 v{metadata['data_version']} · {metadata['saved_date']}"
        draw.text((MARGIN, height - 51), footer, font=fonts["small"], fill=_MUTED)
        pagination = f"{index} / {len(pages)}"
        draw.text(
            (WIDTH - MARGIN - draw.textlength(pagination, font=fonts["small"]), height - 51),
            pagination,
            font=fonts["small"],
            fill=_GOLD,
        )
        path = output_dir / f"{signature}-{index}.png"
        temp = path.with_name(f"{path.stem}-{uuid.uuid4().hex}.tmp")
        image.save(temp, format="PNG", optimize=True)
        os.replace(temp, path)
        paths.append(path)
    manifest.write_text(json.dumps([p.name for p in paths]), encoding="utf-8")
    return paths
