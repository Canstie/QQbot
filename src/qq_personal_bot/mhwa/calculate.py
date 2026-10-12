from __future__ import annotations

import math
import re
from typing import Any

STATUS_NAMES = (
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
)
ELEMENT_NAMES = ("斩", "打", "射", "火", "水", "雷", "冰", "龙", "气绝")


def game_round(value: float) -> int:
    """Match the source viewer's Math.round for nonnegative game parameters."""
    return math.floor(value + 0.5)


def legendary_rate(modifiers: dict, key: str, legendary: int, difficulty: dict) -> float:
    if not legendary:
        return 1.0
    rank = int(re.search(r"(\d+)$", str(difficulty.get("奖励级别·数值") or "0"))[1])
    suffix = "_King" if legendary == 2 else ""
    if (legendary == 2 and rank >= 10) or (legendary == 1 and rank >= 9):
        suffix += "_Hard"
    return float(modifiers.get(key + suffix, 1))


def scale(value: Any, factor: float) -> Any:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return game_round(value * factor)
    return value


def calculate_target(
    monster: dict,
    target: dict,
    difficulty: dict,
    random_rates: dict,
    *,
    players: int = 1,
    multiplayer: dict | None = None,
) -> dict:
    if players != 1 and multiplayer is None:
        raise ValueError("缺少对应人数的倍率数据。")
    multi = multiplayer or {}
    modifiers = monster["modifiers"]
    legendary = target["legendary"]

    def lr(key: str) -> float:
        return legendary_rate(modifiers, key, legendary, difficulty)

    def rate(key: str) -> float:
        value = difficulty.get(key)
        if not isinstance(value, (int, float)):
            raise TypeError(f"任务资料缺少{key}。")
        return float(value)

    health_factor = rate("血量倍率") * lr("HealthRate") * multi.get("血量倍率", 1)
    parts_factor = rate("部位血量倍率") * lr("PartsVitalRate") * multi.get("部位血量倍率", 1)
    wound_factor = rate("伤口血量倍率") * multi.get("伤口血量倍率", 1)
    scar_factor = rate("绽伤血量倍率") * multi.get("绽伤血量倍率", 1)
    normal_factor = scar_factor * lr("NormalVitalRate")
    tear_factor = scar_factor * lr("TearVitalRate")
    legendary_tear_factor = scar_factor * lr("TearVitalRateLegendaryScar")
    hp = game_round(monster["base_hp"] * health_factor)
    adjust = int(target["health_adjust"] or 0)
    hp_min = game_round(hp * random_rates[f"_ValueM{adjust}"]) if adjust else hp
    hp_max = game_round(hp * random_rates[f"_ValueP{adjust}"]) if adjust else hp
    parts = []
    legendary_parts = []
    at_parts = {p["raw_part"] for p in monster["parts"] if p["raw_part"].endswith(" AT")}
    for original in monster["parts"]:
        p = dict(original)
        if p["raw_part"] == "HIDE":
            continue
        if p["raw_part"].endswith(" AT") and legendary != 2:
            continue
        if legendary == 2 and p["kind"] == "历战" and p["raw_part"] + " AT" in at_parts:
            continue
        if p["kind"] == "历战":
            if not legendary:
                continue
            p["values"] = [
                scale(p["values"][3], legendary_tear_factor),
                scale(p["values"][4], wound_factor),
                p["values"][5],
            ]
            legendary_parts.append(p)
        else:
            if p["state"] != "普通" and not legendary:
                continue
            factors = [parts_factor, parts_factor, normal_factor, tear_factor, wound_factor, 1]
            p["values"] = [scale(v, f) for v, f in zip(p["values"], factors, strict=True)]
            parts.append(p)
    at_overrides = {h["part"] for h in monster["hitzones"] if h["group"] == "历战王"}
    hitzones = []
    for row in monster["hitzones"]:
        if row["group"] == "历战王" and legendary != 2:
            continue
        if legendary == 2 and row["group"] == "普通" and row["part"] in at_overrides:
            continue
        if "伤痕" in row["state"] and not legendary:
            continue
        hitzones.append(row)
    conditions = []
    for original in monster["conditions"]:
        row = dict(original)
        values = []
        for name, value in zip(STATUS_NAMES, row["values"], strict=True):
            if row["label"] in {"耐性增量", "耐性上限"}:
                factor = difficulty.get(f"{name}·耐性增量与上限倍率", 1)
                multi_key = (
                    "通用异常耐性倍率" if name in {"毒", "睡眠", "爆破"} else f"{name}耐性倍率"
                )
                value = scale(value, factor * multi.get(multi_key, 1))
            elif row["label"] == "初始耐性":
                value = scale(value, difficulty.get(f"{name}·初始耐性倍率", 1))
            values.append(value)
        row["values"] = values
        conditions.append(row)
    blowouts = [
        dict(p, values=[scale(v, parts_factor) for v in p["values"]]) for p in monster["blowouts"]
    ]
    return {
        "monster": monster,
        "target": target,
        "players": players,
        "hp": hp,
        "hp_min": hp_min,
        "hp_max": hp_max,
        "health_factor": health_factor,
        "parts": parts,
        "legendary_parts": legendary_parts,
        "hitzones": hitzones,
        "conditions": conditions,
        "blowouts": blowouts,
    }
