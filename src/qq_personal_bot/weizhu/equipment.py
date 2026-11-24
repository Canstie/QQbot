"""Read the equipment recorded for one squad member."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Equipment:
    id: str
    name: str


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
