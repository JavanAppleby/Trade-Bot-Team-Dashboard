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


def show(value: object) -> str:
    """'09:31:05 ET (23:31 Bris)', with the ET date when it is not today in ET."""
    dt = value if isinstance(value, datetime) else parse(value)
    if dt is None:
        return "" if value in (None, "") else str(value)
    et = dt.astimezone(ET)
    bris = dt.astimezone(BRISBANE)
    now_et = datetime.now(timezone.utc).astimezone(ET)
    day = "" if et.date() == now_et.date() else et.strftime("%a %d %b ")
    return f"{day}{et:%H:%M:%S} ET ({bris:%H:%M} Bris)"


def show_short(value: object) -> str:
    """'09:31 ET' for table cells."""
    dt = value if isinstance(value, datetime) else parse(value)
    if dt is None:
        return "" if value in (None, "") else str(value)
    et = dt.astimezone(ET)
    now_et = datetime.now(timezone.utc).astimezone(ET)
    day = "" if et.date() == now_et.date() else et.strftime("%d %b ")
    return f"{day}{et:%H:%M:%S}"


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
