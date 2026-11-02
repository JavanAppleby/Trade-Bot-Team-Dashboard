"""The shape every panel and detail view takes, so the page renders them all the same way.

Panel: {bot, title, badge, reason, updated, age, headline, alert, facts, tables, texts}
- badge: LIVE | STALE | STOPPED | STARTING | NO DATA | ERROR
- facts: [[label, value, level]] where level is "" | "good" | "warn" | "bad"
- tables: [{title, columns, rows, keys, note}]; keys[i] is the detail key of rows[i] or None
- texts: [{title, text}] shown as plain preformatted text
"""

from __future__ import annotations

import traceback
from collections.abc import Callable
from decimal import Decimal, InvalidOperation

from dashboard import times
from dashboard.safe import ReadError

LIVE, STALE, STOPPED, STARTING, NO_DATA, ERROR = "LIVE", "STALE", "STOPPED", "STARTING", "NO DATA", "ERROR"


def panel(bot: str, title: str) -> dict:
    return {"bot": bot, "title": title, "badge": NO_DATA, "reason": "", "updated": "", "age": "",
            "headline": "", "alert": False, "facts": [], "tables": [], "texts": []}


def fact(p: dict, label: str, value: object, level: str = "") -> None:
    p["facts"].append([label, cell(value), level])


def table(title: str, columns: list[str], rows: list[list], keys: list | None = None, note: str = "") -> dict:
    return {"title": title, "columns": columns, "rows": [[cell(v) for v in r] for r in rows],
            "keys": keys if keys is not None else [None] * len(rows), "note": note}


def text(p: dict, title: str, body: str) -> None:
    p["texts"].append({"title": title, "text": body})


def cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (list, tuple)):
        return ", ".join(cell(v) for v in value)
    if isinstance(value, dict):
        return ", ".join(f"{k}: {cell(v)}" for k, v in value.items())
    return str(value)


def dec(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return d if d.is_finite() else None


def price(value: object) -> str:
    """A share price: 2 decimals from $1 up (cent ticks), up to 4 below $1."""
    d = dec(value)
    if d is None:
        return ""
    if abs(d) >= 1:
        return f"{d:.2f}"
    return f"{d:.4f}".rstrip("0").rstrip(".") or "0"


def usd(value: object) -> str:
    d = dec(value)
    return "" if d is None else f"{d:,.2f}"


def num(value: object, places: int = 2) -> str:
    d = dec(value)
    return "" if d is None else f"{d:.{places}f}"


def pct(value: object) -> str:
    """A fraction (0.283) as '28.3%'."""
    d = dec(value)
    return "" if d is None else f"{d * 100:.1f}%"


def freshness(p: dict, at, now, limit_s: float, what: str) -> None:
    """Set badge, age and updated from the time the bot last wrote its freshness source."""
    if at is None:
        p["badge"], p["reason"] = ERROR, f"{what} has no readable time"
        return
    age = (now - at).total_seconds()
    p["updated"] = times.show(at)
    p["age"] = times.age_text(age)
    if age > limit_s or age < -limit_s:
        p["badge"] = STALE
        p["reason"] = f"{what} last updated {times.age_text(age)} (stale after {limit_s:.0f} s)"
    else:
        p["badge"] = LIVE


def guarded(p: dict, title: str, fn: Callable[[], None]) -> None:
    """Run one section; a failure becomes a note in that section and the rest still shows."""
    try:
        fn()
    except FileNotFoundError:
        p["tables"].append(table(title, [], [], note="no data (file not found)"))
    except ReadError as exc:
        p["tables"].append(table(title, [], [], note=f"error: {exc}"))
    except Exception as exc:  # noqa: BLE001 - a panel must never crash the page
        p["tables"].append(table(title, [], [], note=f"error: {type(exc).__name__}: {exc}"))


def safe_build(bot: str, title: str, build: Callable[[], dict]) -> dict:
    try:
        return build()
    except Exception as exc:  # noqa: BLE001
        p = panel(bot, title)
        p["badge"], p["reason"] = ERROR, f"{type(exc).__name__}: {exc}"
        p["texts"].append({"title": "Details", "text": traceback.format_exc(limit=3)})
        return p


def detail(title: str) -> dict:
    return {"title": title, "facts": [], "tables": [], "texts": []}
