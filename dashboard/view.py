"""The shape every panel and detail view takes, so the page renders them all the same way.

Panel: {bot, title, badge, reason, updated, age, headline, alert, facts, tables, texts}
- badge: LIVE | STALE | STOPPED | STARTING | NO DATA | ERROR
- facts: [[label, value, level]] where level is "" | "good" | "warn" | "bad"
- tables: [{title, columns, rows, keys, note}]; keys[i] is the detail key of rows[i] or None
- texts: [{title, text}] shown as plain preformatted text
"""

from __future__ import annotations

import re
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
        return "Yes" if value else "No"
    if isinstance(value, (list, tuple)):
        return ", ".join(cell(v) for v in value)
    if isinstance(value, dict):
        return ", ".join(f"{nice(k)}: {cell(v)}" for k, v in value.items())
    return str(value)


# Plain-English wording for the bots' codes ("read_failed" -> "Read failed", PRE -> "Pre-market").
WORDS = {"pct": "%", "usd": "$", "r": "R", "atr": "ATR", "hwm": "high", "coid": "order ID", "id": "ID",
         "llm": "LLM", "vix": "VIX", "cik": "CIK", "url": "link", "sha256": "SHA-256", "et": "ET", "pnl": "P&L",
         "edgar": "EDGAR", "bls": "BLS", "fomc": "FOMC", "cboe": "Cboe", "rss": "RSS", "prnewswire": "PR Newswire",
         "globenewswire": "GlobeNewswire", "nasdaq": "Nasdaq", "jason": "Jason", "discord": "Discord",
         "tg": "Guardian", "sdk": "SDK", "http": "HTTP", "json": "JSON", "rvol": "rel volume", "vwap": "VWAP",
         "mfe": "MFE", "mae": "MAE", "eod": "end of day", "cpi": "CPI", "ppi": "PPI", "nfp": "NFP", "gdp": "GDP",
         "nowflation": "Nowflation", "pr": "PR", "ok": "OK", "pmh": "pre-market high", "rr": "reward:risk",
         "10d": "10-day", "ttl": "TTL", "api": "API", "ath": "after hours", "rth": "regular hours"}
SESSIONS = {"PRE": "Pre-market", "CORE": "Regular hours", "POST": "After hours", "NIGHT": "Overnight",
            "CLOSED": "Closed"}
ANSWERS = {"true": "Yes", "false": "No", "unknown": "Unknown"}
_CODE = re.compile(r"[A-Za-z0-9]+(?:_[A-Za-z0-9]+)*")


def nice(value: object) -> object:
    """A code as plain words: 'trade_events_status' -> 'Trade events status', 'WARNING' -> 'Warning',
    'PRE' -> 'Pre-market'. Anything that is not a single code (a sentence, a number) is unchanged."""
    if isinstance(value, (list, tuple)):
        return [nice(v) for v in value]
    if not isinstance(value, str) or not value:
        return value
    if value in SESSIONS:
        return SESSIONS[value]
    if value in ANSWERS:
        return ANSWERS[value]
    if not _CODE.fullmatch(value) or value.isdigit():
        return value
    words = [WORDS.get(w.lower(), w.lower()) for w in value.split("_")]
    text = " ".join(words).replace(" %", " %").replace("$ ", "$")
    return text[0].upper() + text[1:]


def kv_table(title: str, data: object, note: str = "") -> dict:
    """A JSON object as a two-column table: plain-English keys, codes in values as words."""
    if not isinstance(data, dict):
        return table(title, [], [], note=note or ("Nothing recorded." if data in (None, "", {}) else cell(data)))
    rows = []
    for k, val in data.items():
        if isinstance(val, str) and "_" in val:
            val = nice(val)
        elif isinstance(val, list):
            val = [nice(x) if isinstance(x, str) and "_" in x else x for x in val]
        elif isinstance(val, float):
            val = f"{val:.4g}"
        rows.append([nice(k), val])
    return table(title, ["", ""], rows, note=note)


def sentence(value: object) -> object:
    """A message with a capital first letter and any lower-case code in it as words
    ('open_orders unreadable' -> 'Open orders unreadable')."""
    if not isinstance(value, str) or not value:
        return value
    value = _SNAKE.sub(lambda m: m.group(0).replace("_", " "), value)
    return value[0].upper() + value[1:] if value[0].islower() else value


_SNAKE = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b")


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
