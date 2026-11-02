# Dashboard v1: design

One local web page that shows the live status of Guardian, Radar, Scout and Pilot, with a
click-through for each item. **It only reads.** This note is what the owner agreed on 2026-11-02
(all of section 7 at the defaults): what each panel shows, the exact file it comes from, and when
it counts as stale. Repo: https://github.com/JavanAppleby/Trade-Bot-Team-Dashboard.

## 1. Folders, repo and how it runs

| What | Where |
|---|---|
| Claude's working copy | `E:\Dashboard_dev` |
| The owner's install | `E:\Dashboard` (installed outside US hours, steps in the README) |
| GitHub repo | one the owner creates (suggested name `Trade-Dashboard`); Claude asks for the URL before the first push |
| Program | Python 3.13, **standard library only** (no packages to install, nothing that can pull in broker code) |
| Page | `http://127.0.0.1:8765/` (bound to 127.0.0.1 only, never 0.0.0.0, so no other device can reach it) |
| Launcher | `Start Dashboard.bat`: starts the server and opens the page in the default browser |
| Refresh | the page asks the server for fresh data every 5 s; the server reads the files at that moment (no cache older than 2 s) |
| Its own files | `E:\Dashboard\logs\` only (its own log). It keeps no database |

`config.toml` in the dashboard's folder holds the four bot folders (defaults: `E:\Trade_Guardian`,
`E:\Radar`, `E:\Scout`, `E:\Pilot`), the port and the stale limits below. `config.example.toml`
documents every key.

## 2. Money-safety rules (each one gets a test)

1. **Read only.** The program writes only inside its own folder. A test runs a full page load
   against copies of the bots' folders and checks every file's bytes and modified time are unchanged.
2. **SQLite read-only:** `sqlite3.connect("file:...?mode=ro", uri=True, timeout=1)` with
   `PRAGMA query_only`, one short query batch, then closed. Never a write, never left open between
   refreshes. A locked or busy database shows "busy, retrying" for that panel instead of waiting.
   While the bot has the database open (its `-wal` and `-shm` files exist), the dashboard is an
   ordinary WAL reader: it shares those files as every reader does and never blocks the bot's
   writes. When the bot has closed a WAL database cleanly (no `-wal`/`-shm`, so all data is in the
   main file), it opens with `mode=ro&immutable=1`, because a plain `mode=ro` open would create
   those two files in the bot's folder (found by the read-only test while building).
3. **Plain files** (heartbeats, `.jsonl`, `.md`, plan `.json`): opened, read and closed at once,
   capped in size (heartbeat 64 KiB, a report 1 MiB, a `.jsonl` read from the end, last 2 MiB).
4. **Never read:** any `.env`, any `conf\` folder, token files, Guardian's `meta` table (it holds
   `account_id`), Guardian's `config.toml` except the named keys in section 4.3. File paths are
   built from a fixed list in code; nothing on the page can ask for an arbitrary path.
5. **No network** except answering the browser on 127.0.0.1. No Webull, no logins, no imports of
   `trade_guardian`, `radar`, `scout`, `pilot`, `webull*`. A boundary test checks the imports.
6. **Never crashes:** each panel is read on its own; any error (missing file, bad JSON, a
   database changed shape) becomes that panel's "no data" or "error: ..." line, and the other
   panels still show.
7. Web text from Scout (headlines, Discord text) is shown as plain text, never as HTML or links
   that run anything (escaped; links open only `http(s)` URLs, in a new tab).

## 3. Status badges (same on every panel)

| Badge | Meaning |
|---|---|
| **LIVE** (green) | the bot's freshness source is within its limit |
| **STALE** (amber) | the source exists but is older than its limit; the age is shown ("last update 4 min ago") |
| **STOPPED** (grey) | the bot wrote its own "stopped" marker |
| **NO DATA** (grey) | the folder or file is missing (for example Pilot not installed yet) |
| **ERROR** (red) | the file exists but could not be read; the reason is shown |

Every value on the page carries the time it was read from, in ET and Brisbane.

## 4. Panels

### 4.1 Guardian: positions, stops and planned moves

Freshness: `E:\Trade_Guardian\state\guardian.heartbeat`, field `at`. **Stale after 30 s**
(Guardian's own rule; it beats every pass, in every session). `{"state":"starting"}` or
`{"state":"stopped"}` show as STARTING / STOPPED.

| Shown | Source |
|---|---|
| Mode (shadow/live), session, trading day, version, managed count, **unprotected count** (red when > 0, with `unprotected_symbols`), hands-off and owner-controlled lists, whether the lists are complete | `guardian.heartbeat` |
| STOP file present | `E:\Trade_Guardian\STOP` exists (checked, not read) |
| Running | `state\tg.lock` exists (checked, not read) |
| **Positions managed today**: symbol, classification, entry, qty, R, initial stop, **current stop**, high-water mark, ATR, breakeven armed, partial done, **flatten time**, an exit in progress (reason, qty, working order) | `state\guardian.db`, table `symbol_state` for today's `trading_day`, rows with a `plan` (the plan is JSON of Guardian's `PositionPlan`) |
| **Working orders** for each symbol: type, qty, stop/limit price, status | `tg_orders` where status is `INTENT`, `WORKING` or `UNKNOWN` |
| **Next ratchet** (dashboard estimate, labelled so): the price that next raises the stop and the stop it raises to: breakeven at entry + `breakeven_at_r` x R; after that the next R-step at entry + (k+1) x R (stop to entry + k x R); the ATR trail level HWM - `trail_atr_mult` x ATR | computed from the plan row and the keys in 4.3 |
| Click a position: its stop history (time, old, new, reason), exits (time, qty, price, reason, trigger), today's events for that symbol | `journal_stop_changes`, `journal_exits`, `journal_trades` (by `position_key`), `events` (by symbol and day) |
| Today's closed trades: symbol, entry, exits, realised P&L, R | `journal_trades` + `journal_exits` for today |
| Last 20 warnings and errors | `events`, level WARNING or above, newest first |
| Carried positions (overnight carry) | `carried`, `active = 1` |

### 4.2 Radar: what it is screening and has found

Freshness: `E:\Radar\reports\screener-live.md` modified time (Radar rewrites it every 60 s, also
when idle). **Stale after 3 min.** Running: `state\radar.lock` exists.

| Shown | Source |
|---|---|
| Status line: session, status (idle/running), budget, pause, stream state, deferred jobs | `reports\screener-live.md`, the "Updated" and "Status" sections, shown as written |
| **Today's finds**, newest first: symbol, found time, session, price, % change, rel volume, spread, tags, flags, feeds, last seen | `state\radar.db`, `screener_candidates` for today's `trading_day` |
| Click a find: all its facts and filter results, its price observations, its outcome when computed (best/worst move, sim result in % and R) | `screener_candidates.facts_json`/`filter_json`, `screener_observations`, `screener_outcomes` by `candidate_id` |
| **Entry setups, ranked by Pilot's checks** (owner request 2026-11-02; first on the tab): each stock Pilot checked in the last 10 minutes, ranked Setup now (every check passes; best reward:risk first), Close (1-2 entry conditions missing, named with their values), Not close, Unknown (quote, bars or session check failed, or not re-checked for 90 s). Shows leg age, % vs VWAP, % off the day's high, rel volume, reward:risk, entry, stop, target, Pilot's call and the check's age; a click shows every check and Pilot's sizing. The dashboard adds no score of its own; labelled unproven until Pilot's paper gate is met. Empty until Pilot runs | `E:\Pilot\state\pilot.db` `decisions`: the latest row per symbol on the newest `trading_day` (`setup_json` = Pilot's Setup with `checks` by name; `sizing_json` when the setup passed) |
| **Webull leaderboards** (owner request 2026-11-02): the newest list Radar read from each ranking feed (pre-market / day / after-hours gainers, fast movers, volume surges), in Webull's order, top 50: rank, symbol, price, % change, volume, rel volume, and "Radar find" when the symbol is one of today's finds (click opens it). Each list shows when it was read and turns stale after the Radar limit; prices are as read, not live | `raw_rankings`: per feed, the row with the highest `id` on the newest `trading_day`; `body_json` read as Radar's `parse_ranking_rows` does (bare list or `data`/`list`/`items`/`result`/`rows` wrapper; a repeated symbol keeps its first rank; a 0 price is blank) |
| Every table sorts by clicking a column heading (numbers by value, times by when, blanks last); the finds start sorted by % change, highest first | page only |
| What was handed to Pilot today | `state\handoff\<trading_day>.jsonl` (one line per find; "none yet" while the file is absent) |
| Last 20 events | `events`, newest first |

### 4.3 Guardian settings the dashboard reads (named keys only)

From `E:\Trade_Guardian\config.toml`, falling back to `config.example.toml` for a key not set:
`breakeven_at_r`, `trail_atr_mult`, `trail_pct`, `partial_at_r`, `partial_fraction`,
`flatten_enabled`, `flatten_regular_at`, `flatten_extended_at`, `max_auto_price`. Nothing else in
that file is kept or shown. If the files cannot be read, the "next ratchet" column shows "unknown"
(the stored stops and flatten time still show; they come from the database).

### 4.4 Scout: news and context

Freshness: `E:\Scout\state\scout.heartbeat`, field `at`. **Stale after 2 min** (Scout beats
every 30 s). `stopped: true` shows STOPPED.

| Shown | Source |
|---|---|
| Each source: enabled, status, last OK, last error, gap open | `scout.heartbeat` `sources` |
| LLM spend today and cap reached; Jason level files and when they expire; whether the macro calendar covers the next 45 days | `scout.heartbeat` `llm`, `levels`, `calendar_covers_next_45d` |
| **News feed**, newest first (today and yesterday): time first seen, ticker, kind (wire, filing, halt, Discord...), class, source, title, link | `state\facts\<date>.jsonl` |
| Filter by ticker, kind or source; click a fact for all its fields | same |
| **Macro**: inflation regime, VIX, the day's scheduled events with sources | `state\macro\<today>.json` |
| **Jason's levels**: each setup's symbol, direction, levels, expiry | `state\jason\snapshots.jsonl`, the last line |
| **Answers** Scout gave the other bots today (symbol, fresh catalyst, event before next session, halt open, stale) | `state\answers\<date>.jsonl` |
| Morning brief | newest `reports\scout-YYYY-MM-DD.md`, shown as text |

### 4.5 Pilot: list and each paper trade with its protection

Pilot is still being built (`E:\Pilot_dev`), so this panel is coded against its design
(`docs\DESIGN.md` sections 4, 5, 8, 8a) and shows NO DATA until `E:\Pilot` exists.

Freshness: `E:\Pilot\reports\pilot-live.md` modified time (rewritten every 60 s). **Stale after
3 min.** Running: `state\pilot.lock` exists. `PILOT_STOP` file present is shown.

| Shown | Source |
|---|---|
| Status, budget, Guardian heartbeat as Pilot sees it, **the gate** (closed paper trades of 30, net $, profit factor, breaches, NOT YET / MET) | `reports\pilot-live.md`, shown as written |
| **Watch list**: each symbol and the conditions it is failing | `reports\pilot-live.md` (and `pilot.db` finds) |
| **Paper trades** (open first, then today's closed): symbol, setup, opened, entry, qty, remaining, **effective stop**, Guardian-rule stop, chart stop, target, risk $, P&L $ and R, tags | `state\pilot.db` positions (Pilot's `PaperPosition`) |
| Click a trade: its **plan file** (entry limit, stop, targets, sell-by, carry/hold allowed, created), the decision that took it (every check and its value, input ages), each exit (time, qty, price, reason, trigger), its measures | `state\plans\<client_order_id>.json`; `pilot.db` decisions, exits, measures |
| **Protection status** per open trade, one line each: (1) plan file on disk and readable; (2) a stop is set (effective stop = higher of Guardian's rule stop and the plan stop); (3) Pilot is live (report fresh), so the paper stop is being watched; (4) Guardian's heartbeat fresh with `unprotected = 0`; (5) no "no usable quote for 60 s" flag. All five: **PROTECTED**; any one failing: **AT RISK**, naming which | plan file, `pilot.db`, `pilot-live.md`, `guardian.heartbeat` |
| Today's decisions (taken and skipped, with reason) | `pilot.db` decisions |

Paper trades are not managed by Guardian (Guardian protects only real positions), so in v1
"protected" means Pilot is applying Guardian's rules to the paper position. When Pilot goes
live, the same line will also check that Guardian's `symbol_state` has a plan for the symbol.

`pilot.db` is read as Pilot's `store.py` writes it (tables `positions`, `decisions`, `measures`;
each position is one JSON document of `PaperPosition`; `meta` is never read). A missing table or
column shows "database shape not recognised", never a crash. Pilot tags a position
`no_quote_60s` (no usable quote for 60 s) and `plan_unreadable`; both count as AT RISK.

## 5. Layout of the page

- A top strip with four tiles (Guardian, Radar, Scout, Pilot): badge, age of the newest data, and
  the one number that matters most (Guardian: managed / **unprotected**; Radar: finds today;
  Scout: sources OK of total; Pilot: open paper trades / gate). Red when Guardian has any
  unprotected position or its heartbeat is stale during market hours.
- Clicking a tile opens that bot's panel; clicking a row opens its detail. Times in ET with
  Brisbane beside them.
- No buttons that change anything. There is no "stop", "sell" or "edit" anywhere.

## 6. Tests (no network, no real bot folders)

Fixtures build small copies of each bot's files (heartbeats, databases with the real table
definitions, `.jsonl`, reports) in a temp folder. Tests cover: each panel with good, missing,
stale, corrupt and half-written files; a database locked by a writer; the read-only check (bytes
and mtimes unchanged, no file created in a bot folder, no `-journal` file); the server answers only
on 127.0.0.1; no forbidden import; never reading `meta`, `.env` or `conf\`; HTML escaping of web
text.

## 7. Questions for the owner (the build uses the answer in bold unless you say otherwise)

1. Port **8765**?
2. Page refresh every **5 s**?
3. Stale limits: Guardian **30 s**, Scout **2 min**, Radar **3 min**, Pilot **3 min**?
4. The "next ratchet" estimate reads the named keys of Guardian's `config.toml` (4.3). **Yes**, or
   leave the column out?
5. Repo: the owner created **Trade-Bot-Team-Dashboard**.
