"""Battle analytics adapted from astrbot_plugin_weizhu v1.2.1 (MIT)."""

from collections import defaultdict
from collections.abc import Iterable

MODES = {
    "全部": (None, None),
    "多人": ("modeType", "MULTI"),
    "单人": ("modeType", "SINGLE"),
    "极速": ("modeDifficulty", "FAST"),
    "终极": ("modeDifficulty", "ABYSS"),
}
RESULTS = ("全部", "完成", "中断")
FILTER_HELP = f"模式可选 {'、'.join(MODES)}，结果可选 {'、'.join(RESULTS)}。"


def select_history(records: list[dict], mode: str, result: str = "全部") -> list[tuple[int, dict]]:
    field, value = MODES[mode]
    return [
        (index, record)
        for index, record in enumerate(records, 1)
        if (field is None or record.get(field) == value)
        and (result == "全部" or bool(record.get("completeState")) == (result == "完成"))
    ]


def active_layers(record: dict) -> dict[str, int | None]:
    layers = {}
    for bond in record.get("bondInfo") or []:
        if not isinstance(bond, dict) or not bond.get("activate"):
            continue
        bond_id = bond.get("bondId")
        if not isinstance(bond_id, str) or not bond_id:
            continue
        layers.setdefault(bond_id, None)
        try:
            layer = int(str(bond.get("layer")))
        except ValueError:
            continue
        if layer > 0:
            layers[bond_id] = max(layer, layers[bond_id] or 0)
    return layers


def rankings(records: Iterable[dict]) -> list[tuple[str, int, int | None, float | None]]:
    layers = defaultdict(list)
    for record in records:
        for bond_id, layer in active_layers(record).items():
            layers[bond_id].append(layer)
    rows = []
    for bond_id, values in layers.items():
        measured = [value for value in values if value is not None]
        peak = max(measured, default=None)
        average = sum(measured) / len(measured) if measured else None
        rows.append((bond_id, len(values), peak, average))
    return sorted(rows, key=lambda row: (-row[1], -(row[2] or 0), row[0]))
