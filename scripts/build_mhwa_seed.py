"""Convert the Chinese MHWA workbook to reproducible bot seed data (stdlib only)."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def read_workbook(path: Path) -> dict[str, list[dict]]:
    with zipfile.ZipFile(path) as archive:
        shared = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            shared = ["".join(x.itertext()) for x in root.findall("s:si", NS)]
        rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        targets = {r.attrib["Id"]: r.attrib["Target"] for r in rels}
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        result = {}
        for sheet in workbook.findall("s:sheets/s:sheet", NS):
            target = targets[sheet.attrib[f"{{{REL_NS}}}id"]]
            filename = target.lstrip("/") if target.startswith("/") else f"xl/{target}"
            xml = ET.fromstring(archive.read(filename))
            rows = []
            header = {}
            for row in xml.findall("s:sheetData/s:row", NS):
                number = int(row.attrib["r"])
                values = {}
                for cell in row.findall("s:c", NS):
                    column = re.match(r"[A-Z]+", cell.attrib["r"])[0]
                    kind = cell.attrib.get("t")
                    node = cell.find("s:v", NS)
                    value = node.text if node is not None else None
                    if kind == "s" and value is not None:
                        value = shared[int(value)]
                    elif kind == "inlineStr":
                        value = "".join(cell.find("s:is", NS).itertext())
                    elif kind == "b" and value is not None:
                        value = value == "1"
                    elif value is not None and kind in {None, "n"}:
                        numeric = float(value)
                        value = int(numeric) if numeric.is_integer() else numeric
                    values[column] = value
                if number == 3 and sheet.attrib["name"] == "怪物总览":
                    result["_source_notes"] = [{"text": values.get("A", "")}]
                if number == 6:
                    header = {k: v for k, v in values.items() if v is not None}
                elif number > 6 and any(v is not None for v in values.values()):
                    rows.append({name: values.get(col) for col, name in header.items()})
            result[sheet.attrib["name"]] = rows
        return result


def build_seed(path: Path) -> dict:
    sheets = read_workbook(path)
    note = sheets.get("_source_notes", [{"text": ""}])[0]["text"]
    source = re.search(r"来源：(\S+).*数据版本\s+(\d+).*保存日期\s+(\d{4}-\d{2}-\d{2})", note)
    if source is None:
        raise ValueError("怪物总览缺少来源、数据版本或保存日期。")
    modifiers: dict[str, dict] = {}
    for row in sheets["怪物补充参数"]:
        route = json.loads(row["原始字段路径"])
        if len(route) == 5 and route[:2] == ["RAW", "Monster Data"] and route[3] == "Legendary":
            modifiers.setdefault(str(route[2]), {})[route[4]] = row["原始内容"]
    random_rates = {}
    for row in sheets["其他补充参数"]:
        route = json.loads(row["原始字段路径"])
        if route[:4] == ["RAW", "Difficulty", "_RandomRateTblArray", 0]:
            random_rates[route[4]] = row["原始内容"]
    monsters = [
        {
            "id": r["怪物编号"],
            "name": r["怪物名称"],
            "name_en": r["英文名称"],
            "base_hp": r["基础血量"],
            "capture_allowed": r["捕获限制"] == "可捕获",
            "modifiers": modifiers.get(r["怪物编号"], {}),
        }
        for r in sheets["怪物总览"]
    ]
    quests = [
        {
            "id": r["任务编号"],
            "name": r["任务名称"],
            "name_original": r["原始任务名"],
            "stars": r["星级"],
            "strength": r["个体强度"],
            "type": r["任务类型"],
            "condition": r["完成条件"],
            "map": r["地图"],
            "environment": r["环境"],
            "max_players": r["最多人数"],
        }
        for r in sheets["任务"]
    ]
    targets = [
        {
            "quest_id": r["任务编号"],
            "monster_id": r["怪物编号"],
            "ordinal": r["目标序号"],
            "legendary": {"普通": 0, "历战": 1, "历战王": 2}[r["历战类型"]],
            "health_adjust": r["血量浮动档位"],
            "difficulty_id": r["难度倍率编号"],
            "role_id": r["角色编号"],
        }
        for r in sheets["任务目标"]
    ]
    elements = ["斩击", "打击", "射击", "火", "水", "雷", "冰", "龙", "气绝"]
    hitzones = [
        {
            "monster_id": r["怪物编号"],
            "part": r["部位"],
            "raw_part": r["原始部位名"],
            "group": r["肉质组"],
            "state": r["状态"],
            "values": [r[k] for k in elements],
            "note": r["说明"],
            "source": r["肉质来源"],
        }
        for r in sheets["肉质"]
    ]
    thresholds = ["硬直", "破坏", "打出绽伤血量", "绽伤血量", "伤口血量", "伤口数"]
    parts = [
        {
            "monster_id": r["怪物编号"],
            "part": r["部位"],
            "raw_part": r["原始部位名"],
            "state": r["状态"],
            "kind": r["个体类型"],
            "stage": r["阶段"],
            "values": [r[k] for k in thresholds],
        }
        for r in sheets["部位与伤口"]
    ]
    # Translate mirrored parts using the same monster's already translated base part.
    for part in parts:
        raw = part["raw_part"] or ""
        match = re.fullmatch(r"(Left|Right) (.+?)( AT)?", raw)
        if match and part["part"] == raw:
            base = next(
                (
                    p["part"]
                    for p in parts
                    if p["monster_id"] == part["monster_id"] and p["raw_part"] == match[2]
                ),
                None,
            )
            if base:
                part["part"] = ("左" if match[1] == "Left" else "右") + base
                if match[3]:
                    part["part"] += "（历战王）"
    statuses = [
        "毒",
        "睡眠",
        "麻痹",
        "爆破",
        "气绝",
        "疲劳",
        "骑乘",
        "相杀",
        "锁刃刺击",
        "属性变换",
    ]
    conditions = [
        {"monster_id": r["怪物编号"], "label": r["项目"], "values": [r[k] for k in statuses]}
        for r in sheets["异常状态"]
    ]
    blowouts = [
        {
            "monster_id": r["怪物编号"],
            "part": r["部位或状态"],
            "values": [r[f"炸膛{i}"] for i in range(1, 5)],
        }
        for r in sheets["炸膛"]
    ]
    skills = [
        {
            "id": r["技能编号"],
            "name": r["技能名称"],
            "max_level": r["最高等级"],
            "kind": r["技能种类"],
            "category": r["效果类别"],
            "condition": r["触发条件"],
        }
        for r in sheets["技能"]
    ]
    levels = [
        {
            "skill_id": r["技能编号"],
            "level": r["等级"],
            "description": r["效果说明"],
            "special": r["特殊效果"],
        }
        for r in sheets["技能等级"]
    ]
    effects = [
        {
            "skill_id": r["技能编号"],
            "level": r["等级"],
            "effect": r["效果类型"],
            "value": r["数值"],
            "condition": r["触发条件"],
            "target": r["作用对象"],
        }
        for r in sheets["技能效果"]
    ]
    return {
        "format_version": 1,
        "data_version": int(source[2]),
        "saved_date": source[3],
        "source": source[1],
        "workbook_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "random_health_rates": random_rates,
        "monsters": monsters,
        "quests": quests,
        "quest_monsters": targets,
        "difficulty_profiles": sheets["难度倍率"],
        "multiplayer_profiles": sheets["多人倍率"],
        "hitzones": hitzones,
        "part_thresholds": parts,
        "conditions": conditions,
        "blowouts": blowouts,
        "skills": skills,
        "skill_levels": levels,
        "skill_effects": effects,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workbook", type=Path)
    parser.add_argument("--output", type=Path, default=Path("tools/mhwa/seed.json"))
    args = parser.parse_args()
    seed = build_seed(args.workbook)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(seed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Seed saved: {args.output}; {len(seed['quests'])} quests, {len(seed['skills'])} skills")


if __name__ == "__main__":
    main()
