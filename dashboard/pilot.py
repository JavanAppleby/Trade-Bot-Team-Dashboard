"""Pilot panel: status and the gate from its live report, paper trades with their protection,
today's decisions. Coded to Pilot's store.py (tables finds, decisions, positions, measures, events;
`meta` is never read) and its plan files (`state\\plans\\<coid>.json`, PILOT_SPEC 9.1).
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from decimal import Decimal

from dashboard import safe, times
from dashboard import view as v
from dashboard.config import Config

COID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
NO_QUOTE_TAG = "no_quote_60s"
PLAN_UNREADABLE_TAG = "plan_unreadable"


def _paths(cfg: Config) -> dict:
    d = cfg.pilot_dir
    return {"live": d / "reports" / "pilot-live.md", "db": d / "state" / "pilot.db", "plans": d / "state" / "plans",
            "lock": d / "state" / "pilot.lock", "stop": d / "PILOT_STOP",
            "ghb": cfg.guardian_dir / "state" / "guardian.heartbeat"}


def _load(text: object) -> dict:
    if not isinstance(text, str):
        return {}
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def result(pos: dict) -> tuple[Decimal | None, Decimal | None]:
    """(P&L $ after fees, R) from the exits so far, as Pilot defines them (DESIGN 4)."""
    entry, risk = v.dec(pos.get("entry")), v.dec(pos.get("risk_usd"))
    if entry is None:
        return None, None
    gross = sum(((v.dec(e.get("qty")) or 0) * ((v.dec(e.get("price")) or 0) - entry)
                 for e in pos.get("exits") or [] if isinstance(e, dict)), Decimal(0))
    pnl = gross - (v.dec(pos.get("fees_usd")) or 0)
    return pnl, (pnl / risk if risk else None)


def read_plan(paths: dict, coid: str) -> tuple[dict | None, str]:
    """(plan, problem). The plan file must exist, be JSON and name the same order id."""
    if not COID_RE.match(coid or ""):
        return None, "order id is not a plain file name"
    try:
        data = safe.read_json(paths["plans"] / f"{coid}.json", 64 * 1024)
    except safe.ReadError as exc:
        return None, str(exc)
    if data is None:
        return None, "plan file missing"
    if not isinstance(data, dict) or data.get("client_order_id") != coid:
        return None, "plan file does not match this trade"
    if v.dec(data.get("stop")) is None:
        return None, "plan file has no stop"
    return data, ""


def guardian_ok(paths: dict, now: datetime, limit_s: float) -> tuple[bool, str]:
    try:
        hb = safe.read_json(paths["ghb"], safe.HEARTBEAT_CAP)
    except safe.ReadError as exc:
        return False, str(exc)
    if not isinstance(hb, dict) or "at" not in hb:
        return False, "Guardian heartbeat missing, starting or stopped"
    at = times.parse(hb.get("at"))
    if at is None or abs((now - at).total_seconds()) > limit_s:
        return False, "Guardian heartbeat stale"
    if hb.get("unprotected") != 0:
        return False, f"Guardian reports {hb.get('unprotected')} unprotected"
    return True, "Guardian running, 0 unprotected"


def protection(pos: dict, plan_problem: str, pilot_live: bool, guardian: tuple[bool, str]) -> tuple[str, list[str]]:
    """PROTECTED when all five checks pass (DESIGN.md 4.5), else AT RISK naming the failures."""
    tags = pos.get("tags") or []
    fails = []
    if plan_problem or PLAN_UNREADABLE_TAG in tags:
        fails.append(f"plan: {plan_problem or 'unreadable when Pilot restored it'}")
    if v.dec(pos.get("stop")) is None:
        fails.append("no stop set")
    if not pilot_live:
        fails.append("Pilot not live (report stale or missing), so the stop is not being watched")
    if not guardian[0]:
        fails.append(guardian[1])
    if NO_QUOTE_TAG in tags:
        fails.append("no usable quote for 60 s")
    return ("PROTECTED" if not fails else "AT RISK"), fails


def build(cfg: Config, now: datetime) -> dict:
    p = v.panel("pilot", "Pilot (paper)")
    paths = _paths(cfg)
    if not safe.exists(cfg.pilot_dir):
        p["reason"] = f"{cfg.pilot_dir} not found (Pilot is not installed yet)"
        p["headline"] = "not installed"
        return p
    written = safe.mtime(paths["live"])
    if written is None:
        p["badge"], p["reason"] = v.NO_DATA, "pilot-live.md not found"
    else:
        v.freshness(p, datetime.fromtimestamp(written, timezone.utc), now, cfg.pilot_stale_s, "live report")
    v.fact(p, "Running (lock file)", safe.exists(paths["lock"]))
    stop = safe.exists(paths["stop"])
    v.fact(p, "PILOT_STOP", "present: no new entries" if stop else "none", "warn" if stop else "")
    pilot_live = p["badge"] == v.LIVE
    guardian = guardian_ok(paths, now, cfg.guardian_stale_s)
    v.fact(p, "Guardian (for protection)", guardian[1], "good" if guardian[0] else "bad")
    v.guarded(p, "Paper trades", lambda: _trades(p, paths, pilot_live, guardian))
    v.guarded(p, "Decisions", lambda: _decisions(p, paths))
    v.guarded(p, "Live report", lambda: _live(p, paths))
    p["alert"] = p["alert"] or p["badge"] in (v.STALE, v.ERROR)
    return p


def _trades(p: dict, paths: dict, pilot_live: bool, guardian: tuple[bool, str]) -> None:
    with safe.open_db(paths["db"]) as conn:
        safe.require(conn, "positions", {"coid", "symbol", "trading_day", "opened_at", "closed_at", "position_json"})
        day = conn.execute("SELECT MAX(trading_day) FROM positions").fetchone()[0]
        rows = conn.execute("SELECT coid, position_json, closed_at FROM positions "
                            "WHERE closed_at IS NULL OR trading_day = ? "
                            "ORDER BY closed_at IS NOT NULL, opened_at DESC", (day,)).fetchall()
    out, keys, at_risk, open_n = [], [], 0, 0
    for r in rows:
        pos = _load(r["position_json"])
        is_open = r["closed_at"] is None
        pnl, rr = result(pos)
        if is_open:
            open_n += 1
            _, problem = read_plan(paths, r["coid"])
            status, fails = protection(pos, problem, pilot_live, guardian)
            at_risk += status != "PROTECTED"
            prot = status + (": " + "; ".join(fails) if fails else "")
        else:
            prot = "closed"
        out.append([pos.get("symbol"), "open" if is_open else "closed", pos.get("setup_name"),
                    times.show_short(pos.get("opened_at")), v.price(pos.get("entry")), pos.get("qty"),
                    pos.get("remaining"), v.price(pos.get("stop")), v.price(pos.get("guardian_stop")),
                    v.price(pos.get("chart_stop")), v.price(pos.get("target")), v.usd(pos.get("risk_usd")),
                    v.usd(pnl), v.num(rr), pos.get("tags"), prot])
        keys.append(r["coid"])
    p["headline"] = f"{open_n} open paper trade(s)" + (f", {at_risk} AT RISK" if at_risk else "")
    p["alert"] = at_risk > 0
    p["tables"].append(v.table(
        f"Paper trades (open, then closed on {day or 'no day yet'})",
        ["Symbol", "State", "Setup", "Opened", "Entry", "Qty", "Left", "Stop now", "Guardian-rule stop",
         "Chart stop", "Target", "Risk $", "P&L $", "R", "Tags", "Protection"],
        out, keys, note="" if out else "No paper trades yet."))


def _decisions(p: dict, paths: dict) -> None:
    with safe.open_db(paths["db"]) as conn:
        safe.require(conn, "decisions", {"trading_day", "at", "symbol", "session", "taken", "reason"})
        day = conn.execute("SELECT MAX(trading_day) FROM decisions").fetchone()[0]
        rows = conn.execute("SELECT at, symbol, session, taken, reason FROM decisions WHERE trading_day = ? "
                            "ORDER BY at DESC LIMIT 200", (day,)).fetchall()
    p["tables"].append(v.table(f"Decisions ({day or 'none yet'}), newest first, last 200",
                               ["Time", "Symbol", "Session", "Taken", "Reason"],
                               [[times.show_short(r["at"]), r["symbol"], r["session"], bool(r["taken"]), r["reason"]]
                                for r in rows], note="" if rows else "None yet."))


def _live(p: dict, paths: dict) -> None:
    body = safe.read_text(paths["live"])
    if body is None:
        raise FileNotFoundError(paths["live"])
    v.text(p, "Pilot's live report (status, watch list, the gate), as written", body)


def detail(cfg: Config, key: str) -> dict:
    if not COID_RE.match(key):
        raise ValueError("bad key")
    paths = _paths(cfg)
    with safe.open_db(paths["db"]) as conn:
        row = conn.execute("SELECT position_json FROM positions WHERE coid = ?", (key,)).fetchone()
        m = conn.execute("SELECT entry_json, after_json FROM measures WHERE coid = ?", (key,)).fetchone() \
            if safe.table_columns(conn, "measures") else None
        pos = _load(row["position_json"]) if row else {}
        dec = None
        if pos.get("opened_at") and safe.table_columns(conn, "decisions"):
            dec = conn.execute("SELECT at, reason, setup_json, sizing_json, input_ages_json FROM decisions "
                               "WHERE symbol = ? AND taken = 1 AND at <= ? ORDER BY at DESC LIMIT 1",
                               (pos.get("symbol"), pos.get("opened_at"))).fetchone()
    d = v.detail(f"Pilot paper trade {pos.get('symbol', '')} ({key})")
    if not pos:
        d["facts"].append(["Trade", "not found", "warn"])
        return d
    pnl, rr = result(pos)
    for k in ("symbol", "trading_day", "session", "setup_name", "entry", "qty", "remaining", "stop", "guardian_stop",
              "chart_stop", "target", "risk_usd", "fees_usd", "tags", "carried", "high", "low"):
        val = pos.get(k)
        d["facts"].append([k.replace("_", " ").capitalize(),
                           v.price(val) if k in ("entry", "stop", "guardian_stop", "chart_stop", "target", "high", "low")
                           else v.cell(val), ""])
    d["facts"] += [["Opened", times.show(pos.get("opened_at")), ""], ["Closed", times.show(pos.get("closed_at")), ""],
                   ["P&L $ (after fees)", v.usd(pnl), ""], ["R", v.num(rr), ""]]
    if pos.get("closed_at") is None:
        _, problem = read_plan(paths, key)
        live_w = safe.mtime(paths["live"])
        now = datetime.now(timezone.utc)
        live = live_w is not None and (now.timestamp() - live_w) <= cfg.pilot_stale_s
        status, fails = protection(pos, problem, live, guardian_ok(paths, now, cfg.guardian_stale_s))
        d["facts"].insert(0, ["Protection", status + (": " + "; ".join(fails) if fails else ""),
                              "good" if not fails else "bad"])
    gp = pos.get("guardian_plan") if isinstance(pos.get("guardian_plan"), dict) else {}
    if gp:
        d["tables"].append(v.table("Guardian's rules applied to this paper trade", ["Field", "Value"],
                                   [[k, times.show(gp[k]) if k.endswith("_at") else v.cell(gp[k])]
                                    for k in ("stop", "initial_stop", "r", "hwm", "atr", "breakeven_armed",
                                              "partial_done", "flatten_at", "first_buy_at") if k in gp]))
    d["tables"].append(v.table("Exits", ["Time", "Qty", "Price", "Reason", "Trigger"],
                               [[times.show_short(e.get("at")), e.get("qty"), v.price(e.get("price")), e.get("reason"),
                                 v.price(e.get("trigger"))] for e in pos.get("exits") or [] if isinstance(e, dict)]))
    plan, problem = read_plan(paths, key)
    d["texts"].append({"title": "Plan file", "text": json.dumps(plan, indent=1) if plan else f"problem: {problem}"})
    if dec is not None:
        d["texts"].append({"title": f"Decision that took it ({times.show(dec['at'])})",
                           "text": json.dumps({"setup": _load(dec["setup_json"]), "sizing": _load(dec["sizing_json"]),
                                               "input_ages_s": _load(dec["input_ages_json"])}, indent=1)})
    if m is not None:
        d["texts"].append({"title": "Measures", "text": json.dumps({"entry": _load(m["entry_json"]),
                                                                    "after": _load(m["after_json"])}, indent=1)})
    return d
