from __future__ import annotations

from dataclasses import dataclass
from datetime import date


_BASE_DATE = date(1900, 1, 31)
_MAX_SUPPORTED_DATE = date(2100, 12, 31)
_MONTH_LABELS = ("正", "二", "三", "四", "五", "六", "七", "八", "九", "十", "冬", "腊")
_DAY_TENS = ("初", "十", "廿", "三")
_DAY_DIGITS = ("一", "二", "三", "四", "五", "六", "七", "八", "九")
_LUNAR_INFO = (
    0x04BD8,
    0x04AE0,
    0x0A570,
    0x054D5,
    0x0D260,
    0x0D950,
    0x16554,
    0x056A0,
    0x09AD0,
    0x055D2,
    0x04AE0,
    0x0A5B6,
    0x0A4D0,
    0x0D250,
    0x1D255,
    0x0B540,
    0x0D6A0,
    0x0ADA2,
    0x095B0,
    0x14977,
    0x04970,
    0x0A4B0,
    0x0B4B5,
    0x06A50,
    0x06D40,
    0x1AB54,
    0x02B60,
    0x09570,
    0x052F2,
    0x04970,
    0x06566,
    0x0D4A0,
    0x0EA50,
    0x06E95,
    0x05AD0,
    0x02B60,
    0x186E3,
    0x092E0,
    0x1C8D7,
    0x0C950,
    0x0D4A0,
    0x1D8A6,
    0x0B550,
    0x056A0,
    0x1A5B4,
    0x025D0,
    0x092D0,
    0x0D2B2,
    0x0A950,
    0x0B557,
    0x06CA0,
    0x0B550,
    0x15355,
    0x04DA0,
    0x0A5D0,
    0x14573,
    0x052D0,
    0x0A9A8,
    0x0E950,
    0x06AA0,
    0x0AEA6,
    0x0AB50,
    0x04B60,
    0x0AAE4,
    0x0A570,
    0x05260,
    0x0F263,
    0x0D950,
    0x05B57,
    0x056A0,
    0x096D0,
    0x04DD5,
    0x04AD0,
    0x0A4D0,
    0x0D4D4,
    0x0D250,
    0x0D558,
    0x0B540,
    0x0B5A0,
    0x195A6,
    0x095B0,
    0x049B0,
    0x0A974,
    0x0A4B0,
    0x0B27A,
    0x06A50,
    0x06D40,
    0x0AF46,
    0x0AB60,
    0x09570,
    0x04AF5,
    0x04970,
    0x064B0,
    0x074A3,
    0x0EA50,
    0x06B58,
    0x05AC0,
    0x0AB60,
    0x096D5,
    0x092E0,
    0x0C960,
    0x0D954,
    0x0D4A0,
    0x0DA50,
    0x07552,
    0x056A0,
    0x0ABB7,
    0x025D0,
    0x092D0,
    0x0CAB5,
    0x0A950,
    0x0B4A0,
    0x0BAA4,
    0x0AD50,
    0x055D9,
    0x04BA0,
    0x0A5B0,
    0x15176,
    0x052B0,
    0x0A930,
    0x07954,
    0x06AA0,
    0x0AD50,
    0x05B52,
    0x04B60,
    0x0A6E6,
    0x0A4E0,
    0x0D260,
    0x0EA65,
    0x0D530,
    0x05AA0,
    0x076A3,
    0x096D0,
    0x04BD7,
    0x04AD0,
    0x0A4D0,
    0x1D0B6,
    0x0D250,
    0x0D520,
    0x0DD45,
    0x0B5A0,
    0x056D0,
    0x055B2,
    0x049B0,
    0x0A577,
    0x0A4B0,
    0x0AA50,
    0x1B255,
    0x06D20,
    0x0ADA0,
)


@dataclass(frozen=True)
class LunarDateInfo:
    year: int
    month: int
    day: int
    is_leap_month: bool

    @property
    def month_label(self) -> str:
        prefix = "闰" if self.is_leap_month else ""
        return f"{prefix}{_MONTH_LABELS[self.month - 1]}月"

    @property
    def day_label(self) -> str:
        return _format_lunar_day(self.day)

    @property
    def display(self) -> str:
        return f"{self.month_label}{self.day_label}"

    @property
    def key(self) -> str:
        leap_flag = "L" if self.is_leap_month else "N"
        return f"{self.year:04d}-{self.month:02d}-{self.day:02d}-{leap_flag}"


def solar_to_lunar(target: date) -> LunarDateInfo:
    if target < _BASE_DATE or target > _MAX_SUPPORTED_DATE:
        raise ValueError("date is outside supported lunar conversion range")

    offset = (target - _BASE_DATE).days
    year = 1900
    while year < 2101:
        year_days = _lunar_year_days(year)
        if offset < year_days:
            break
        offset -= year_days
        year += 1

    leap_month = _leap_month(year)
    month = 1
    is_leap_month = False
    while month <= 12:
        if leap_month == month and not is_leap_month:
            month_days = _leap_days(year)
            is_leap_month = True
        else:
            month_days = _month_days(year, month)
        if offset < month_days:
            break
        offset -= month_days
        if is_leap_month and leap_month == month:
            is_leap_month = False
        else:
            month += 1

    day = offset + 1
    return LunarDateInfo(year=year, month=month, day=day, is_leap_month=is_leap_month)


def _leap_month(year: int) -> int:
    return _LUNAR_INFO[year - 1900] & 0xF


def _leap_days(year: int) -> int:
    if _leap_month(year):
        return 30 if (_LUNAR_INFO[year - 1900] & 0x10000) else 29
    return 0


def _month_days(year: int, month: int) -> int:
    if month < 1 or month > 12:
        raise ValueError("month must be between 1 and 12")
    return 30 if (_LUNAR_INFO[year - 1900] & (0x10000 >> month)) else 29


def _lunar_year_days(year: int) -> int:
    days = 348
    info = _LUNAR_INFO[year - 1900]
    for mask in (0x8000, 0x4000, 0x2000, 0x1000, 0x800, 0x400, 0x200, 0x100, 0x80, 0x40, 0x20, 0x10):
        if info & mask:
            days += 1
    return days + _leap_days(year)


def _format_lunar_day(day: int) -> str:
    if day <= 0 or day > 30:
        raise ValueError("day must be between 1 and 30")
    if day == 10:
        return "初十"
    if day == 20:
        return "二十"
    if day == 30:
        return "三十"
    tens = _DAY_TENS[(day - 1) // 10]
    digit = _DAY_DIGITS[(day - 1) % 10]
    return f"{tens}{digit}"
