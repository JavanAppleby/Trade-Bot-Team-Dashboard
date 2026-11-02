"""Fixtures: small copies of each bot's folder in a temp dir. Never the real folders, no network."""

from __future__ import annotations

import json
import os
import socket
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from dashboard.config import Config

NOW = datetime.now(timezone.utc).replace(microsecond=0)
DAY = "2026-11-03"
SECRET = "ACCT-99887766"


def iso(dt: datetime) -> str:
    return dt.isoformat()


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    real = socket.socket.connect

    def guarded(self, address):
        host = address[0] if isinstance(address, tuple) else address
        if host not in ("127.0.0.1", "localhost", "::1"):
            raise AssertionError(f"network access in a test: {address}")
        return real(self, address)

    monkeypatch.setattr(socket.socket, "connect", guarded)


def write_db(path: Path, ddl: str, rows: dict[str, list[tuple]], wal: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    if wal:
        conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(ddl)
    for table, values in rows.items():
        for v in values:
            conn.execute(f"INSERT INTO {table} VALUES ({','.join('?' * len(v))})", v)
    conn.commit()
    conn.close()  # a clean close removes -wal and -shm


GUARDIAN_DDL = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE symbol_state (trading_day TEXT NOT NULL, symbol TEXT NOT NULL, classification TEXT,
  reasons TEXT NOT NULL DEFAULT '[]', hands_off INTEGER NOT NULL DEFAULT 0, hands_off_reason TEXT NOT NULL DEFAULT '',
  reject_count INTEGER NOT NULL DEFAULT 0, breaker_until TEXT, halted_until TEXT, cent_ticks INTEGER NOT NULL DEFAULT 0,
  last_replace_at TEXT, plan TEXT, updated_at TEXT, PRIMARY KEY (trading_day, symbol));
CREATE TABLE tg_orders (client_order_id TEXT PRIMARY KEY, symbol TEXT NOT NULL, order_type TEXT NOT NULL,
  quantity INTEGER NOT NULL, extended_hours INTEGER NOT NULL, time_in_force TEXT NOT NULL DEFAULT '',
  reason TEXT NOT NULL DEFAULT '', trading_day TEXT NOT NULL, position_key TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, stop_price TEXT, limit_price TEXT,
  trigger_price TEXT, order_id TEXT, error_code TEXT, shadow INTEGER NOT NULL DEFAULT 0);
CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, trading_day TEXT NOT NULL,
  level TEXT NOT NULL, code TEXT NOT NULL, message TEXT NOT NULL, symbol TEXT, data TEXT NOT NULL DEFAULT '{}');
CREATE TABLE carried (trading_day TEXT NOT NULL, symbol TEXT NOT NULL, position_key TEXT NOT NULL, qty INTEGER NOT NULL,
  entry TEXT NOT NULL, first_buy_at TEXT NOT NULL, other_qty INTEGER NOT NULL DEFAULT 0, from_day TEXT,
  active INTEGER NOT NULL DEFAULT 1, PRIMARY KEY (trading_day, symbol));
CREATE TABLE journal_trades (position_key TEXT PRIMARY KEY, symbol TEXT NOT NULL, trading_day TEXT NOT NULL,
  first_buy_at TEXT NOT NULL, session TEXT NOT NULL, entry TEXT NOT NULL, qty INTEGER NOT NULL, initial_stop TEXT NOT NULL,
  r TEXT NOT NULL, atr TEXT, high TEXT, low TEXT, closed_at TEXT, shadow INTEGER NOT NULL DEFAULT 0,
  opened_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE journal_stop_changes (id INTEGER PRIMARY KEY AUTOINCREMENT, position_key TEXT NOT NULL, at TEXT NOT NULL,
  old TEXT, new TEXT NOT NULL, reason TEXT NOT NULL);
CREATE TABLE journal_exits (position_key TEXT NOT NULL, client_order_id TEXT NOT NULL, at TEXT NOT NULL,
  qty TEXT NOT NULL, price TEXT NOT NULL, reason TEXT NOT NULL, trigger_price TEXT, PRIMARY KEY (position_key, client_order_id));
"""

RADAR_DDL = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE screener_candidates (id INTEGER PRIMARY KEY, trading_day TEXT NOT NULL, session TEXT NOT NULL,
  symbol TEXT NOT NULL, found_at TEXT NOT NULL, feeds TEXT NOT NULL, instrument_id TEXT, facts_json TEXT NOT NULL,
  price TEXT NOT NULL, spread_pct TEXT, change_pct TEXT, rel_volume_10d TEXT, volume TEXT, price_band TEXT NOT NULL,
  filter_json TEXT NOT NULL, tags_json TEXT NOT NULL DEFAULT '[]', flags_json TEXT NOT NULL,
  facts_complete INTEGER NOT NULL DEFAULT 0, flags_complete INTEGER NOT NULL DEFAULT 0, config_hash TEXT NOT NULL,
  tg_release TEXT NOT NULL, last_seen_at TEXT, UNIQUE (trading_day, session, symbol));
CREATE TABLE screener_observations (candidate_id INTEGER NOT NULL, at TEXT NOT NULL, price TEXT, bid TEXT, ask TEXT,
  spread_pct TEXT, volume TEXT, last_print_at TEXT, PRIMARY KEY (candidate_id, at));
CREATE TABLE screener_outcomes (candidate_id INTEGER PRIMARY KEY, computed_at TEXT NOT NULL, final INTEGER NOT NULL,
  bars_truncated INTEGER NOT NULL, windows_json TEXT NOT NULL, eod_close_pct TEXT, plus30_pct TEXT, green_min INTEGER,
  mae_before_green_pct TEXT, mfe_pct TEXT, mae_pct TEXT, peak_pct TEXT, peak_min INTEGER, sim_json TEXT,
  sim_outcome TEXT, sim_result_pct TEXT, sim_result_r TEXT, tg_release TEXT NOT NULL);
CREATE TABLE events (id INTEGER PRIMARY KEY, at TEXT NOT NULL, level TEXT NOT NULL, kind TEXT NOT NULL,
  symbol TEXT, message TEXT NOT NULL);
CREATE TABLE raw_rankings (id INTEGER PRIMARY KEY, trading_day TEXT NOT NULL, received_at TEXT NOT NULL,
  feed TEXT NOT NULL, body_json TEXT NOT NULL);
"""

PILOT_DDL = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE finds (id INTEGER PRIMARY KEY, trading_day TEXT NOT NULL, session TEXT NOT NULL, symbol TEXT NOT NULL,
  found_at TEXT NOT NULL, candidate_id INTEGER NOT NULL, find_json TEXT NOT NULL, added_at TEXT NOT NULL);
CREATE TABLE decisions (id INTEGER PRIMARY KEY, trading_day TEXT NOT NULL, at TEXT NOT NULL, symbol TEXT NOT NULL,
  session TEXT NOT NULL, taken INTEGER NOT NULL, reason TEXT NOT NULL, setup_json TEXT, sizing_json TEXT,
  input_ages_json TEXT NOT NULL, UNIQUE (symbol, at));
CREATE TABLE positions (coid TEXT PRIMARY KEY, symbol TEXT NOT NULL, trading_day TEXT NOT NULL, opened_at TEXT NOT NULL,
  closed_at TEXT, position_json TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE measures (coid TEXT PRIMARY KEY, entry_json TEXT NOT NULL, after_json TEXT, updated_at TEXT NOT NULL);
CREATE TABLE events (id INTEGER PRIMARY KEY, trading_day TEXT NOT NULL, at TEXT NOT NULL, code TEXT NOT NULL,
  symbol TEXT, message TEXT NOT NULL, data_json TEXT);
"""


def guardian_plan(**over) -> dict:
    plan = {"symbol": "ABCD", "position_key": f"{DAY}:ABCD:TGB1", "trading_day": DAY, "entry": "2.00", "qty": 100,
            "r": "0.10", "initial_stop": "1.90", "stop": "1.90", "hwm": "2.05", "first_buy_at": iso(NOW - timedelta(minutes=20)),
            "flatten_at": iso(NOW + timedelta(hours=3)), "atr": "0.04", "breakeven_armed": False, "partial_done": False,
            "breach_since": None, "exit": None, "updated_at": iso(NOW)}
    plan.update(over)
    return plan


def make_guardian(root: Path, *, hb_age_s: float = 2, hb: dict | None = None) -> Path:
    g = root / "Trade_Guardian"
    (g / "state").mkdir(parents=True)
    (g / "conf").mkdir()
    (g / "conf" / "token.txt").write_text("SECRET-TOKEN")
    (g / ".env").write_text("WEBULL_APP_SECRET=nope")
    body = hb if hb is not None else {
        "format": 1, "at": iso(NOW - timedelta(seconds=hb_age_s)), "mode": "live", "managed": 1, "unprotected": 0,
        "unprotected_symbols": [], "hands_off": [], "owner_controlled": [], "lists_complete": True,
        "session": "CORE", "trading_day": DAY, "version": "1.1.1"}
    (g / "state" / "guardian.heartbeat").write_text(json.dumps(body))
    (g / "state" / "tg.lock").write_text("")
    (g / "config.example.toml").write_text(
        "max_auto_price = 20.0\nbreakeven_at_r = 1.0\ntrail_atr_mult = 2.0\ntrail_pct = 0.06\n"
        "flatten_regular_at = \"15:55\"\n")
    (g / "config.toml").write_text('mode = "live"\nmax_auto_price = 15.0\n')
    pk = f"{DAY}:ABCD:TGB1"
    write_db(g / "state" / "guardian.db", GUARDIAN_DDL, {
        "meta": [("account_id", SECRET)],
        "symbol_state": [(DAY, "ABCD", "MANAGED", "[]", 0, "", 0, None, None, 0, None, json.dumps(guardian_plan()), iso(NOW)),
                         (DAY, "ZZZ", "IGNORE", "[]", 0, "", 0, None, None, 0, None, None, iso(NOW))],
        "tg_orders": [("TGS1", "ABCD", "STOP", 100, 0, "DAY", "stop", DAY, pk, "WORKING", iso(NOW), iso(NOW),
                       "1.90", None, None, "OID", None, 0)],
        "events": [(1, iso(NOW), DAY, "WARNING", "quote_stale", "quote <b>old</b>", "ABCD", "{}")],
        "journal_trades": [(pk, "ABCD", DAY, iso(NOW - timedelta(minutes=20)), "CORE", "2.00", 100, "1.90", "0.10",
                            "0.04", None, None, None, 0, iso(NOW), iso(NOW))],
        "journal_stop_changes": [(1, pk, iso(NOW), None, "1.90", "initial")],
        "journal_exits": [(pk, "TGX1", iso(NOW), "40", "2.20", "partial", "2.21")],
    })
    return g


def make_radar(root: Path) -> Path:
    r = root / "Radar"
    (r / "reports").mkdir(parents=True)
    (r / "state" / "handoff").mkdir(parents=True)
    (r / "reports" / "screener-live.md").write_text(
        "# Radar live list\n\nUpdated now. Session CORE, status **running**.\n\n## Status\n\n- Budget: 10/min\n\n"
        "## Candidates today (1), newest first\n")
    write_db(r / "state" / "radar.db", RADAR_DDL, {
        "screener_candidates": [(1, DAY, "PRE", "ABCD", iso(NOW), "gainers", None, '{"vwap": "2.1"}', "2.31", "0.0043",
                                 "0.283", "6.2", "1234567", "1-5", '{"ok": true}', '["gap_and_hold"]', '["news_filing"]',
                                 1, 1, "abc", "tg 1.1.1", iso(NOW))],
        "screener_observations": [(1, iso(NOW), "2.31", "2.30", "2.31", "0.0043", "1234567", None)],
        "events": [(1, iso(NOW), "INFO", "start", None, "Radar started")],
        "raw_rankings": [
            (1, DAY, iso(NOW - timedelta(seconds=90)), "PRE_MARKET", json.dumps({"data": [{"symbol": "OLD"}]})),
            (2, DAY, iso(NOW - timedelta(seconds=30)), "PRE_MARKET", json.dumps({"data": [
                {"symbol": "WXYZ", "price": "5.10", "change_ratio": "0.62", "volume": "9000000", "relative_volume_10d": "14.2"},
                {"symbol": "abcd", "price": "2.31", "change_ratio": "0.283", "volume": "1234567"},
                {"symbol": "WXYZ", "price": "5.00"},
                {"symbol": "ZERO", "price": "0.000000", "change_ratio": "0.2"}]})),
            (3, DAY, iso(NOW - timedelta(minutes=10)), "RVOL_10D", json.dumps([{"symbol": "QQQQ", "price": "1.00"}])),
        ],
    })
    lines = [json.dumps({"format": 1, "candidate_id": 1, "trading_day": DAY, "session": "PRE", "symbol": "ABCD",
                         "found_at": iso(NOW), "price": "2.31", "ask": "2.31", "change_pct": "0.283", "tags": ["gap_and_hold"]})]
    (r / "state" / "handoff" / f"{DAY}.jsonl").write_text("\n".join(lines) + "\n{\"torn")
    return r


def make_scout(root: Path, *, hb_age_s: float = 5) -> Path:
    s = root / "Scout"
    st = s / "state"
    for d in ("facts", "macro", "jason", "answers"):
        (st / d).mkdir(parents=True)
    (s / "reports").mkdir()
    (s / ".env").write_text("ANTHROPIC_API_KEY=nope")
    hb = {"format": 1, "at": iso(NOW - timedelta(seconds=hb_age_s)), "version": "0.1.0", "stopped": False,
          "sources": {"prnewswire": {"enabled": True, "status": "ok", "last_ok": iso(NOW), "last_error": None, "gap_open": False},
                      "bls": {"enabled": True, "status": "stale", "last_ok": None, "last_error": "timeout", "gap_open": True}},
          "llm": {"enabled": False, "spent_today_usd": 0.0, "cap_reached": False},
          "levels": {"current_files": [], "expires": None}, "calendar_covers_next_45d": True}
    (st / "scout.heartbeat").write_text(json.dumps(hb))
    fact = {"format": 1, "fact_id": "f1", "kind": "wire", "ticker": "ABCD", "class": "offering", "source": "prnewswire",
            "title": "<script>alert(1)</script> ABCD prices offering", "url": "https://example.com/a",
            "first_seen_at": iso(NOW), "published_at": iso(NOW)}
    (st / "facts" / f"{DAY}.jsonl").write_text(json.dumps(fact) + "\nnot json\n")
    (st / "macro" / f"{DAY}.json").write_text(json.dumps({"format": 1, "at": iso(NOW), "inflation_regime": "cooling",
        "regime_status": "ok", "vix": {"date": DAY, "close": 17.2}, "events": [{"kind": "CPI", "event_at": iso(NOW),
        "sources": ["bls"], "sources_agree": True}], "forecast_lean": None}))
    (st / "jason" / "snapshots.jsonl").write_text(json.dumps({"format": 1, "first_seen_at": iso(NOW), "setups": [
        {"symbol": "ABCD", "direction": "long", "session_date": DAY, "expires": iso(NOW), "attribution": "Jason",
         "levels": [{"kind": "entry", "readings": ["2.40"]}]}]}) + "\n")
    (st / "answers" / f"{DAY}.jsonl").write_text(json.dumps({"symbol": "ABCD", "written_at": iso(NOW), "origin": "pilot",
        "fresh_catalyst": "true", "event_before_next_session": "false", "halt_open": "false", "stale": False}) + "\n")
    (s / "reports" / f"scout-{DAY}.md").write_text("# Morning brief\nAll quiet.\n")
    return s


def pilot_position(coid: str = "PB1", *, closed: bool = False, tags=()) -> dict:
    return {"coid": coid, "symbol": "ABCD", "trading_day": DAY, "session": "CORE", "setup_name": "pullback",
            "opened_at": iso(NOW - timedelta(minutes=5)), "entry": "2.00", "qty": 100, "stop": "1.92", "chart_stop": "1.92",
            "guardian_stop": "1.90", "target": "2.30", "risk_usd": "8.00", "fees_usd": "1.00", "tags": list(tags),
            "plan_path": "x", "guardian_plan": guardian_plan(), "remaining": 0 if closed else 100,
            "exits": [{"at": iso(NOW), "qty": 100, "price": "2.16", "reason": "target", "trigger": "2.16"}] if closed else [],
            "closed_at": iso(NOW) if closed else None, "carried": False, "high": None, "low": None}


def make_pilot(root: Path, *, report_age_s: float = 5, tags=(), plan: bool = True) -> Path:
    p = root / "Pilot"
    (p / "reports").mkdir(parents=True)
    (p / "state" / "plans").mkdir(parents=True)
    (p / "conf").mkdir()
    live = p / "reports" / "pilot-live.md"
    live.write_text("# Pilot live\n\nGate: NOT YET (1 of 30 closed)\n")
    t = (NOW - timedelta(seconds=report_age_s)).timestamp()
    os.utime(live, (t, t))
    if plan:
        (p / "state" / "plans" / "PB1.json").write_text(json.dumps({
            "version": 1, "client_order_id": "PB1", "symbol": "ABCD", "created_utc": iso(NOW), "mode": "paper",
            "setup": "pullback", "entry_limit": "2.00", "stop": "1.92", "targets": [{"price": "2.30", "fraction": "0.5"}],
            "sell_by": None, "carry": {"allowed": True}, "hold": {"allowed": False}}))
    write_db(p / "state" / "pilot.db", PILOT_DDL, {
        "meta": [("schema_version", "1")],
        "decisions": [(1, DAY, iso(NOW - timedelta(minutes=5)), "ABCD", "CORE", 1, "taken", "{}", "{}", "{}"),
                      (2, DAY, iso(NOW - timedelta(minutes=6)), "WXYZ", "CORE", 0, "spread_too_wide", None, None, "{}")],
        "positions": [("PB1", "ABCD", DAY, iso(NOW - timedelta(minutes=5)), None,
                       json.dumps(pilot_position("PB1", tags=tags)), iso(NOW)),
                      ("PB0", "ABCD", DAY, iso(NOW - timedelta(minutes=50)), iso(NOW),
                       json.dumps(pilot_position("PB0", closed=True)), iso(NOW))],
        "measures": [("PB0", '{"rvol": "3.1"}', None, iso(NOW))],
    })
    return p


@pytest.fixture
def bots(tmp_path) -> Config:
    root = tmp_path / "bots"
    root.mkdir()
    return Config(guardian_dir=make_guardian(root), radar_dir=make_radar(root), scout_dir=make_scout(root),
                  pilot_dir=make_pilot(root), port=8765)


@pytest.fixture
def empty(tmp_path) -> Config:
    return Config(guardian_dir=tmp_path / "g", radar_dir=tmp_path / "r", scout_dir=tmp_path / "s",
                  pilot_dir=tmp_path / "p")
