"""Money-safety rules: read only, no secrets, no bot imports, localhost only, text never HTML."""

from __future__ import annotations

import ast
import os
import socket
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from conftest import SECRET
from dashboard import guardian, pilot, radar, safe, scout
from dashboard.server import Dashboard, make_handler

PKG = Path(__file__).resolve().parents[1] / "dashboard"


def snapshot(root: Path) -> dict:
    out = {}
    for dirpath, _, files in os.walk(root):
        for f in files:
            p = Path(dirpath) / f
            st = p.stat()
            out[str(p)] = (p.read_bytes(), st.st_mtime_ns)
    return out


def exercise(cfg) -> None:
    app = Dashboard(cfg)
    app.overview()
    app.detail("guardian", "2026-11-03|ABCD")
    app.detail("radar", "1")
    app.detail("scout", "2026-11-03|f1")
    app.detail("pilot", "PB1")
    app.detail("pilot", "PB0")


def test_reading_changes_nothing(bots):
    root = bots.guardian_dir.parent
    before = snapshot(root)
    exercise(bots)
    after = snapshot(root)
    assert after.keys() == before.keys(), f"files created or removed: {set(after) ^ set(before)}"
    assert after == before


def test_reading_a_busy_wal_db_changes_nothing(bots):
    """With a bot connection open (so -wal and -shm exist), reads leave the bot's files alone."""
    db = bots.pilot_dir / "state" / "pilot.db"
    writer = sqlite3.connect(db)
    writer.execute("INSERT INTO events (trading_day, at, code, message) VALUES ('d', 'a', 'c', 'm')")
    writer.commit()
    try:
        before = (db.read_bytes(), db.stat().st_mtime_ns)
        exercise(bots)
        assert (db.read_bytes(), db.stat().st_mtime_ns) == before
    finally:
        writer.close()


def test_locked_db_shows_busy_quickly(bots):
    db = bots.radar_dir / "state" / "radar.db"
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.execute("BEGIN EXCLUSIVE")
    try:
        start = time.monotonic()
        p = radar.build(bots, __import__("conftest").NOW)
        assert time.monotonic() - start < 6
        assert any("busy" in t["note"] or "locked" in t["note"] for t in p["tables"])
    finally:
        conn.rollback()
        conn.close()


def test_never_reads_meta_or_secrets(bots, monkeypatch):
    seen: list[str] = []
    real_connect = sqlite3.connect

    def traced(*a, **k):
        conn = real_connect(*a, **k)
        conn.set_trace_callback(seen.append)
        return conn

    monkeypatch.setattr(sqlite3, "connect", traced)
    opened: list[str] = []
    real_open = open

    def watched(file, *a, **k):
        opened.append(str(file))
        return real_open(file, *a, **k)

    monkeypatch.setattr("builtins.open", watched)
    exercise(bots)
    assert seen, "no SQL traced"
    assert not [s for s in seen if "meta" in s.lower()]
    for path in opened:
        low = path.lower()
        assert ".env" not in low and "\\conf\\" not in low and "/conf/" not in low and "token" not in low, path
    body = str(Dashboard(bots).overview())
    assert SECRET not in body


def test_check_allowed_refuses_secret_paths(tmp_path):
    for bad in (tmp_path / ".env", tmp_path / "conf" / "x.json", tmp_path / "conf", tmp_path / "webull_token.txt"):
        with pytest.raises(safe.ForbiddenPath):
            safe.read_bytes(bad, 10)
    assert safe.read_bytes(tmp_path / "missing.json", 10) is None


def test_open_db_never_creates_a_database(tmp_path):
    with pytest.raises(FileNotFoundError):
        with safe.open_db(tmp_path / "nope.db"):
            pass
    assert not (tmp_path / "nope.db").exists()


def test_db_connection_is_read_only(bots):
    with safe.open_db(bots.guardian_dir / "state" / "guardian.db") as conn:
        with pytest.raises(sqlite3.Error):
            conn.execute("DELETE FROM events")


FORBIDDEN = ("webull", "trade_guardian", "radar", "scout", "pilot", "requests", "urllib3", "httpx")


def test_imports_stdlib_and_own_package_only():
    import sys
    allowed_third = {"tzdata"}
    for path in PKG.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                names = [node.module or ""]
            for name in names:
                top = name.split(".")[0]
                if top == "dashboard":
                    continue
                assert top not in FORBIDDEN, f"{path.name} imports {name}"
                assert top in sys.stdlib_module_names or top in allowed_third, f"{path.name} imports {name}"


def test_page_never_uses_html_sinks():
    js = (PKG / "static" / "app.js").read_text(encoding="utf-8")
    for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"):
        assert sink not in js


@pytest.fixture
def server(bots):
    from http.server import ThreadingHTTPServer
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    srv = ThreadingHTTPServer(("127.0.0.1", port), make_handler(Dashboard(bots), port))
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv, port
    srv.shutdown()
    srv.server_close()


def get(port: int, path: str, host: str | None = None, method: str = "GET") -> tuple[int, bytes, dict]:
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method)
    if host:
        req.add_header("Host", host)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)


def test_server_routes(server):
    srv, port = server
    assert srv.server_address[0] == "127.0.0.1"
    status, body, headers = get(port, "/")
    assert status == 200 and b"Trade Dashboard" in body
    assert "script-src 'self'" in headers["Content-Security-Policy"]
    status, body, _ = get(port, "/api/overview")
    assert status == 200 and b'"guardian"' in body
    status, body, _ = get(port, "/api/detail?bot=pilot&key=PB1")
    assert status == 200 and b"PROTECTED" in body
    assert get(port, "/../config.toml")[0] == 404
    assert get(port, "/api/overview", method="POST")[0] == 405


def test_server_refuses_other_hosts(server):
    _, port = server
    assert get(port, "/api/overview", host=f"evil.example:{port}")[0] == 403
    assert get(port, "/", host=f"localhost:{port}")[0] == 200
