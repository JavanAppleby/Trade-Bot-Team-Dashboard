# Trade Dashboard: repository rules

A local, read-only web page showing Guardian, Radar, Scout and Pilot. Design: `docs/DESIGN.md`.

- **Read only.** The dashboard never writes in any bot's folder, never sends a Webull request,
  never logs in and never trades. It writes only its own `logs\`.
- Every bot file is read through `dashboard/safe.py`: opened, read and closed at once, capped in size.
  SQLite is opened read-only (`mode=ro`, or `mode=ro&immutable=1` when the bot has closed a WAL
  database, so no `-wal`/`-shm` file is created), one query batch, then closed.
- Never read any `.env`, any `conf\` folder, token files, the account number, or the `meta` table of
  any bot's database (Guardian's holds `account_id`). Guardian's `config.toml` is read for the named
  keys in `guardian.RATCHET_KEYS` only.
- Standard library only (plus `tzdata`). Never import `webull*`, `trade_guardian`, `radar`, `scout`
  or `pilot`; `tests/test_safety.py` checks it.
- The server binds 127.0.0.1 only, answers GET only, and checks the Host header.
- Bot and web text reaches the page as text only (`textContent`); `app.js` uses no HTML sinks.
- A panel never crashes the page: missing, stale or corrupt sources show NO DATA, STALE or ERROR.
- Tests never touch the real bot folders or the network: fixtures build copies in a temp folder.
- Stored times are UTC; the page shows ET with Brisbane beside it.
- New panels or sources need the owner's explicit request.

Run the tests from the repo root:

    .venv\Scripts\python.exe -m pytest -q

(Dev setup: `py -3.13 -m venv .venv`, then `.venv\Scripts\python.exe -m pip install -e ".[dev]"`.)
