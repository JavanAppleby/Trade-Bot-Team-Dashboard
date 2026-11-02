"""Guardian panel: heartbeat, managed positions with stops and planned moves, orders, warnings.

Reads `state\\guardian.heartbeat`, `state\\guardian.db` (never its `meta` table: it holds the
account id) and named keys of `config.toml` / `config.example.toml` for the next-ratchet estimate.
"""

from __future__ import annotations

import json
import math
import tomllib
from datetime import datetime
from decimal import Decimal

from dashboard import safe, times
from dashboard import view as v
from dashboard.config import Config

RATCHET_KEYS = ("breakeven_at_r", "trail_atr_mult", "trail_pct", "partial_at_r", "partial_fraction",
                "flatten_enabled", "flatten_regular_at", "flatten_extended_at", "max_auto_price")
OPEN_ORDER = ("INTENT", "WORKING", "UNKNOWN")


def _paths(cfg: Config) -> dict:
    g = cfg.guardian_dir
    return {"hb": g / "state" / "guardian.heartbeat", "db": g / "state" / "guardian.db",
            "stop": g / "STOP", "lock": g / "state" / "tg.lock",
            "config": g / "config.toml", "example": g / "config.example.toml"}


def settings(cfg: Config) -> dict | None:
    """The named ratchet keys only (config.toml over config.example.toml); None if unreadable."""
    paths = _paths(cfg)
    out: dict = {}
    try:
        for path in (paths["example"], paths["config"]):
            body = safe.read_text(path, 256 * 1024)
            if body is None:
                continue
            data = tomllib.loads(body)
            out.update({k: data[k] for k in RATCHET_KEYS if k in data})
    except (safe.ReadError, tomllib.TOMLDecodeError):
        return None
    return out if "breakeven_at_r" in out else None


def next_ratchet(plan: dict, s: dict | None) -> str:
    """Dashboard estimate of Guardian's next stop moves (plan.advance rules, before tick rounding)."""
    if s is None:
        return "Unknown (Guardian's settings could not be read)"
    entry, r, hwm, stop = (v.dec(plan.get(k)) for k in ("entry", "r", "hwm", "stop"))
    if entry is None or r is None or hwm is None or r <= 0:
        return ""
    armed = bool(plan.get("breakeven_armed"))
    parts = []
    if not armed:
        be = v.dec(s.get("breakeven_at_r")) or Decimal(1)
        parts.append(f"Breakeven at {v.price(entry + be * r)} → stop {v.price(entry)}")
    k = math.floor((hwm - entry) / r)
    kn = max(k + 1, 2)
    parts.append(f"R-step at {v.price(entry + kn * r)} → stop {v.price(entry + (kn - 1) * r)}")
    if armed:
        atr = v.dec(plan.get("atr"))
        if atr is not None and s.get("trail_atr_mult") is not None:
            trail = hwm - v.dec(s["trail_atr_mult"]) * atr
        else:
            trail = hwm * (1 - (v.dec(s.get("trail_pct")) or Decimal("0.06")))
        where = "above" if stop is None or trail > stop else "below"
        parts.append(f"Trail {v.price(trail)} ({where} the stop, rises with each new high)")
    return "; ".join(parts)


def _load_plan(text: object) -> dict | None:
    if not isinstance(text, str) or not text.strip():
        return None
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _reasons(text: object) -> str:
    try:
        value = json.loads(text) if isinstance(text, str) else text
    except json.JSONDecodeError:
        return str(text)
    return v.cell(v.nice(value)) if isinstance(value, list) else v.cell(value)


def _exit_text(plan: dict) -> str:
    ex = plan.get("exit")
    if not isinstance(ex, dict):
        return ""
    return f"{v.nice(ex.get('reason', '?'))} {ex.get('target_qty', '')} shares, since {times.show_short(ex.get('started_at'))}"


def build(cfg: Config, now: datetime) -> dict:
    p = v.panel("guardian", "Guardian")
    paths = _paths(cfg)
    hb: dict = {}
    try:
        raw = safe.read_json(paths["hb"], safe.HEARTBEAT_CAP)
        if raw is None:
            p["badge"], p["reason"] = v.NO_DATA, "Heartbeat file not found"
        elif not isinstance(raw, dict):
            p["badge"], p["reason"] = v.ERROR, "The heartbeat is not a JSON object"
        elif "at" not in raw and raw.get("state") in ("starting", "stopped"):
            p["badge"] = v.STARTING if raw["state"] == "starting" else v.STOPPED
            p["reason"] = f"Guardian says it is {raw['state']}"
            written = safe.mtime(paths["hb"])
            if written is not None:
                p["updated"] = times.show(datetime.fromtimestamp(written, now.tzinfo))
        else:
            hb = raw
            v.freshness(p, times.parse(raw.get("at")), now, cfg.guardian_stale_s, "Heartbeat")
    except safe.ReadError as exc:
        p["badge"], p["reason"] = v.ERROR, str(exc)

    unprotected = hb.get("unprotected") if isinstance(hb.get("unprotected"), int) else None
    stop_file = safe.exists(paths["stop"])
    if hb:
        v.fact(p, "Mode", "Live (real orders)" if hb.get("mode") == "live" else v.nice(hb.get("mode")))
        v.fact(p, "Session", v.nice(hb.get("session")))
        v.fact(p, "Trading day", times.show_date(hb.get("trading_day")))
        v.fact(p, "Managed", hb.get("managed"))
        v.fact(p, "Unprotected", f"{unprotected} {v.cell(hb.get('unprotected_symbols') or '')}".strip(),
               "bad" if unprotected else "good")
        v.fact(p, "Hands off", hb.get("hands_off") or "None")
        v.fact(p, "Owner controlled", hb.get("owner_controlled") or "None")
        v.fact(p, "Lists complete", hb.get("lists_complete"), "" if hb.get("lists_complete") else "warn")
        v.fact(p, "Version", hb.get("version"))
    v.fact(p, "STOP file", "Present: Guardian is stopping" if stop_file else "None", "bad" if stop_file else "")
    v.fact(p, "Running", safe.exists(paths["lock"]))
    p["headline"] = f"{hb.get('managed', '?')} managed · {unprotected if unprotected is not None else '?'} unprotected"
    p["alert"] = bool(unprotected) or p["badge"] in (v.STALE, v.ERROR) or stop_file

    s = settings(cfg)
    day = hb.get("trading_day") if isinstance(hb.get("trading_day"), str) else None
    v.guarded(p, "Positions", lambda: _positions(p, paths, day, s))
    v.guarded(p, "Working orders", lambda: _orders(p, paths))
    v.guarded(p, "Trades", lambda: _closed(p, paths, day))
    v.guarded(p, "Warnings and errors", lambda: _warnings(p, paths))
    if s is None:
        v.fact(p, "Next-ratchet estimate", "Unavailable: Guardian's settings could not be read", "warn")
    return p


def _latest_day(conn, table: str) -> str | None:
    row = conn.execute(f"SELECT MAX(trading_day) FROM {table}").fetchone()
    return row[0] if row else None


def _positions(p: dict, paths: dict, day: str | None, s: dict | None) -> None:
    with safe.open_db(paths["db"]) as conn:
        safe.require(conn, "symbol_state", {"trading_day", "symbol", "classification", "plan"})
        day = day or _latest_day(conn, "symbol_state")
        rows = conn.execute("SELECT symbol, classification, plan FROM symbol_state "
                            "WHERE trading_day = ? AND plan IS NOT NULL AND plan <> '' ORDER BY symbol",
                            (day,)).fetchall()
        carried = []
        if safe.table_columns(conn, "carried"):
            carried = conn.execute("SELECT symbol, qty, entry, first_buy_at, from_day FROM carried "
                                   "WHERE active = 1 ORDER BY trading_day DESC, symbol").fetchall()
    out, keys = [], []
    for r in rows:
        plan = _load_plan(r["plan"]) or {}
        out.append([r["symbol"], v.nice(r["classification"]), v.price(plan.get("entry")), plan.get("qty"),
                    v.price(plan.get("r")), v.price(plan.get("initial_stop")), v.price(plan.get("stop")),
                    v.price(plan.get("hwm")), v.price(plan.get("atr")), plan.get("breakeven_armed"),
                    times.show_short(plan.get("flatten_at")), _exit_text(plan), next_ratchet(plan, s)])
        keys.append(f"{day}|{r['symbol']}")
    p["tables"].append(v.table(
        f"Positions Guardian is managing · {times.show_date(day) or 'no trading day yet'}",
        ["Symbol", "Class", "Entry", "Qty", "R", "Initial stop", "Stop now", "High", "ATR",
         "Breakeven armed", "Flatten at (ET)", "Exit in progress", "Next ratchet (estimate)"],
        out, keys, note="" if out else "Guardian isn't managing any positions right now."))
    if carried:
        p["tables"].append(v.table("Carried from an earlier day", ["Symbol", "Qty", "Entry", "First buy", "From"],
                                   [[c["symbol"], c["qty"], v.price(c["entry"]), times.show_short(c["first_buy_at"]),
                                     times.show_date(c["from_day"])] for c in carried]))


def _orders(p: dict, paths: dict) -> None:
    with safe.open_db(paths["db"]) as conn:
        safe.require(conn, "tg_orders", {"symbol", "order_type", "quantity", "status", "stop_price",
                                         "limit_price", "reason", "created_at", "shadow"})
        rows = conn.execute("SELECT symbol, order_type, quantity, stop_price, limit_price, status, reason, "
                            "shadow, created_at FROM tg_orders WHERE status IN (?, ?, ?) ORDER BY created_at DESC",
                            OPEN_ORDER).fetchall()
    p["tables"].append(v.table(
        "Guardian's working orders", ["Symbol", "Type", "Qty", "Stop", "Limit", "Status", "Reason", "Shadow", "Created"],
        [[r["symbol"], v.nice(r["order_type"]), r["quantity"], v.price(r["stop_price"]), v.price(r["limit_price"]),
          v.nice(r["status"]), v.nice(r["reason"]), bool(r["shadow"]), times.show_short(r["created_at"])] for r in rows],
        note="" if rows else "No working orders."))


def _trade_rows(conn, where: str, args: tuple) -> list:
    trades = conn.execute("SELECT position_key, symbol, trading_day, first_buy_at, session, entry, qty, "
                          f"initial_stop, r, closed_at, shadow FROM journal_trades {where}", args).fetchall()
    out = []
    for t in trades:
        exits = conn.execute("SELECT qty, price FROM journal_exits WHERE position_key = ?", (t["position_key"],)).fetchall()
        entry, r = v.dec(t["entry"]), v.dec(t["r"])
        pnl = sum(((v.dec(e["qty"]) or 0) * ((v.dec(e["price"]) or 0) - entry) for e in exits), Decimal(0)) \
            if entry is not None else None
        sold = sum((v.dec(e["qty"]) or 0 for e in exits), Decimal(0))
        rr = pnl / (r * sold) if pnl is not None and r and sold else None
        out.append((t, pnl, rr, sold))
    return out


def _closed(p: dict, paths: dict, day: str | None) -> None:
    with safe.open_db(paths["db"]) as conn:
        safe.require(conn, "journal_trades", {"position_key", "trading_day", "entry", "qty", "r"})
        day = day or _latest_day(conn, "journal_trades")
        rows = _trade_rows(conn, "WHERE trading_day = ? ORDER BY first_buy_at DESC", (day,))
    p["tables"].append(v.table(
        f"Today's trades · {times.show_date(day) or 'none'}",
        ["Symbol", "First buy", "Session", "Entry", "Qty", "Initial stop", "Sold", "P&L $", "R", "Closed", "Shadow"],
        [[t["symbol"], times.show_short(t["first_buy_at"]), v.nice(t["session"]), v.price(t["entry"]), t["qty"],
          v.price(t["initial_stop"]), v.num(sold, 0), v.usd(pnl), v.num(rr), times.show_short(t["closed_at"]),
          bool(t["shadow"])] for t, pnl, rr, sold in rows],
        [f"{t['trading_day']}|{t['symbol']}" for t, *_ in rows],
        note="" if rows else "No trades yet today."))


def _warnings(p: dict, paths: dict) -> None:
    with safe.open_db(paths["db"]) as conn:
        safe.require(conn, "events", {"at", "level", "code", "message", "symbol"})
        rows = conn.execute("SELECT at, level, code, symbol, message FROM events "
                            "WHERE level IN ('WARNING', 'ERROR', 'CRITICAL') ORDER BY id DESC LIMIT 20").fetchall()
    p["tables"].append(v.table("Latest warnings and errors", ["Time", "Level", "What", "Symbol", "Message"],
                               [[times.show_short(r["at"]), v.nice(r["level"]), v.nice(r["code"]), r["symbol"],
                                 v.sentence(r["message"])] for r in rows], note="" if rows else "None."))


def detail(cfg: Config, key: str) -> dict:
    """One symbol on one trading day: its plan, stop history, exits, orders and events."""
    day, _, symbol = key.partition("|")
    d = v.detail(f"Guardian · {symbol} · {times.show_date(day)}")
    paths = _paths(cfg)
    s = settings(cfg)
    with safe.open_db(paths["db"]) as conn:
        row = conn.execute("SELECT classification, reasons, hands_off, hands_off_reason, plan, updated_at "
                           "FROM symbol_state WHERE trading_day = ? AND symbol = ?", (day, symbol)).fetchone()
        plan = _load_plan(row["plan"]) if row else None
        keys = conn.execute("SELECT position_key FROM journal_trades WHERE trading_day = ? AND symbol = ?",
                            (day, symbol)).fetchall()
        pkeys = sorted({k[0] for k in keys} | ({plan["position_key"]} if plan and plan.get("position_key") else set()))
        marks = ",".join("?" * len(pkeys)) or "''"
        stops = conn.execute(f"SELECT at, old, new, reason FROM journal_stop_changes WHERE position_key IN ({marks}) "
                             "ORDER BY at", pkeys).fetchall()
        exits = conn.execute(f"SELECT at, qty, price, reason, trigger_price FROM journal_exits "
                             f"WHERE position_key IN ({marks}) ORDER BY at", pkeys).fetchall()
        orders = conn.execute("SELECT created_at, order_type, quantity, stop_price, limit_price, status, reason, "
                              "updated_at FROM tg_orders WHERE trading_day = ? AND symbol = ? ORDER BY created_at",
                              (day, symbol)).fetchall()
        events = conn.execute("SELECT at, level, code, message FROM events WHERE trading_day = ? AND symbol = ? "
                              "ORDER BY id DESC LIMIT 100", (day, symbol)).fetchall()
    if row:
        d["facts"] += [["Class", v.cell(v.nice(row["classification"])), ""],
                       ["Reasons", _reasons(row["reasons"]) or "None", ""],
                       ["Hands off", f"{v.cell(bool(row['hands_off']))} {row['hands_off_reason']}".strip(),
                        "warn" if row["hands_off"] else ""],
                       ["Last updated", times.show(row["updated_at"]), ""]]
    if plan:
        for k in ("entry", "qty", "r", "initial_stop", "stop", "hwm", "atr"):
            d["facts"].append([v.nice(k), v.price(plan.get(k)) if k != "qty" else v.cell(plan.get(k)), ""])
        d["facts"] += [["First buy", times.show(plan.get("first_buy_at")), ""],
                       ["Flatten at", times.show(plan.get("flatten_at")), ""],
                       ["Breakeven armed", v.cell(plan.get("breakeven_armed")), ""],
                       ["Partial done", v.cell(plan.get("partial_done")), ""],
                       ["Exit in progress", _exit_text(plan) or "None", "warn" if plan.get("exit") else ""],
                       ["Next ratchet (estimate)", next_ratchet(plan, s), ""]]
    elif not row:
        d["facts"].append(["Plan", "Nothing stored for this symbol and day", "warn"])
    d["tables"].append(v.table("Stop history", ["Time", "From", "To", "Why"],
                               [[times.show_short(r["at"], seconds=True), v.price(r["old"]), v.price(r["new"]),
                                 v.nice(r["reason"])] for r in stops]))
    d["tables"].append(v.table("Exits", ["Time", "Qty", "Price", "Why", "Trigger"],
                               [[times.show_short(r["at"], seconds=True), r["qty"], v.price(r["price"]),
                                 v.nice(r["reason"]), v.price(r["trigger_price"])] for r in exits]))
    d["tables"].append(v.table("Guardian's orders", ["Created", "Type", "Qty", "Stop", "Limit", "Status", "Reason", "Updated"],
                               [[times.show_short(r["created_at"], seconds=True), v.nice(r["order_type"]), r["quantity"],
                                 v.price(r["stop_price"]), v.price(r["limit_price"]), v.nice(r["status"]),
                                 v.nice(r["reason"]), times.show_short(r["updated_at"], seconds=True)] for r in orders]))
    d["tables"].append(v.table("Events, newest first", ["Time", "Level", "What", "Message"],
                               [[times.show_short(r["at"], seconds=True), v.nice(r["level"]), v.nice(r["code"]),
                                 v.sentence(r["message"])] for r in events]))
    return d
