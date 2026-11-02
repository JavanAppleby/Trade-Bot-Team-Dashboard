"""The local web server: 127.0.0.1 (plus the home-network address when lan_address is set), GET only,
a fixed set of routes.

/                      the page (static/index.html, app.js, style.css)
/api/overview          every panel, read fresh
/api/detail?bot=&key=  one row's details
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import socket
import threading
import time
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from dashboard import __version__, guardian, pilot, radar, scout, times
from dashboard import view as v
from dashboard.config import Config, ConfigError, load_config

log = logging.getLogger("dashboard")

# In the order work flows: Scout gathers context, Radar finds, Pilot buys (paper), Guardian protects and sells.
BOTS = {"scout": scout, "radar": radar, "pilot": pilot, "guardian": guardian}
TITLES = {"scout": "Scout", "radar": "Radar", "pilot": "Pilot (paper)", "guardian": "Guardian"}
STATIC = {"/": ("index.html", "text/html; charset=utf-8"),
          "/app.js": ("app.js", "text/javascript; charset=utf-8"),
          "/style.css": ("style.css", "text/css; charset=utf-8")}
CACHE_S = 2.0
HEADERS = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer",
           "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; "
                                      "img-src 'self'; connect-src 'self'; frame-ancestors 'none'"}


class Dashboard:
    """Builds the overview; one build at a time, reused for CACHE_S so many tabs cost one read."""

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self._lock = threading.Lock()
        self._cached: tuple[float, dict] | None = None

    def overview(self) -> dict:
        with self._lock:
            if self._cached and time.monotonic() - self._cached[0] < CACHE_S:
                return self._cached[1]
            now = datetime.now(timezone.utc)
            panels = [v.safe_build(name, TITLES[name], lambda m=mod: m.build(self.cfg, now))
                      for name, mod in BOTS.items()]
            body = {"now": times.show(now), "version": __version__, "refresh_s": self.cfg.refresh_s,
                    "panels": panels}
            self._cached = (time.monotonic(), body)
            return body

    def detail(self, bot: str, key: str) -> dict:
        mod = BOTS.get(bot)
        if mod is None or not key or len(key) > 200:
            return {"title": "Not found", "facts": [["Error", "unknown item", "bad"]], "tables": [], "texts": []}
        try:
            return mod.detail(self.cfg, key)
        except FileNotFoundError:
            return {"title": "No data", "facts": [["Error", "the bot's file was not found", "warn"]],
                    "tables": [], "texts": []}
        except Exception as exc:  # noqa: BLE001 - never crash the page
            return {"title": "Error", "facts": [["Error", f"{type(exc).__name__}: {exc}", "bad"]],
                    "tables": [], "texts": []}


def make_handler(app: Dashboard, port: int, lan_address: str = "") -> type[BaseHTTPRequestHandler]:
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"} | ({f"{lan_address}:{port}"} if lan_address else set())

    class Handler(BaseHTTPRequestHandler):
        server_version = "TradeDashboard"
        sys_version = ""

        def log_message(self, fmt: str, *args: object) -> None:
            log.debug("%s %s", self.address_string(), fmt % args)

        def _send(self, status: int, body: bytes, ctype: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            for k, val in HEADERS.items():
                self.send_header(k, val)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, data: object) -> None:
            self._send(200, json.dumps(data).encode("utf-8"), "application/json; charset=utf-8")

        def do_GET(self) -> None:  # noqa: N802
            if self.headers.get("Host", "") not in allowed_hosts:  # DNS-rebinding guard
                self._send(403, b"forbidden", "text/plain")
                return
            url = urlsplit(self.path)
            if url.path in STATIC:
                name, ctype = STATIC[url.path]
                body = resources.files("dashboard").joinpath("static", name).read_bytes()
                self._send(200, body, ctype)
            elif url.path == "/api/overview":
                self._json(app.overview())
            elif url.path == "/api/detail":
                q = parse_qs(url.query)
                self._json(app.detail(q.get("bot", [""])[0], q.get("key", [""])[0]))
            else:
                self._send(404, b"not found", "text/plain")

        do_HEAD = do_GET

        def _refuse(self) -> None:
            self._send(405, b"read-only", "text/plain")

        do_POST = do_PUT = do_DELETE = do_PATCH = _refuse

    return Handler


class LocalServer(ThreadingHTTPServer):
    """Refuses a port another program already listens on. Python's default (SO_REUSEADDR) lets a
    second copy listen on the same port on Windows, so a second start would not fail."""

    allow_reuse_address = os.name != "nt"
    daemon_threads = True

    def server_bind(self) -> None:
        if os.name == "nt":
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def serve(cfg: Config, app: Dashboard | None = None, host: str = "127.0.0.1") -> ThreadingHTTPServer:
    app = app or Dashboard(cfg)
    return LocalServer((host, cfg.port), make_handler(app, cfg.port, cfg.lan_address))


def serve_all(cfg: Config) -> list[ThreadingHTTPServer]:
    """This PC's server, plus the home-network one when lan_address is set. A home-network address the
    PC no longer has (the router gave it a new one) is logged and skipped; this PC's server still runs."""
    app = Dashboard(cfg)
    servers = [serve(cfg, app)]
    if cfg.lan_address:
        try:
            servers.append(serve(cfg, app, cfg.lan_address))
        except OSError as exc:
            log.warning("cannot listen on %s:%s for phones (is that still this PC's address? see the README): %s",
                        cfg.lan_address, cfg.port, exc)
    return servers


def _setup_logging(root: Path) -> None:
    logs = root / "logs"
    logs.mkdir(exist_ok=True)
    handler = logging.FileHandler(logs / "dashboard.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler, logging.StreamHandler()])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="dashboard", description="Read-only dashboard for the trading bots.")
    ap.add_argument("--config", default="config.toml", help="the dashboard's own config.toml")
    ap.add_argument("--no-browser", action="store_true", help="do not open the page")
    ap.add_argument("--version", action="version", version=f"Trade Dashboard {__version__}")
    args = ap.parse_args(argv)
    config_path = Path(args.config).resolve()
    _setup_logging(config_path.parent)
    try:
        cfg = load_config(config_path)
    except ConfigError as exc:
        log.error("%s", exc)
        return 2
    try:
        servers = serve_all(cfg)
    except OSError as exc:
        log.error("cannot use port %s (is the dashboard already running?): %s", cfg.port, exc)
        return 2
    url = f"http://127.0.0.1:{cfg.port}/"
    log.info("Trade Dashboard %s at %s (read-only). Close this window to stop it.", __version__, url)
    if len(servers) > 1:
        log.info("On your phone (home Wi-Fi): http://%s:%s/", cfg.lan_address, cfg.port)
    if cfg.open_browser and not args.no_browser:
        threading.Timer(0.5, webbrowser.open, (url,)).start()
    for extra in servers[1:]:
        threading.Thread(target=extra.serve_forever, daemon=True).start()
    try:
        servers[0].serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        for server in servers:
            server.server_close()
    return 0
