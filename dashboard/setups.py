"""Entry setups for the Radar tab: Radar's stocks ranked by Pilot's own entry checks (PILOT_SPEC 5.3).

The dashboard computes no score of its own. It reads Pilot's latest decision per symbol from
`pilot.db` (`decisions`: the Setup with every check by name, and the Sizing when the setup passed)
and orders them:
  1. Setup now   every check passes; best reward:risk first
  2. Close       one or two entry conditions missing; fewest missing first
  3. Not close   more missing
  4. Unknown     the quote, bars or session check failed, or Pilot has not re-checked it lately
Pilot stores a repeated identical skip at most once a minute, so a record up to about a minute old
is current; older than RECHECK_S counts as not re-checked. Records older than HIDE_AFTER_S (Pilot
dropped the symbol or stopped) are left out.
"""

from __future__ import annotations

import json
from datetime import datetime

from dashboard import safe, times
from dashboard import view as v
from dashboard.config import Config

RECHECK_S = 90
HIDE_AFTER_S = 600
DATA_CHECKS = ("quote_fresh", "bars_fresh", "session_ok")
# Pilot's check names (pilot.setup.CHECKS) in plain words, without thresholds: Pilot's config sets those.
LABELS = {"quote_fresh": "Fresh quote", "bars_fresh": "Fresh 1-minute bars", "session_ok": "Session open for Pilot",
          "price_range": "Price in range", "change": "Up enough on the day", "spread": "Spread tight enough",
          "dollars_15m": "Enough traded in 15 min", "leg": "Leg old enough", "above_pmh": "Above pre-market high",
          "above_vwap": "Well above VWAP", "rvol": "Normal day's volume", "pullback": "Shallow pullback"}
DATA_MISSING = {"quote_fresh": "no fresh quote", "bars_fresh": "no fresh bars",
                "session_ok": "session closed for Pilot"}
GROUPS = ("Setup now", "Close", "Not close", "Unknown")
NOTE = ("Ranked by Pilot's entry checks (Pilot spec 5.3: an established 20% leg, above the pre-market high, "
        "well above VWAP, normal volume, and a shallow 1-5% pullback rather than a new high). "
        "Unproven until Pilot's paper gate (30 trades) is met. Prices are Pilot's last check, not live.")


def _paths(cfg: Config) -> dict:
    return {"dir": cfg.pilot_dir, "db": cfg.pilot_dir / "state" / "pilot.db"}


def _load(text: object) -> dict:
    if not isinstance(text, str):
        return {}
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _ratio(a: object, b: object) -> object:
    da, db = v.dec(a), v.dec(b)
    return da / db - 1 if da is not None and db else None


def measures(st: dict) -> dict:
    """The values behind the checks, as text for the table and the 'missing' notes."""
    price = v.dec(st.get("price"))
    high = v.dec(st.get("day_high"))
    off_high = (high - price) / high if price and high else None
    leg = v.dec(st.get("leg_min"))
    return {"leg": "" if leg is None else f"{leg:.0f} min",
            "vwap": v.pct(_ratio(st.get("price"), st.get("vwap"))),
            "off_high": "" if off_high is None else ("at the high" if off_high <= 0 else v.pct(off_high)),
            "rvol": "" if v.dec(st.get("rvol")) is None else f"{v.dec(st.get('rvol')):.1f}x",
            "pmh": v.pct(_ratio(st.get("price"), st.get("premarket_high"))),
            "spread": v.pct(st.get("spread_pct")), "change": v.pct(st.get("change_pct")),
            "dollars_15m": "" if v.dec(st.get("dollars_15m")) is None else f"${v.dec(st.get('dollars_15m')):,.0f}"}


def missing_text(name: str, m: dict) -> str:
    value = {"leg": m["leg"], "above_vwap": m["vwap"], "pullback": m["off_high"], "rvol": m["rvol"],
             "above_pmh": m["pmh"], "spread": m["spread"], "change": m["change"],
             "dollars_15m": m["dollars_15m"]}.get(name, "")
    if name == "pullback" and value and value != "at the high":
        value += " off the high"
    elif name == "above_vwap" and value:
        value += " vs VWAP"
    return LABELS.get(name, v.nice(name)) + (f" ({value})" if value else "")


def classify(st: dict, age_s: float) -> tuple[int, list[str]]:
    """(group index into GROUPS, the entry checks that failed)."""
    checks = st.get("checks") if isinstance(st.get("checks"), dict) else {}
    if not checks or age_s > RECHECK_S or not all(checks.get(c) for c in DATA_CHECKS):
        return 3, [c for c in checks if not checks[c] and c not in DATA_CHECKS]
    failed = [c for c in checks if not checks[c]]
    return (0 if not failed else 1 if len(failed) <= 2 else 2), failed


def build_section(p: dict, cfg: Config, now: datetime) -> None:
    title = "Entry setups · ranked by Pilot's checks"
    paths = _paths(cfg)
    if not safe.exists(paths["dir"]):
        p["tables"].append(v.table(title, [], [], note=(
            "Pilot isn't installed yet, so there are no entry checks. This list fills in while Pilot runs.")))
        return
    with safe.open_db(paths["db"]) as conn:
        safe.require(conn, "decisions", {"id", "trading_day", "at", "symbol", "taken", "reason", "setup_json",
                                         "sizing_json"})
        day = conn.execute("SELECT MAX(trading_day) FROM decisions").fetchone()[0]
        rows = conn.execute(
            "SELECT d.id, d.at, d.symbol, d.taken, d.reason, d.setup_json, d.sizing_json FROM decisions d "
            "JOIN (SELECT symbol, MAX(at) AS at FROM decisions WHERE trading_day = ? GROUP BY symbol) m "
            "ON d.symbol = m.symbol AND d.at = m.at WHERE d.trading_day = ?", (day, day)).fetchall()
    ranked, hidden = [], 0
    for r in rows:
        at = times.parse(r["at"])
        age = (now - at).total_seconds() if at else float("inf")
        if age > HIDE_AFTER_S:
            hidden += 1
            continue
        st, sz = _load(r["setup_json"]), _load(r["sizing_json"])
        group, failed = classify(st, age)
        rr = v.dec(sz.get("reward_risk"))
        ranked.append((group, len(failed), -(rr or 0), r["symbol"], r, st, sz, failed, age))
    ranked.sort(key=lambda x: x[:4])
    out, keys = [], []
    for n, (group, _, _, symbol, r, st, sz, failed, age) in enumerate(ranked, start=1):
        m = measures(st)
        status = GROUPS[group]
        if group == 3 and age > RECHECK_S:
            status = "Unknown: not re-checked"
        elif group == 3:
            status = "Unknown: " + ", ".join(DATA_MISSING[c] for c in DATA_CHECKS
                                             if not (st.get("checks") or {}).get(c))
        out.append([n, symbol, status, "; ".join(missing_text(c, m) for c in failed) or "Nothing",
                    m["leg"], m["vwap"], m["off_high"], m["rvol"], v.num(sz.get("reward_risk"), 1),
                    v.price(sz.get("entry") or st.get("ask")), v.price(sz.get("stop")),
                    v.price(sz.get("reward_target")), v.nice(r["reason"]),
                    times.age_text(age), v.nice(list(st.get("tags") or []))])
        keys.append(f"setup:{r['id']}")
    setups_now = sum(1 for x in ranked if x[0] == 0)
    if setups_now:
        p["headline"] = (p["headline"] + " · " if p["headline"] else "") + \
            f"{setups_now} setup{'' if setups_now == 1 else 's'} now"
    note = NOTE + (f" {hidden} symbol(s) Pilot last checked more than {HIDE_AFTER_S // 60} minutes ago are hidden."
                   if hidden else "")
    if not out:
        note = ("Pilot hasn't checked any stocks in the last 10 minutes (it checks Radar's finds while a "
                "session is on). " + note)
    p["tables"].append(v.table(
        f"{title} · {times.show_date(day)}" if day else title,
        ["Rank", "Symbol", "Status", "Not yet", "Leg", "Vs VWAP", "Off high", "Rel vol", "Reward:risk",
         "Entry (ask)", "Stop", "Target", "Pilot's call", "Checked", "Tags"],
        out, keys, note=note))


def detail(cfg: Config, decision_id: int) -> dict:
    with safe.open_db(_paths(cfg)["db"]) as conn:
        r = conn.execute("SELECT at, symbol, session, taken, reason, setup_json, sizing_json, input_ages_json "
                         "FROM decisions WHERE id = ?", (decision_id,)).fetchone()
    d = v.detail("Entry setup")
    if r is None:
        d["facts"].append(["Check", "Not found", "warn"])
        return d
    st, sz = _load(r["setup_json"]), _load(r["sizing_json"])
    d["title"] = f"Entry setup · {r['symbol']} · checked {times.show_short(r['at'], seconds=True)}"
    m = measures(st)
    d["facts"] += [["Pilot's call", v.cell(v.nice(r["reason"])), "good" if r["taken"] else ""],
                   ["Session", v.cell(v.nice(r["session"])), ""], ["Ask", v.price(st.get("ask")), ""],
                   ["Bid", v.price(st.get("bid")), ""], ["Spread", m["spread"], ""],
                   ["Change on the day", m["change"], ""], ["Leg", m["leg"], ""],
                   ["Vs VWAP", m["vwap"], ""], ["Vs pre-market high", m["pmh"], ""],
                   ["Day's high", v.price(st.get("day_high")), ""], ["Off the high", m["off_high"], ""],
                   ["Rel volume", m["rvol"], ""], ["Traded in 15 min", m["dollars_15m"], ""],
                   ["Tags", v.cell(v.nice(list(st.get("tags") or []))) or "None", ""]]
    checks = st.get("checks") if isinstance(st.get("checks"), dict) else {}
    d["tables"].append(v.table("Every check", ["Check", "Result"],
                               [[LABELS.get(c, v.nice(c)), "Pass" if ok else "Fail"] for c, ok in checks.items()]))
    if sz:
        d["tables"].append(v.table("Pilot's sizing", ["", ""], [
            ["Entry", v.price(sz.get("entry"))], ["Stop used", v.price(sz.get("stop"))],
            ["Chart stop", v.price(sz.get("chart_stop"))], ["Guardian's stop", v.price(sz.get("guardian_stop"))],
            ["Target (the day's high)", v.price(sz.get("reward_target"))],
            ["Reward:risk", v.num(sz.get("reward_risk"), 2)], ["Risk per share", v.price(sz.get("risk_per_share"))],
            ["Shares (paper)", v.cell(sz.get("shares"))], ["Tags", v.cell(v.nice(list(sz.get("tags") or []))) or "None"],
            ["Skipped because", v.cell(v.nice(sz.get("skip"))) or "Not skipped"]]))
    else:
        d["tables"].append(v.table("Pilot's sizing", [], [], note="Pilot sizes a stock only when every check passes."))
    d["tables"].append(v.kv_table("Age of each input (seconds)", _load(r["input_ages_json"])))
    return d
