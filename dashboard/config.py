"""Settings from the dashboard's own config.toml (every key optional; defaults below)."""

from __future__ import annotations

import ipaddress
import tomllib
from dataclasses import dataclass, fields
from pathlib import Path


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Config:
    guardian_dir: Path = Path(r"E:\Trade_Guardian")
    radar_dir: Path = Path(r"E:\Radar")
    scout_dir: Path = Path(r"E:\Scout")
    pilot_dir: Path = Path(r"E:\Pilot")
    port: int = 8765
    refresh_s: int = 5
    guardian_stale_s: int = 30
    radar_stale_s: int = 180
    scout_stale_s: int = 120
    pilot_stale_s: int = 180
    open_browser: bool = True
    lan_address: str = ""


_PATHS = {"guardian_dir", "radar_dir", "scout_dir", "pilot_dir"}
# Home networks (RFC 1918) and Tailscale (100.64.0.0/10); nothing reachable from the open internet.
_ALLOWED = tuple(ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
                                                   "100.64.0.0/10"))


def _lan_address(raw: object) -> str:
    """The PC's own address on the home network (or Tailscale); never the open internet."""
    if not isinstance(raw, str):
        raise ConfigError("lan_address must be text, like '192.168.20.7', or '' for this PC only")
    if not raw.strip():
        return ""
    try:
        ip = ipaddress.IPv4Address(raw.strip())
    except ValueError as exc:
        raise ConfigError(f"lan_address {raw!r} is not an address like 192.168.20.7") from exc
    if not any(ip in net for net in _ALLOWED):
        raise ConfigError(f"lan_address {raw!r} must be this PC's home-network address (192.168..., 10..., "
                          "172.16-31...) or its Tailscale address (100.64-127...)")
    return str(ip)


def config_from_dict(data: dict) -> Config:
    known = {f.name for f in fields(Config)}
    unknown = sorted(set(data) - known)
    if unknown:
        raise ConfigError(f"unknown setting(s) in config.toml: {', '.join(unknown)}")
    values: dict = {}
    for key, raw in data.items():
        if key in _PATHS:
            if not isinstance(raw, str) or not raw.strip():
                raise ConfigError(f"{key} must be a folder path")
            values[key] = Path(raw)
        elif key == "open_browser":
            if not isinstance(raw, bool):
                raise ConfigError("open_browser must be true or false")
            values[key] = raw
        elif key == "lan_address":
            values[key] = _lan_address(raw)
        else:
            if not isinstance(raw, int) or isinstance(raw, bool) or raw <= 0:
                raise ConfigError(f"{key} must be a whole number above 0")
            values[key] = raw
    cfg = Config(**values)
    if not 1024 <= cfg.port <= 65535:
        raise ConfigError("port must be between 1024 and 65535")
    return cfg


def load_config(path: Path) -> Config:
    """The dashboard's config.toml; a missing file means every default."""
    if not path.exists():
        return Config()
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc
    return config_from_dict(data)
