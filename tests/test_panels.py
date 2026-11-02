from __future__ import annotations

import json
from datetime import timedelta

from conftest import NOW, guardian_plan, make_guardian, make_pilot, make_scout
from dashboard import guardian, pilot, radar, scout
from dashboard.config import Config
from dashboard.server import Dashboard


def table(p: dict, starts: str) -> dict:
    return next(t for t in p["tables"] if t["title"].startswith(starts))


def facts(p: dict) -> dict:
    return {f[0]: f[1] for f in p["facts"]}


def test_guardian_live_with_position_stops_and_ratchet(bots):
    p = guardian.build(bots, NOW)
    assert p["badge"] == "LIVE" and not p["alert"]
    assert p["headline"] == "managed 1, unprotected 0"
    pos = table(p, "Positions with a plan")
    assert len(pos["rows"]) == 1  # ZZZ has no plan
    row = dict(zip(pos["columns"], pos["rows"][0]))
    assert row["Symbol"] == "ABCD" and row["Stop now"] == "1.90"
    assert "breakeven at 2.10: stop to 2.00" in row["Next ratchet (estimate)"]
    assert "R-step at 2.20: stop to 2.10" in row["Next ratchet (estimate)"]
    assert pos["keys"] == ["2026-11-03|ABCD"]
    assert table(p, "Guardian's working orders")["rows"][0][5] == "WORKING"
    closed = table(p, "Trades journalled")
    assert dict(zip(closed["columns"], closed["rows"][0]))["P&L $"] == "8.00"


def test_guardian_detail(bots):
    d = guardian.detail(bots, "2026-11-03|ABCD")
    f = {x[0]: x[1] for x in d["facts"]}
    assert f["Stop"] == "1.90" and "breakeven" in f["Next ratchet (estimate)"]
    titles = [t["title"] for t in d["tables"]]
    assert titles == ["Stop history", "Exits", "Guardian orders", "Events (newest first)"]
    assert d["tables"][1]["rows"][0][2] == "2.20"


def test_next_ratchet_after_breakeven():
    s = {"breakeven_at_r": 1.0, "trail_atr_mult": 2.0, "trail_pct": 0.06}
    text = guardian.next_ratchet(guardian_plan(hwm="2.35", stop="2.10", breakeven_armed=True), s)
    assert "breakeven" not in text
    assert "R-step at 2.40: stop to 2.30" in text
    assert "trail 2.27 (above the stop" in text
    assert guardian.next_ratchet(guardian_plan(), None).startswith("unknown")


def test_guardian_unprotected_alerts(tmp_path):
    hb = {"format": 1, "at": NOW.isoformat(), "mode": "live", "managed": 2, "unprotected": 1,
          "unprotected_symbols": ["ABCD"], "trading_day": "2026-11-03"}
    cfg = Config(guardian_dir=make_guardian(tmp_path, hb=hb))
    p = guardian.build(cfg, NOW)
    assert p["alert"] and facts(p)["Unprotected"] == "1 ABCD"


def test_guardian_stale_starting_stopped(tmp_path):
    cfg = Config(guardian_dir=make_guardian(tmp_path / "a", hb_age_s=95))
    p = guardian.build(cfg, NOW)
    assert p["badge"] == "STALE" and p["alert"] and "stale after 30 s" in p["reason"]
    for state, badge in (("starting", "STARTING"), ("stopped", "STOPPED")):
        cfg = Config(guardian_dir=make_guardian(tmp_path / state, hb={"state": state}))
        assert guardian.build(cfg, NOW)["badge"] == badge


def test_radar(bots):
    p = radar.build(bots, NOW)
    assert p["badge"] == "LIVE" and p["headline"] == "1 finds on 2026-11-03"
    finds = table(p, "Finds")
    row = dict(zip(finds["columns"], finds["rows"][0]))
    assert row["Change"] == "28.3%" and row["Tags"] == "gap_and_hold"
    hand = table(p, "Handed to Pilot")
    assert len(hand["rows"]) == 1  # the torn last line waits
    assert facts(p)["Radar status"] == "running"
    d = radar.detail(bots, finds["keys"][0])
    assert d["title"].startswith("Radar: ABCD")
    assert d["tables"][-1]["rows"][0][2] == "2.30"


def test_scout(bots):
    p = scout.build(bots, NOW)
    assert p["badge"] == "LIVE" and p["headline"] == "sources OK 1 of 2"
    news = table(p, "News and context")
    assert len(news["rows"]) == 1 and "1 unreadable line(s) skipped" in news["note"]
    assert news["rows"][0][5].startswith("<script>")  # kept as text; the page renders it as text
    assert table(p, "Jason's levels")["rows"][0][4] == "entry: 2.40"
    assert table(p, "Answers")["rows"][0][1] == "ABCD"
    assert any("All quiet" in t["text"] for t in p["texts"])
    d = scout.detail(bots, news["keys"][0])
    assert ["kind", "wire", ""] in d["facts"]


def test_scout_stopped_and_stale(tmp_path):
    s = make_scout(tmp_path, hb_age_s=600)
    p = scout.build(Config(scout_dir=s), NOW)
    assert p["badge"] == "STALE"
    hb = json.loads((s / "state" / "scout.heartbeat").read_text())
    hb["stopped"] = True
    (s / "state" / "scout.heartbeat").write_text(json.dumps(hb))
    assert scout.build(Config(scout_dir=s), NOW)["badge"] == "STOPPED"


def test_pilot_protected(bots):
    p = pilot.build(bots, NOW)
    assert p["badge"] == "LIVE" and p["headline"] == "1 open paper trade(s)" and not p["alert"]
    trades = table(p, "Paper trades")
    rows = [dict(zip(trades["columns"], r)) for r in trades["rows"]]
    assert rows[0]["State"] == "open" and rows[0]["Protection"] == "PROTECTED"
    assert rows[1]["State"] == "closed" and rows[1]["P&L $"] == "15.00" and rows[1]["R"] == "1.88"
    assert table(p, "Decisions")["rows"][1][4] == "spread_too_wide"


def test_pilot_at_risk_reasons(tmp_path):
    g = make_guardian(tmp_path, hb_age_s=300)
    pd = make_pilot(tmp_path, report_age_s=600, tags=("no_quote_60s",), plan=False)
    p = pilot.build(Config(guardian_dir=g, pilot_dir=pd), NOW)
    prot = dict(zip(table(p, "Paper trades")["columns"], table(p, "Paper trades")["rows"][0]))["Protection"]
    assert prot.startswith("AT RISK")
    for part in ("plan file missing", "Pilot not live", "Guardian heartbeat stale", "no usable quote"):
        assert part in prot
    assert p["alert"]


def test_pilot_detail(bots):
    d = pilot.detail(bots, "PB1")
    f = {x[0]: x[1] for x in d["facts"]}
    assert f["Protection"] == "PROTECTED" and f["Stop"] == "1.92"
    assert any(t["title"] == "Plan file" and '"stop": "1.92"' in t["text"] for t in d["texts"])
    assert pilot.detail(bots, "PB0")["texts"][-1]["title"] == "Measures"


def test_pilot_not_installed(empty):
    p = pilot.build(empty, NOW)
    assert p["badge"] == "NO DATA" and "not installed" in p["reason"]


def test_everything_missing_never_crashes(empty):
    body = Dashboard(empty).overview()
    assert [p["badge"] for p in body["panels"]] == ["NO DATA"] * 4


def test_corrupt_files(tmp_path):
    g = make_guardian(tmp_path)
    (g / "state" / "guardian.heartbeat").write_text('{"at": "2026-')
    (g / "state" / "guardian.db").write_bytes(b"this is not a database" * 100)
    p = guardian.build(Config(guardian_dir=g), NOW)
    assert p["badge"] == "ERROR" and "not valid JSON" in p["reason"]
    assert any(t["note"].startswith("error:") for t in p["tables"])


def test_changed_db_shape(tmp_path):
    import sqlite3
    pd = make_pilot(tmp_path)
    conn = sqlite3.connect(pd / "state" / "pilot.db")
    conn.execute("ALTER TABLE positions RENAME TO positions_old")
    conn.commit()
    conn.close()
    p = pilot.build(Config(pilot_dir=pd, guardian_dir=tmp_path / "none"), NOW)
    assert "database shape not recognised" in table(p, "Paper trades")["note"]


def test_bad_detail_keys(bots):
    app = Dashboard(bots)
    assert app.detail("nobot", "x")["title"] == "Not found"
    assert app.detail("pilot", "..\\..\\x")["title"] == "Error"
    assert app.detail("scout", "../x|y")["title"] == "Error"
    assert app.detail("radar", "999")["facts"][0][1] == "not found"


def test_future_heartbeat_is_stale(tmp_path):
    hb = {"format": 1, "at": (NOW + timedelta(minutes=5)).isoformat(), "managed": 0, "unprotected": 0}
    assert guardian.build(Config(guardian_dir=make_guardian(tmp_path, hb=hb)), NOW)["badge"] == "STALE"
