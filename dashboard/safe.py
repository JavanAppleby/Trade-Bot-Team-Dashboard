"""The only code that touches the bots' files. Every read opens, reads and closes at once.

- Plain files are read in one call, capped in size; nothing is ever written.
- SQLite is opened read-only (`mode=ro`, `query_only`), used for one query batch and closed.
- A path naming `.env`, a `conf` folder or a token file is refused before it is opened.
"""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

HEARTBEAT_CAP = 64 * 1024
REPORT_CAP = 1024 * 1024
JSON_CAP = 1024 * 1024
TAIL_CAP = 2 * 1024 * 1024
DB_TIMEOUT_S = 1.0


class ReadError(Exception):
    """A file exists but could not be read; the message is shown on the page."""


class ForbiddenPath(ReadError):
    pass


def check_allowed(path: Path) -> None:
    parts = [p.lower() for p in Path(path).parts]
    name = parts[-1] if parts else ""
    if "conf" in parts[:-1] or name == "conf" or name.startswith(".env") or "token" in name:
        raise ForbiddenPath(f"refused to read {path}")


def mtime(path: Path) -> float | None:
    """Modified time (epoch seconds), None when missing. Does not open the file."""
    check_allowed(path)
    try:
        return os.stat(path).st_mtime
    except OSError:
        return None


def exists(path: Path) -> bool:
    check_allowed(path)
    return os.path.exists(path)


def list_names(folder: Path, prefix: str = "", suffix: str = "") -> list[str]:
    """File names in a folder (sorted), without opening any; [] when the folder is missing."""
    check_allowed(folder)
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    return sorted(n for n in names if n.startswith(prefix) and n.endswith(suffix)
                  and not n.lower().startswith(".env") and "token" not in n.lower())


def read_bytes(path: Path, cap: int) -> bytes | None:
    """The whole file, or None when it is missing. Larger than `cap`: ReadError."""
    check_allowed(path)
    try:
        with open(path, "rb") as fh:
            data = fh.read(cap + 1)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ReadError(f"cannot read {Path(path).name}: {exc.strerror or exc}") from exc
    if len(data) > cap:
        raise ReadError(f"{Path(path).name} is larger than {cap // 1024} KiB")
    return data


def read_text(path: Path, cap: int = REPORT_CAP) -> str | None:
    data = read_bytes(path, cap)
    return None if data is None else data.decode("utf-8", errors="replace")


def read_json(path: Path, cap: int = JSON_CAP) -> object | None:
    data = read_bytes(path, cap)
    if data is None:
        return None
    try:
        return json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReadError(f"{Path(path).name} is not valid JSON (it may be mid-write)") from exc


def tail_jsonl(path: Path, cap: int = TAIL_CAP) -> tuple[list[dict], int] | None:
    """The complete JSON-object lines in the last `cap` bytes, oldest first, and how many lines
    were skipped as unreadable. None when the file is missing. A torn last line is left out."""
    check_allowed(path)
    try:
        with open(path, "rb") as fh:
            size = fh.seek(0, os.SEEK_END)
            start = max(0, size - cap)
            fh.seek(start)
            data = fh.read(size - start)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ReadError(f"cannot read {Path(path).name}: {exc.strerror or exc}") from exc
    lines = data.split(b"\n")
    if start > 0:
        lines = lines[1:]  # the first line was cut by the cap
    if lines and lines[-1].strip():
        lines = lines[:-1]  # no newline yet: still being written
    rows: list[dict] = []
    bad = 0
    for raw in lines:
        if not raw.strip():
            continue
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            bad += 1
            continue
        if isinstance(value, dict):
            rows.append(value)
        else:
            bad += 1
    return rows, bad


def _header(path: Path) -> bytes:
    """The SQLite file header (bytes 18-19 are 2 and 2 in WAL mode)."""
    try:
        with open(path, "rb") as fh:
            return fh.read(100)
    except OSError as exc:
        raise ReadError(f"cannot read {Path(path).name}: {exc.strerror or exc}") from exc


@contextmanager
def open_db(path: Path) -> Iterator[sqlite3.Connection]:
    """A read-only connection, closed on exit. A missing database is never created.

    While the bot has the database open (its `-wal` and `-shm` files exist) this is a plain
    `mode=ro` reader: it shares the bot's files the way every SQLite reader does and never blocks
    the bot's writes. With no `-wal`/`-shm` (the bot closed it cleanly, so everything is in the main
    file) it is opened `immutable=1`, because a `mode=ro` open would create those two files in the
    bot's folder."""
    check_allowed(path)
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    header = _header(path)
    wal_mode = len(header) >= 20 and header[18] == 2 and header[19] == 2
    in_use = os.path.exists(f"{path}-wal") and os.path.exists(f"{path}-shm")
    immutable = wal_mode and not in_use
    uri = Path(path).resolve().as_uri() + ("?mode=ro&immutable=1" if immutable else "?mode=ro")
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=DB_TIMEOUT_S, isolation_level=None,
                               check_same_thread=True)
    except sqlite3.Error as exc:
        raise ReadError(f"cannot open {Path(path).name}: {exc}") from exc
    try:
        conn.execute("PRAGMA query_only = 1")
        conn.row_factory = sqlite3.Row
        yield conn
    except sqlite3.OperationalError as exc:
        text = str(exc)
        if "locked" in text or "busy" in text:
            raise ReadError(f"{Path(path).name} is busy, retrying") from exc
        raise ReadError(f"{Path(path).name}: {text}") from exc
    except sqlite3.Error as exc:
        raise ReadError(f"{Path(path).name}: {exc}") from exc
    finally:
        conn.close()


def table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')}


def require(conn: sqlite3.Connection, table: str, columns: set[str]) -> None:
    """ReadError naming what is missing when a bot's table changed shape."""
    have = table_columns(conn, table)
    if not have:
        raise ReadError(f"table {table} not found (database shape not recognised)")
    missing = sorted(columns - have)
    if missing:
        raise ReadError(f"table {table} lacks {', '.join(missing)} (database shape not recognised)")
