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
    assert p["headline"] == "1 managed · 0 unprotected"
    pos = table(p, "Positions Guardian is managing")
    assert len(pos["rows"]) == 1  # ZZZ has no plan
    row = dict(zip(pos["columns"], pos["rows"][0]))
    assert row["Symbol"] == "ABCD" and row["Stop now"] == "1.90"
    assert "Breakeven at 2.10 → stop 2.00" in row["Next ratchet (estimate)"]
    assert "R-step at 2.20 → stop 2.10" in row["Next ratchet (estimate)"]
    assert row["Class"] == "Managed"
    assert pos["keys"] == ["2026-11-03|ABCD"]
    assert table(p, "Guardian's working orders")["rows"][0][5] == "Working"
    warn = table(p, "Latest warnings and errors")["rows"][0]
    assert warn[1:3] == ["Warning", "Quote stale"] and warn[4] == "Quote <b>old</b>"
    closed = table(p, "Today's trades")
    assert dict(zip(closed["columns"], closed["rows"][0]))["P&L $"] == "8.00"


def test_guardian_detail(bots):
    d = guardian.detail(bots, "2026-11-03|ABCD")
    f = {x[0]: x[1] for x in d["facts"]}
    assert f["Stop"] == "1.90" and "Breakeven" in f["Next ratchet (estimate)"]
    titles = [t["title"] for t in d["tables"]]
    assert titles == ["Stop history", "Exits", "Guardian's orders", "Events, newest first"]
    assert d["tables"][1]["rows"][0][2] == "2.20"


def test_next_ratchet_after_breakeven():
    s = {"breakeven_at_r": 1.0, "trail_atr_mult": 2.0, "trail_pct": 0.06}
    text = guardian.next_ratchet(guardian_plan(hwm="2.35", stop="2.10", breakeven_armed=True), s)
    assert "Breakeven" not in text
    assert "R-step at 2.40 → stop 2.30" in text
    assert "Trail 2.27 (above the stop" in text
    assert guardian.next_ratchet(guardian_plan(), None).startswith("Unknown")


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
    assert p["badge"] == "LIVE" and p["headline"] == "1 find · Tue 3 Nov"
    finds = table(p, "Radar's finds")
    row = dict(zip(finds["columns"], finds["rows"][0]))
    assert row["Change"] == "28.3%" and row["Tags"] == "Gap and hold" and row["Session"] == "Pre-market"
    hand = table(p, "Handed to Pilot")
    assert len(hand["rows"]) == 1  # the torn last line waits
    assert facts(p)["Radar status"] == "Running"
    d = radar.detail(bots, finds["keys"][0])
    assert d["title"].startswith("Radar · ABCD")
    assert d["tables"][-1]["rows"][0][2] == "2.30"
    assert ["VWAP", "2.1"] in table(d, "Facts")["rows"]


def test_scout(bots):
    p = scout.build(bots, NOW)
    assert p["badge"] == "LIVE" and p["headline"] == "1 of 2 sources OK"
    news = table(p, "News and context")
    assert len(news["rows"]) == 1 and "1 unreadable line(s) skipped" in news["note"]
    assert news["rows"][0][5].startswith("<script>")  # kept as text; the page renders it as text
    assert table(p, "Jason's levels")["rows"][0][4] == "Entry: 2.40"
    ans = table(p, "Answers")["rows"][0]
    assert ans[1] == "ABCD" and ans[3] == "Yes" and ans[4] == "No"
    src = table(p, "Sources")["rows"]
    assert src[0][:3] == ["BLS", "Yes", "Stale"] and src[1][0] == "PR Newswire"
    assert any("All quiet" in t["text"] for t in p["texts"])
    d = scout.detail(bots, news["keys"][0])
    assert ["Kind", "Wire", ""] in d["facts"] and ["Link", "https://example.com/a", ""] in d["facts"]


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
    assert p["badge"] == "LIVE" and p["headline"] == "1 open paper trade" and not p["alert"]
    trades = table(p, "Paper trades")
    rows = [dict(zip(trades["columns"], r)) for r in trades["rows"]]
    assert rows[0]["State"] == "Open" and rows[0]["Protection"] == "Protected"
    assert rows[1]["State"] == "Closed" and rows[1]["P&L $"] == "15.00" and rows[1]["R"] == "1.88"
    assert table(p, "Decisions")["rows"][1][3:] == ["No", "Spread too wide"]


def test_pilot_at_risk_reasons(tmp_path):
    g = make_guardian(tmp_path, hb_age_s=300)
    pd = make_pilot(tmp_path, report_age_s=600, tags=("no_quote_60s",), plan=False)
    p = pilot.build(Config(guardian_dir=g, pilot_dir=pd), NOW)
    prot = dict(zip(table(p, "Paper trades")["columns"], table(p, "Paper trades")["rows"][0]))["Protection"]
    assert prot.startswith("At risk")
    for part in ("Plan file missing", "Pilot not live", "Guardian's heartbeat is stale", "No usable quote"):
        assert part in prot
    assert p["alert"]


def test_pilot_detail(bots):
    d = pilot.detail(bots, "PB1")
    f = {x[0]: x[1] for x in d["facts"]}
    assert f["Protection"] == "Protected" and f["Stop"] == "1.92" and f["Order ID"] == "PB1"
    plan = table(d, "Plan file")["rows"]
    assert ["Stop", "1.92"] in plan and ["Targets", "2.30 for 50.0% of the shares"] in plan
    assert ["Rel volume", "3.1"] in table(pilot.detail(bots, "PB0"), "Measures at entry")["rows"]


def test_pilot_not_installed(empty):
    p = pilot.build(empty, NOW)
    assert p["badge"] == "NO DATA" and "isn't installed yet" in p["reason"]


def test_everything_missing_never_crashes(empty):
    body = Dashboard(empty).overview()
    assert [p["bot"] for p in body["panels"]] == ["scout", "radar", "pilot", "guardian"]
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
    assert app.detail("radar", "999")["facts"][0][1] == "Not found"


def test_future_heartbeat_is_stale(tmp_path):
    hb = {"format": 1, "at": (NOW + timedelta(minutes=5)).isoformat(), "managed": 0, "unprotected": 0}
    assert guardian.build(Config(guardian_dir=make_guardian(tmp_path, hb=hb)), NOW)["badge"] == "STALE"


def test_plain_english_wording():
    from dashboard import times
    from dashboard.view import nice, sentence
    assert nice("trade_events_status") == "Trade events status"
    assert nice("WARNING") == "Warning" and nice("PRE") == "Pre-market" and nice("CORE") == "Regular hours"
    assert nice("open_orders_unreadable_no_writes") == "Open orders unreadable no writes"
    assert nice("cboe_vix") == "Cboe VIX" and nice("edgar_atom") == "EDGAR atom" and nice("hwm") == "High"
    assert nice("true") == "Yes" and nice("unknown") == "Unknown"
    assert nice("10-K: TimeoutError") == "10-K: TimeoutError" and nice("123") == "123"
    assert sentence("positions unreadable") == "Positions unreadable"
    assert sentence("open_orders unreadable (SDK.HttpError)") == "Open orders unreadable (SDK.HttpError)"
    assert times.show_date("2026-11-02") == "Mon 2 Nov"
    from datetime import datetime, timezone
    assert times.show_short(datetime(2026, 10, 31, 12, 38, 24, tzinfo=timezone.utc)) == "31 Oct, 8:38 am"


def test_radar_leaderboards_and_sort(bots):
    p = radar.build(bots, NOW)
    finds = table(p, "Radar's finds")
    assert finds["sort"] == {"col": "Change", "desc": True}
    titles = [t["title"] for t in p["tables"] if t["title"].startswith("Leaderboard")]
    assert titles == ["Leaderboard · Pre-market gainers", "Leaderboard · Volume surges (vs 10-day)"]
    pre = table(p, "Leaderboard · Pre-market gainers")
    assert pre["columns"] == ["Rank", "Symbol", "Price", "Change", "Volume", "Rel vol", "Radar"]
    assert pre["rows"][0] == ["1", "WXYZ", "5.10", "62.0%", "9,000,000", "14.2", ""]
    assert pre["rows"][1][:2] == ["2", "ABCD"] and pre["rows"][1][6] == "Radar find"
    assert pre["rows"][2][:3] == ["3", "ZERO", ""]  # the repeated WXYZ keeps rank 1; a 0 price is blank
    assert pre["keys"] == [None, finds["keys"][0], None]
    assert pre["note"].startswith("Read 30 s ago")
    assert table(p, "Leaderboard · Volume surges")["note"].startswith("Stale: read 10 min ago")


def test_radar_no_leaderboards_yet(tmp_path):
    import sqlite3
    from conftest import make_radar
    r = make_radar(tmp_path)
    conn = sqlite3.connect(r / "state" / "radar.db")
    conn.execute("DELETE FROM raw_rankings")
    conn.commit()
    conn.close()
    p = radar.build(Config(radar_dir=r), NOW)
    assert "No leaderboards read yet" in table(p, "Webull leaderboards")["note"]
