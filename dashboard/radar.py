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
        p["badge"], p["reason"] = v.NO_DATA, "screener-live.md not found"
    else:
        v.freshness(p, datetime.fromtimestamp(written, timezone.utc), now, cfg.radar_stale_s, "live report")
    v.fact(p, "Running (lock file)", safe.exists(paths["lock"]))
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
            v.fact(p, "Radar status", status, "bad" if status == "stopped" else "")
    v.text(p, "Radar's live report (status part, as written)", head)


def _finds(p: dict, paths: dict) -> list:
    with safe.open_db(paths["db"]) as conn:
        safe.require(conn, "screener_candidates", {"id", "trading_day", "session", "symbol", "found_at", "price",
                                                   "change_pct", "rel_volume_10d", "spread_pct", "tags_json",
                                                   "flags_json", "feeds", "last_seen_at"})
        day = conn.execute("SELECT MAX(trading_day) FROM screener_candidates").fetchone()[0]
        rows = conn.execute("SELECT id, symbol, found_at, session, price, change_pct, rel_volume_10d, spread_pct, "
                            "tags_json, flags_json, feeds, last_seen_at FROM screener_candidates "
                            "WHERE trading_day = ? ORDER BY found_at DESC", (day,)).fetchall()
    p["headline"] = f"{len(rows)} finds" + (f" on {day}" if day else "")
    p["tables"].append(v.table(
        f"Finds ({day or 'none yet'}), newest first",
        ["Symbol", "Found", "Session", "Price", "Change", "Rel vol", "Spread", "Tags", "Flags", "Feeds", "Last seen"],
        [[r["symbol"], times.show_short(r["found_at"]), r["session"], v.price(r["price"]), v.pct(r["change_pct"]),
          v.num(r["rel_volume_10d"], 1), v.pct(r["spread_pct"]), _json(r["tags_json"]), _json(r["flags_json"]),
          r["feeds"], times.show_short(r["last_seen_at"])] for r in rows],
        [f"{r['id']}" for r in rows], note="" if rows else "Radar has found nothing yet."))
    return [day] if day else []


def _handoff(p: dict, paths: dict, day: str | None) -> None:
    if not day:
        return
    got = safe.tail_jsonl(paths["handoff"] / f"{day}.jsonl")
    if got is None:
        p["tables"].append(v.table("Handed to Pilot", [], [], note="none yet (no hand-off file for this day)"))
        return
    rows, bad = got
    p["tables"].append(v.table(
        f"Handed to Pilot ({day})", ["Symbol", "Found", "Session", "Price", "Ask", "Change", "Tags"],
        [[r.get("symbol"), times.show_short(r.get("found_at")), r.get("session"), v.price(r.get("price")),
          v.price(r.get("ask")), v.pct(r.get("change_pct")), r.get("tags")] for r in reversed(rows)],
        note=f"{bad} unreadable line(s) skipped" if bad else ""))


def _events(p: dict, paths: dict) -> None:
    with safe.open_db(paths["db"]) as conn:
        safe.require(conn, "events", {"at", "level", "kind", "symbol", "message"})
        rows = conn.execute("SELECT at, level, kind, symbol, message FROM events ORDER BY id DESC LIMIT 20").fetchall()
    p["tables"].append(v.table("Last 20 events", ["Time", "Level", "Kind", "Symbol", "Message"],
                               [[times.show_short(r["at"]), r["level"], r["kind"], r["symbol"], r["message"]]
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
        d["facts"].append(["Find", "not found", "warn"])
        return d
    d = v.detail(f"Radar: {c['symbol']} found {times.show(c['found_at'])}")
    for k in ("trading_day", "session", "feeds", "price", "change_pct", "rel_volume_10d", "spread_pct", "volume",
              "price_band", "facts_complete", "flags_complete", "config_hash", "tg_release"):
        val = c[k]
        if k in ("change_pct", "spread_pct"):
            val = v.pct(val)
        d["facts"].append([k.replace("_", " ").capitalize(), v.cell(val), ""])
    d["facts"].append(["Last seen", times.show(c["last_seen_at"]), ""])
    for k in ("tags_json", "flags_json", "filter_json", "facts_json"):
        d["texts"].append({"title": k.removesuffix("_json").capitalize(),
                           "text": json.dumps(_json(c[k]), indent=1, sort_keys=True)})
    if out is not None:
        d["tables"].append(v.table("Outcome", ["Field", "Value"],
                                   [[k, out[k]] for k in out.keys() if k not in ("candidate_id", "windows_json", "sim_json")]))
        d["texts"].append({"title": "Simulation (Guardian's rules)", "text": json.dumps(_json(out["sim_json"]), indent=1)})
    d["tables"].append(v.table("Price observations (newest first)", ["Time", "Price", "Bid", "Ask", "Spread", "Volume"],
                               [[times.show_short(r["at"]), v.price(r["price"]), v.price(r["bid"]), v.price(r["ask"]),
                                 v.pct(r["spread_pct"]), r["volume"]] for r in obs]))
    return d
