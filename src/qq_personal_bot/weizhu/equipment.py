"""Read the equipment recorded for one squad member."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Equipment:
    id: str
    name: str


@dataclass(frozen=True)
class EquipmentDisplay:
    name: str
    icon_urls: tuple[str, ...] = ()
    elite: bool = False


def equipment_display(entry: Equipment, catalog: dict) -> EquipmentDisplay:
    item_id = entry.id
    elite_variant = item_id.startswith("chess_item_") and item_id.endswith("_e_b")
    # 天马之耀 in 规程·序列Ⅰ is gold without a separate upgrade ID.
    elite = elite_variant or item_id == "chess_item_6_12_e_a"
    item = catalog.get(item_id) or {}
    base = (catalog.get(item_id[:-1] + "a") or {}) if elite_variant else {}
    name = item.get("name") or entry.name or base.get("name") or item_id
    icon = item.get("iconUrl") or base.get("iconUrl")
    if not icon:
        return EquipmentDisplay(name, elite=elite)
    if (
        elite
        and icon.startswith("https://web.hycdn.cn/arknights/game/assets/char_skill/sktok_")
        and icon.endswith("_1.png")
    ):
        # The catalog may contain only the gray sprite for an elite/ordinary pair.
        return EquipmentDisplay(name, (icon.removesuffix("_1.png") + "_2.png", icon), elite=True)
    return EquipmentDisplay(name, (icon,), elite)


def read_equipment(member: dict) -> list[Equipment] | None:
    """Return None for missing or unreadable data, and [] for an empty loadout."""
    has_empty = False
    info = member.get("chessInfo")
    for owner in (member, info if isinstance(info, dict) else {}):
        for key in ("equips", "equipIds", "equipList", "equipmentIds", "equipments"):
            slots = owner.get(key)
            if not isinstance(slots, list):
                continue
            has_empty |= not slots
            equipment = []
            for slot in slots:
                fields = slot if isinstance(slot, dict) else {"id": slot}
                item_id = next(
                    (
                        str(fields[key]).strip()
                        for key in ("equipId", "itemId", "id")
                        if isinstance(fields.get(key), (str, int))
                        and not isinstance(fields[key], bool)
                        and str(fields[key]).strip()
                    ),
                    "",
                )
                name = next(
                    (
                        fields[key].strip()
                        for key in ("name", "itemName", "equipName")
                        if isinstance(fields.get(key), str) and fields[key].strip()
                    ),
                    "",
                )
                if item_id or name:
                    equipment.append(Equipment(item_id, name))
            if equipment:
                return equipment
    return [] if has_empty else None
