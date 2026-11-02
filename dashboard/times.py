"""Times: stored UTC in, ET and Brisbane out."""

from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
BRISBANE = ZoneInfo("Australia/Brisbane")


def parse(text: object) -> datetime | None:
    """An ISO 8601 time with "Z" or an offset, as aware UTC; None for anything else."""
    if not isinstance(text, str) or not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None or dt.utcoffset() is None:
        return None
    return dt.astimezone(timezone.utc)


def clock(dt: datetime, seconds: bool = False) -> str:
    """'8:38 am' (or '8:38:24 am')."""
    text = f"{dt.hour % 12 or 12}:{dt.minute:02d}" + (f":{dt.second:02d}" if seconds else "")
    return text + (" am" if dt.hour < 12 else " pm")


def _day(et: datetime) -> str:
    """'' today (ET), else 'Sat 31 Oct, '."""
    now_et = datetime.now(timezone.utc).astimezone(ET)
    return "" if et.date() == now_et.date() else f"{et:%a} {et.day} {et:%b}, "


def show(value: object) -> str:
    """'Sat 31 Oct, 8:38:24 am ET (10:38 pm Brisbane)'; the date is left out for today in ET."""
    dt = value if isinstance(value, datetime) else parse(value)
    if dt is None:
        return "" if value in (None, "") else str(value)
    et = dt.astimezone(ET)
    return f"{_day(et)}{clock(et, seconds=True)} ET ({clock(dt.astimezone(BRISBANE))} Brisbane)"


def show_short(value: object, seconds: bool = False) -> str:
    """'8:38 am' for table cells (ET), with '31 Oct, ' before it when it is not today."""
    dt = value if isinstance(value, datetime) else parse(value)
    if dt is None:
        return "" if value in (None, "") else str(value)
    et = dt.astimezone(ET)
    day = _day(et)
    return (day.split(" ", 1)[1] if day else "") + clock(et, seconds)


def show_date(value: object) -> str:
    """'2026-11-02' -> 'Mon 2 Nov'."""
    try:
        d = date.fromisoformat(str(value))
    except ValueError:
        return "" if value in (None, "") else str(value)
    return f"{d:%a} {d.day} {d:%b}"


def et_today(now: datetime) -> date:
    return now.astimezone(ET).date()


def age_text(seconds: float | None) -> str:
    if seconds is None:
        return "unknown"
    s = abs(seconds)
    if s < 90:
        text = f"{s:.0f} s"
    elif s < 5400:
        text = f"{s / 60:.0f} min"
    elif s < 172800:
        text = f"{s / 3600:.1f} h"
    else:
        text = f"{s / 86400:.0f} days"
    return text + (" ago" if seconds >= 0 else " in the future")
