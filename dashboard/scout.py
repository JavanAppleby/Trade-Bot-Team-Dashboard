"""Scout panel: source health, the news it has gathered, macro, Jason's levels, answers, the brief.

Web and Discord text is shown as plain text only (the page never renders it as HTML).
"""

from __future__ import annotations

from datetime import datetime

from dashboard import safe, times
from dashboard import view as v
from dashboard.config import Config

NEWS_MAX = 300


def _paths(cfg: Config) -> dict:
    s = cfg.scout_dir
    st = s / "state"
    return {"hb": st / "scout.heartbeat", "facts": st / "facts", "macro": st / "macro",
            "jason": st / "jason" / "snapshots.jsonl", "answers": st / "answers", "reports": s / "reports",
            "lock": st / "scout.lock"}


def build(cfg: Config, now: datetime) -> dict:
    p = v.panel("scout", "Scout")
    paths = _paths(cfg)
    hb: dict = {}
    try:
        raw = safe.read_json(paths["hb"], safe.HEARTBEAT_CAP)
        if raw is None:
            p["badge"], p["reason"] = v.NO_DATA, "Scout's heartbeat file not found"
        elif not isinstance(raw, dict):
            p["badge"], p["reason"] = v.ERROR, "The heartbeat is not a JSON object"
        else:
            hb = raw
            v.freshness(p, times.parse(raw.get("at")), now, cfg.scout_stale_s, "Heartbeat")
            if raw.get("stopped") is True:
                p["badge"], p["reason"] = v.STOPPED, "Scout says it has stopped"
    except safe.ReadError as exc:
        p["badge"], p["reason"] = v.ERROR, str(exc)
    v.fact(p, "Running", safe.exists(paths["lock"]))
    if hb:
        _health(p, hb)
    v.guarded(p, "News", lambda: _news(p, paths))
    v.guarded(p, "Macro", lambda: _macro(p, paths))
    v.guarded(p, "Jason's levels", lambda: _jason(p, paths))
    v.guarded(p, "Answers", lambda: _answers(p, paths))
    v.guarded(p, "Morning brief", lambda: _brief(p, paths))
    p["alert"] = p["badge"] in (v.STALE, v.ERROR)
    return p


def _health(p: dict, hb: dict) -> None:
    sources = hb.get("sources") if isinstance(hb.get("sources"), dict) else {}
    enabled = {k: s for k, s in sources.items() if isinstance(s, dict) and s.get("enabled")}
    ok = sum(1 for s in enabled.values() if s.get("status") == "ok")
    p["headline"] = f"{ok} of {len(enabled)} sources OK"
    llm = hb.get("llm") if isinstance(hb.get("llm"), dict) else {}
    levels = hb.get("levels") if isinstance(hb.get("levels"), dict) else {}
    v.fact(p, "Version", hb.get("version"))
    v.fact(p, "Sources OK", f"{ok} of {len(enabled)} enabled", "good" if ok == len(enabled) else "warn")
    v.fact(p, "Claude (LLM)", f"{'On' if llm.get('enabled') else 'Off'}, ${float(llm.get('spent_today_usd') or 0):.2f} spent today"
           + (", cap reached" if llm.get("cap_reached") else ""), "warn" if llm.get("cap_reached") else "")
    v.fact(p, "Jason level files", levels.get("current_files") or "None")
    v.fact(p, "Levels expire", times.show(levels.get("expires")) or "No levels loaded")
    v.fact(p, "Macro calendar covers next 45 days", hb.get("calendar_covers_next_45d"),
           "" if hb.get("calendar_covers_next_45d") else "warn")
    rows = []
    for name, s in sorted(sources.items()):
        if not isinstance(s, dict):
            continue
        rows.append([v.nice(name), s.get("enabled"), v.nice(s.get("status")), times.show_short(s.get("last_ok")),
                     v.sentence(v.nice(s.get("last_error"))), s.get("gap_open")])
    p["tables"].append(v.table("Sources", ["Source", "Enabled", "Status", "Last OK", "Last error", "Gap open"], rows))


def _news(p: dict, paths: dict) -> None:
    names = safe.list_names(paths["facts"], suffix=".jsonl")[-2:]
    items: list[tuple[str, dict]] = []
    bad = 0
    for name in names:
        got = safe.tail_jsonl(paths["facts"] / name)
        if got:
            items += [(name.removesuffix(".jsonl"), f) for f in got[0]]
            bad += got[1]
    items.sort(key=lambda it: str(it[1].get("first_seen_at") or ""), reverse=True)
    items = items[:NEWS_MAX]
    p["tables"].append(v.table(
        "News and context, newest first",
        ["First seen", "Ticker", "Kind", "Class", "Source", "Title", "Link"],
        [[times.show_short(f.get("first_seen_at")), f.get("ticker"), v.nice(f.get("kind")), v.nice(f.get("class")),
          v.nice(f.get("source")), f.get("title"), f.get("url")] for _, f in items],
        [f"{day}|{f.get('fact_id')}" for day, f in items],
        note=(f"{bad} unreadable line(s) skipped. " if bad else "") + ("" if items else "No news yet.")))


def _macro(p: dict, paths: dict) -> None:
    names = [n for n in safe.list_names(paths["macro"], suffix=".json")]
    if not names:
        raise FileNotFoundError(paths["macro"])
    data = safe.read_json(paths["macro"] / names[-1])
    if not isinstance(data, dict):
        raise safe.ReadError("macro file is not a JSON object")
    vix = data.get("vix") if isinstance(data.get("vix"), dict) else {}
    rows = [["Day", times.show_date(names[-1].removesuffix(".json"))], ["Written", times.show(data.get("at"))],
            ["Inflation regime", f"{v.nice(data.get('inflation_regime'))} ({v.nice(data.get('regime_status'))})"],
            ["VIX", f"{vix.get('close', '')} on {vix.get('date', '')}" if vix else "unknown"],
            ["Forecast lean (informational, untested)", v.nice(data.get("forecast_lean")) or "None"]]
    p["tables"].append(v.table("Macro", ["", ""], rows))
    events = [e for e in data.get("events") or [] if isinstance(e, dict)]
    p["tables"].append(v.table("Scheduled macro events", ["When", "Kind", "Prior", "Street", "Sources", "Sources agree"],
                               [[times.show(e.get("event_at")), v.nice(e.get("kind")), e.get("prior"), e.get("street"),
                                 v.nice(e.get("sources")), e.get("sources_agree")] for e in events],
                               note="" if events else "None listed."))
    _results(p, data.get("results"))


def _month(value: object) -> str:
    text = str(value or "")
    try:
        return datetime.strptime(text[:7], "%Y-%m").strftime("%b %Y")
    except ValueError:
        return ""


def _k(value: object) -> str:
    n = v.dec(value)
    return "" if n is None else f"{n:+,.0f}k"


def _rate(value: object) -> str:
    n = v.dec(value)
    return "" if n is None else f"{n}%"


def _results(p: dict, res: object) -> None:
    """Scout's BLS results (jobs, unemployment, CPI): record only, never used in an answer."""
    title, cols = "Jobs and inflation results", ["Result", "Month", "Value", "Before", "Expected", "Vs expected"]
    note = "From the BLS, as Scout first saw them. Expected values are Nowflation's. Record only."
    if not isinstance(res, dict) or not res:
        p["tables"].append(v.table(title, [], [], note="No results yet (Scout reads the BLS each morning)."))
        return
    rows = []
    pay = res.get("payrolls") if isinstance(res.get("payrolls"), dict) else None
    if pay:
        rows.append(["Jobs added" + (" (first estimate)" if pay.get("preliminary") else ""), _month(pay.get("month")),
                     _k(pay.get("change_k")), _k(pay.get("prior_change_k")), _k(pay.get("street_k")),
                     _k(pay.get("vs_street_k"))])
    if isinstance(res.get("jobs_trend"), str):
        rows.append(["Jobs trend (3 months)", "", v.nice(res.get("jobs_trend")),
                     f"average {_k(res.get('jobs_trend_avg_k'))} a month" if res.get("jobs_trend_avg_k") is not None
                     else "", "", ""])
    un = res.get("unemployment") if isinstance(res.get("unemployment"), dict) else None
    if un:
        rows.append(["Unemployment rate", _month(un.get("month")), _rate(un.get("rate")), _rate(un.get("prior")), "", ""])
    cpi = res.get("cpi") if isinstance(res.get("cpi"), dict) else None
    if cpi:
        exp = cpi.get("nowflation_forecast") if cpi.get("nowflation_forecast") is not None else cpi.get("street")
        off = cpi.get("vs_nowflation") if cpi.get("vs_nowflation") is not None else cpi.get("vs_street")
        rows.append(["CPI, a year", _month(cpi.get("month")), _rate(cpi.get("yoy_pct")), _rate(cpi.get("prior_yoy_pct")),
                     _rate(exp), f"{off:+g} pts" if isinstance(off, (int, float)) and not isinstance(off, bool) else ""])
    p["tables"].append(v.table(title, cols, rows, note=note))


def _jason(p: dict, paths: dict) -> None:
    got = safe.tail_jsonl(paths["jason"], 512 * 1024)
    if not got or not got[0]:
        p["tables"].append(v.table("Jason's levels", [], [], note="No levels yet."))
        return
    snap = got[0][-1]
    rows = []
    for s in snap.get("setups") or []:
        if not isinstance(s, dict):
            continue
        levels = "; ".join(f"{v.nice(lv.get('kind'))}: {', '.join(lv.get('readings') or [])}"
                           for lv in s.get("levels") or [] if isinstance(lv, dict))
        rows.append([s.get("symbol"), v.nice(s.get("direction")), times.show_date(s.get("session_date")), times.show_short(s.get("expires")),
                     levels, s.get("attribution")])
    p["tables"].append(v.table(f"Jason's levels · read {times.show_short(snap.get('first_seen_at'))}",
                               ["Symbol", "Direction", "Session", "Expires", "Levels", "From"], rows,
                               note="" if rows else "No setups in the latest snapshot."))


def _answers(p: dict, paths: dict) -> None:
    names = safe.list_names(paths["answers"], suffix=".jsonl")
    if not names:
        p["tables"].append(v.table("Answers to the other bots", [], [], note="No answers yet."))
        return
    got = safe.tail_jsonl(paths["answers"] / names[-1]) or ([], 0)
    rows = list(reversed(got[0]))[:100]
    p["tables"].append(v.table(
        f"Answers to the other bots · {times.show_date(names[-1].removesuffix('.jsonl'))}",
        ["Written", "Symbol", "For", "Fresh catalyst", "Event before next session", "Macro next session",
         "Halt open", "Stale"],
        [[times.show_short(a.get("written_at")), a.get("symbol"), v.nice(a.get("origin")), v.nice(a.get("fresh_catalyst")),
          v.nice(a.get("event_before_next_session")), v.nice(a.get("macro_event_next_session")),
          v.nice(a.get("halt_open")), a.get("stale")] for a in rows]))


def _brief(p: dict, paths: dict) -> None:
    names = safe.list_names(paths["reports"], prefix="scout-", suffix=".md")
    if not names:
        v.text(p, "Morning brief", "No brief yet.")
        return
    v.text(p, f"Morning brief · {times.show_date(names[-1][6:-3])}", safe.read_text(paths["reports"] / names[-1]) or "")


def detail(cfg: Config, key: str) -> dict:
    day, _, fact_id = key.partition("|")
    paths = _paths(cfg)
    if "/" in day or "\\" in day or ".." in day:
        raise ValueError("bad key")
    got = safe.tail_jsonl(paths["facts"] / f"{day}.jsonl") or ([], 0)
    match = next((f for f in got[0] if f.get("fact_id") == fact_id), None)
    d = v.detail("Scout news item")
    if match is None:
        d["facts"].append(["Item", "Not found", "warn"])
        return d
    d["title"] = f"Scout · {match.get('title') or v.nice(match.get('kind'))}"
    skip = {"format", "fact_id", "raw_sha256", "guid"}
    for k, val in match.items():
        if k in skip or val in (None, "", [], {}):
            continue
        if k.endswith("_at") or k.endswith("_time"):
            shown = times.show(val)
        elif k in ("kind", "class", "source", "feed", "event_kind", "event_source"):
            shown = v.cell(v.nice(val))
        else:
            shown = v.cell(val)
        d["facts"].append([v.nice(k), shown, ""])
    return d
