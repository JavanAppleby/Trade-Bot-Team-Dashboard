# Trade Dashboard

One page on this PC that shows the live status of the four bots (Guardian, Radar, Scout and Pilot),
with a click-through for each item. It **only reads** their files: it never trades, never sends a
Webull request, never logs in, and never reads `.env`, `conf\`, token files or the account number.

The page is at **http://127.0.0.1:8765/**. Only this PC can open it, unless you turn on phone
access (below).

## What you see

- **Four tiles at the top**, one for each bot. Each tile has a badge:
  - **LIVE**: the bot's data is up to date.
  - **STALE**: the data is older than the stale limit. The tile shows how old it is.
  - **STOPPED** or **STARTING**: the bot says so itself.
  - **NO DATA**: the folder or file isn't there (for example, Pilot not installed yet).
  - **ERROR**: a file is there but couldn't be read. The reason is shown.

  A tile turns red when something needs you: Guardian has an unprotected position, a bot's data is
  stale, Guardian's STOP file is present, or a Pilot paper trade is AT RISK.
- **Click a tile** to open that bot's panel. **Click a row** for its details.
  - **Guardian:** each position with its current stop, flatten time and next ratchet.
  - **Radar:** each find's facts and outcome.
  - **Scout:** each news item.
  - **Pilot:** each paper trade's plan file and protection checks.
- Tables with more than five rows have a filter box.
- Times are shown in ET, with Brisbane beside them.

The panels, the files they read and the stale limits are listed in `docs/DESIGN.md`.

## Install (outside US market hours)

Do this once, on a weekend or before 04:00 ET (19:00 Brisbane from November to March, 18:00 the rest of the year). The dashboard doesn't touch the
bots, but doing it outside US hours means nothing distracts you while a bot is trading.

1. Open PowerShell and run these lines one at a time:

   ```
   cd E:\
   git clone https://github.com/JavanAppleby/Trade-Bot-Team-Dashboard.git Dashboard
   cd E:\Dashboard
   py -3.13 -m venv .venv
   .venv\Scripts\python.exe -m pip install -e ".[dev]"
   .venv\Scripts\python.exe -m pytest -q
   ```

   The last line should end with `passed` and no `failed`. (Run each line on its own: Windows
   PowerShell 5.1 does not accept `&&` between commands.)

2. Optional: to change a folder, the port or a stale limit, copy `config.example.toml` to
   `config.toml` and edit it. Without a `config.toml` the defaults are used: `E:\Trade_Guardian`,
   `E:\Radar`, `E:\Scout`, `E:\Pilot`, port 8765.

3. Double-click **`Start Dashboard.bat`**. A window opens, then the page opens in your browser.
   Keep the window open while you use the page. Close the window to stop the dashboard.

## Open it on your phone (home Wi-Fi)

Off until you turn it on. Anyone on your home Wi-Fi could then open the page (there is no password),
so skip this if guests use the same Wi-Fi network.

1. If there is no `config.toml` in `E:\Dashboard`, copy `config.example.toml` to `config.toml`.
2. In `config.toml`, change the `lan_address` line to this PC's home-network address:

   ```
   lan_address = '192.168.20.7'
   ```

3. Close the dashboard's window and double-click `Start Dashboard.bat`. If Windows asks whether to
   allow Python on networks, tick **Private networks** only and click **Allow**.
4. On your phone, connected to home Wi-Fi, open **http://192.168.20.7:8765/**.

It works only on home Wi-Fi, not over mobile data. If the phone link stops working, the router may
have given the PC a new address: the dashboard's window says "cannot listen on 192.168.20.7".
Run `ipconfig` in PowerShell, find the "IPv4 Address" under your Wi-Fi or Ethernet adapter, and
put that in `lan_address`. (Reserving the address in your router's settings stops it changing.)

## Update

Close the dashboard's window, then double-click **`Update Dashboard.bat`**.

## Troubleshooting

- **"cannot use port 8765"**: the dashboard is probably already running in another window. Open
  http://127.0.0.1:8765/ in your browser.
- **A panel shows "busy, retrying"**: the bot was writing at that moment. The next refresh, 5 s
  later, reads it again. The dashboard never makes a bot wait.
- **"dashboard program not answering"** at the top of the page: the dashboard window was closed.
  Double-click `Start Dashboard.bat` again.
- The dashboard's own log is `logs\dashboard.log` in its folder.
