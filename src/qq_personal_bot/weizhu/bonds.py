"""Infer active bond tiers from Skland's rules and end-of-match roster."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass

from .equipment import read_equipment

_RICH_TAG = re.compile(r"<@[^>]+>|</>")
_THRESHOLD = re.compile(
    r"<(?:(?:首次)?达到(?P<layers>\d+)层|"
    r"在场(?P<people>\d+)名(?P<distinct>不同)?(?:【[^】]+】干员|(?P<elite>精锐状态的干员)))>"
)
_ROMAN = ("Ⅰ", "Ⅱ", "Ⅲ", "Ⅳ", "Ⅴ", "Ⅵ", "Ⅶ", "Ⅷ", "Ⅸ", "Ⅹ", "Ⅺ", "Ⅻ")

# Base equipment IDs paired with 变形同构体 in 规程·序列Ⅰ. Both grades use the same bond.
_TRANSFER_BONDS = {
    "chess_item_1_01_e": "victoriaShip",
    "chess_item_1_02_e": "steadShip",
    "chess_item_2_01_e": "indomShip",
    "chess_item_2_03_e": "victoriaShip",
    "chess_item_2_04_e": "sargonShip",
    "chess_item_2_09_e": "docShip",
    "chess_item_2_10_e": "skillfulShip",
    "chess_item_3_01_e": "siracusaShip",
    "chess_item_3_02_e": "preciShip",
    "chess_item_3_03_e": "yanShip",
    "chess_item_3_04_e": "swiftShip",
    "chess_item_3_06_e": "egirShip",
    "chess_item_3_07_e": "arcaneShip",
    "chess_item_3_08_e": "victoriaShip",
    "chess_item_3_09_e": "victoriaShip",
    "chess_item_3_10_e": "raidShip",
    "chess_item_3_12_e": "ursusShip",
    "chess_item_3_14_e": "iberiaShip",
    "chess_item_4_04_e": "kazimierzShip",
    "chess_item_4_05_e": "victoriaShip",
    "chess_item_4_09_e": "lethaniaShip",
    "chess_item_4_10_e": "kjeragShip",
    "chess_item_5_07_e": "victoriaShip",
    "chess_item_6_11_e": "victoriaShip",
}


@dataclass(frozen=True)
class ActiveBond:
    id: str
    label: str
    roster_based: bool = False


def _number(value: object) -> int:
    try:
        return int(str(value))
    except ValueError:
        return 0


def _roster_counts(record, catalog, chars, use_transfers):
    native: dict[str, set[str]] = defaultdict(set)
    for char_id, char in chars.items():
        native[char_id].update(bond["bondId"] for bond in char.get("bonds") or () if bond.get("bondId"))
    for bond_id, bond in catalog.items():
        for char in bond.get("chars") or ():
            if char.get("charId"):
                native[char["charId"]].add(bond_id)

    total: Counter[str] = Counter()
    distinct: dict[str, set[str]] = defaultdict(set)
    elites = 0
    for member in record.get("chessSquad") or ():
        info = member.get("chessInfo") or {}
        char_id = info.get("charId") or member.get("charId")
        elites += _number(member.get("mergeLevel")) > 0
        recorded = {bid for bid in info.get("bondIds") or () if isinstance(bid, str)}
        affiliations = recorded or native[char_id].copy()
        if use_transfers and not recorded:
            items = {item.id.rpartition("_")[0] for item in read_equipment(member) or ()}
            if "chess_item_6_03_e" in items:
                extra = {_TRANSFER_BONDS[item] for item in items if item in _TRANSFER_BONDS}
                # A multi-slot loadout can contain several possible partners. Avoid guessing.
                if len(extra) == 1:
                    affiliations.update(extra)
        total.update(affiliations)
        if char_id:
            for bond_id in affiliations:
                distinct[bond_id].add(char_id)
    return {key: len(ids) for key, ids in distinct.items()}, total, elites


def active_bonds(record: dict, catalog: dict, chars: dict, version: dict) -> list[ActiveBond]:
    activated = {
        row["bondId"]: _number(row.get("layer"))
        for row in record.get("bondInfo") or ()
        if row.get("activate") and row.get("bondId")
    }
    if not activated:
        return []
    recorded_version = record.get("versionId")
    same_version = not recorded_version or recorded_version == version.get("versionId")
    distinct, total, elites = _roster_counts(
        record, catalog, chars, same_version and version.get("versionId") == "V3_1"
    )
    result = []
    for bond_id, layer in sorted(activated.items(), key=lambda item: -item[1]):
        bond = catalog.get(bond_id) or {}
        name = bond.get("name") or bond_id
        if not bond or not same_version:
            result.append(ActiveBond(bond_id, f"{name} · {layer}层"))
            continue
        base_count = _number(bond.get("activeCount"))
        harmony = int("maniShip" in activated and bond.get("bondType") == 1)
        tier = 1
        participants = None
        roster_based = False
        effect = _RICH_TAG.sub("", bond.get("effect") or "")
        for match in _THRESHOLD.finditer(effect):
            if layer_threshold := match["layers"]:
                tier += layer >= int(layer_threshold)
                continue
            if int(match["people"]) <= base_count:
                continue  # The match already reports whether the base tier is active.
            roster_based = True
            if match["elite"]:
                count, unit = elites, "名精锐"
            else:
                count = (distinct.get(bond_id, 0) if match["distinct"] else total[bond_id]) + harmony
                unit = "人"
            tier += count >= int(match["people"])
            if count >= base_count:
                participants = f"{count}{unit}"
        parts = [f"{name} {_ROMAN[tier - 1]}"]
        if participants:
            parts.append(participants)
        if layer or not participants:
            parts.append(f"{layer}层")
        result.append(ActiveBond(bond_id, " · ".join(parts), roster_based))
    return result
