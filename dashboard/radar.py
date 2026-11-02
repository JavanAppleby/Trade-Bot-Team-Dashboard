"""Radar panel: status from its live report, today's finds, the hand-off to Pilot, events."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from dashboard import safe, times
from dashboard import view as v
from dashboard.config import Config


def _paths(cfg: Config) -> dict:
    r = cfg.radar_dir
    return {"live": r / "reports" / "screener-live.md", "db": r / "state" / "radar.db",
            "lock": r / "state" / "radar.lock", "handoff": r / "state" / "handoff"}


def _json(text: object) -> object:
    if not isinstance(text, str):
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def build(cfg: Config, now: datetime) -> dict:
    p = v.panel("radar", "Radar")
    paths = _paths(cfg)
    written = safe.mtime(paths["live"])
    if written is None:
        p["badge"], p["reason"] = v.NO_DATA, "Radar's live report (screener-live.md) not found"
    else:
        v.freshness(p, datetime.fromtimestamp(written, timezone.utc), now, cfg.radar_stale_s, "Live report")
    v.fact(p, "Running", safe.exists(paths["lock"]))
    found: list = []
    v.guarded(p, "Live report", lambda: _status(p, paths))
    v.guarded(p, "Finds", lambda: found.extend(_finds(p, paths)))
    day = found[0] if found else None
    v.guarded(p, "Handed to Pilot", lambda: _handoff(p, paths, day))
    v.guarded(p, "Events", lambda: _events(p, paths))
    p["alert"] = p["badge"] in (v.STALE, v.ERROR)
    return p


def _status(p: dict, paths: dict) -> None:
    body = safe.read_text(paths["live"])
    if body is None:
        raise FileNotFoundError(paths["live"])
    head = body.split("\n## Candidates", 1)[0].strip()
    for line in head.splitlines():
        if "status **" in line:
            status = line.split("status **", 1)[1].split("**", 1)[0]
            v.fact(p, "Radar status", v.nice(status), "bad" if status == "stopped" else "")
    v.text(p, "Radar's live report (as Radar wrote it)", head)


def _finds(p: dict, paths: dict) -> list:
    with safe.open_db(paths["db"]) as conn:
        safe.require(conn, "screener_candidates", {"id", "trading_day", "session", "symbol", "found_at", "price",
                                                   "change_pct", "rel_volume_10d", "spread_pct", "tags_json",
                                                   "flags_json", "feeds", "last_seen_at"})
        day = conn.execute("SELECT MAX(trading_day) FROM screener_candidates").fetchone()[0]
        rows = conn.execute("SELECT id, symbol, found_at, session, price, change_pct, rel_volume_10d, spread_pct, "
                            "tags_json, flags_json, feeds, last_seen_at FROM screener_candidates "
                            "WHERE trading_day = ? ORDER BY found_at DESC", (day,)).fetchall()
    p["headline"] = f"{len(rows)} find{'' if len(rows) == 1 else 's'}" + (f" · {times.show_date(day)}" if day else "")
    p["tables"].append(v.table(
        f"Radar's finds · {times.show_date(day) or 'none yet'}",
        ["Symbol", "Found", "Session", "Price", "Change", "Rel vol", "Spread", "Tags", "Flags", "Feeds", "Last seen"],
        [[r["symbol"], times.show_short(r["found_at"]), v.nice(r["session"]), v.price(r["price"]), v.pct(r["change_pct"]),
          v.num(r["rel_volume_10d"], 1), v.pct(r["spread_pct"]), v.nice(_json(r["tags_json"])),
          v.nice(_json(r["flags_json"])), v.nice(r["feeds"]), times.show_short(r["last_seen_at"])] for r in rows],
        [f"{r['id']}" for r in rows], note="" if rows else "Nothing found yet."))
    return [day] if day else []


def _handoff(p: dict, paths: dict, day: str | None) -> None:
    if not day:
        return
    got = safe.tail_jsonl(paths["handoff"] / f"{day}.jsonl")
    if got is None:
        p["tables"].append(v.table("Handed to Pilot", [], [], note="Nothing handed to Pilot yet."))
        return
    rows, bad = got
    p["tables"].append(v.table(
        f"Handed to Pilot · {times.show_date(day)}", ["Symbol", "Found", "Session", "Price", "Ask", "Change", "Tags"],
        [[r.get("symbol"), times.show_short(r.get("found_at")), v.nice(r.get("session")), v.price(r.get("price")),
          v.price(r.get("ask")), v.pct(r.get("change_pct")), v.nice(r.get("tags"))] for r in reversed(rows)],
        note=f"{bad} unreadable line(s) skipped." if bad else ""))


def _events(p: dict, paths: dict) -> None:
    with safe.open_db(paths["db"]) as conn:
        safe.require(conn, "events", {"at", "level", "kind", "symbol", "message"})
        rows = conn.execute("SELECT at, level, kind, symbol, message FROM events ORDER BY id DESC LIMIT 20").fetchall()
    p["tables"].append(v.table("Latest events", ["Time", "Level", "What", "Symbol", "Message"],
                               [[times.show_short(r["at"]), v.nice(r["level"]), v.nice(r["kind"]), r["symbol"],
                                 v.sentence(r["message"])]
                                for r in rows], note="" if rows else "None."))


def detail(cfg: Config, key: str) -> dict:
    cid = int(key)
    paths = _paths(cfg)
    with safe.open_db(paths["db"]) as conn:
        c = conn.execute("SELECT * FROM screener_candidates WHERE id = ?", (cid,)).fetchone()
        obs = conn.execute("SELECT at, price, bid, ask, spread_pct, volume FROM screener_observations "
                           "WHERE candidate_id = ? ORDER BY at DESC LIMIT 200", (cid,)).fetchall()
        out = conn.execute("SELECT * FROM screener_outcomes WHERE candidate_id = ?", (cid,)).fetchone()
    if c is None:
        d = v.detail(f"Radar find {cid}")
        d["facts"].append(["Find", "Not found", "warn"])
        return d
    d = v.detail(f"Radar · {c['symbol']} · found {times.show(c['found_at'])}")
    d["facts"] += [["Session", v.cell(v.nice(c["session"])), ""], ["Price", v.price(c["price"]), ""],
                   ["Change", v.pct(c["change_pct"]), ""], ["Rel volume (10-day)", v.num(c["rel_volume_10d"], 1), ""],
                   ["Spread", v.pct(c["spread_pct"]), ""], ["Volume", v.cell(c["volume"]), ""],
                   ["Price band", v.cell(c["price_band"]), ""], ["Feeds", v.cell(v.nice(c["feeds"])), ""],
                   ["Tags", v.cell(v.nice(_json(c["tags_json"]))) or "None", ""],
                   ["Flags", v.cell(v.nice(_json(c["flags_json"]))) or "None", ""],
                   ["Facts complete", v.cell(bool(c["facts_complete"])), ""],
                   ["Flags complete", v.cell(bool(c["flags_complete"])), ""],
                   ["Last seen", times.show(c["last_seen_at"]), ""]]
    if out is not None:
        d["tables"].append(v.kv_table("Outcome", {k: out[k] for k in out.keys()
                                                  if k not in ("candidate_id", "windows_json", "sim_json")}))
        d["tables"].append(v.kv_table("Simulation with Guardian's rules", _json(out["sim_json"])))
    d["tables"].append(v.kv_table("Facts", _json(c["facts_json"])))
    d["tables"].append(v.kv_table("Filter checks", _json(c["filter_json"])))
    d["tables"].append(v.table("Price checks, newest first", ["Time", "Price", "Bid", "Ask", "Spread", "Volume"],
                               [[times.show_short(r["at"], seconds=True), v.price(r["price"]), v.price(r["bid"]),
                                 v.price(r["ask"]), v.pct(r["spread_pct"]), r["volume"]] for r in obs]))
    return d
