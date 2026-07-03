from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from lunar_python import Solar


@dataclass(frozen=True)
class LunarDateInfo:
    year: int
    month: int
    day: int
    is_leap_month: bool
    month_label: str
    day_label: str
    display: str
    key: str
    day_yi: tuple[str, ...]
    day_ji: tuple[str, ...]


def solar_to_lunar(target: date) -> LunarDateInfo:
    lunar = Solar.fromYmd(target.year, target.month, target.day).getLunar()
    month = int(lunar.getMonth())
    is_leap_month = month < 0
    normalized_month = abs(month)
    day = int(lunar.getDay())
    year = int(lunar.getYear())
    month_label = f"{'闰' if is_leap_month else ''}{lunar.getMonthInChinese()}月"
    day_label = lunar.getDayInChinese()
    display = f"{month_label}{day_label}"
    leap_flag = "L" if is_leap_month else "N"
    day_yi = tuple(str(item) for item in lunar.getDayYi())
    day_ji = tuple(str(item) for item in lunar.getDayJi())
    return LunarDateInfo(
        year=year,
        month=normalized_month,
        day=day,
        is_leap_month=is_leap_month,
        month_label=month_label,
        day_label=day_label,
        display=display,
        key=f"{year:04d}-{normalized_month:02d}-{day:02d}-{leap_flag}",
        day_yi=day_yi,
        day_ji=day_ji,
    )
