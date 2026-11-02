from pathlib import Path

import pytest

from dashboard.config import Config, ConfigError, config_from_dict, load_config

ROOT = Path(__file__).resolve().parents[1]


def test_defaults_when_missing(tmp_path):
    assert load_config(tmp_path / "config.toml") == Config()


def test_example_documents_every_key():
    text = (ROOT / "config.example.toml").read_text(encoding="utf-8")
    for name in Config.__dataclass_fields__:
        assert f"\n{name} = " in text or text.startswith(f"{name} = ") or f"# {name} = " in text, name


def test_example_loads(tmp_path):
    import tomllib
    data = tomllib.loads((ROOT / "config.example.toml").read_text(encoding="utf-8"))
    assert config_from_dict(data) == Config()


@pytest.mark.parametrize("data", [{"nope": 1}, {"port": 80}, {"port": "x"}, {"radar_dir": ""},
                                  {"open_browser": 1}, {"refresh_s": 0}])
def test_refused(data):
    with pytest.raises(ConfigError):
        config_from_dict(data)


@pytest.mark.parametrize("raw, want", [("192.168.20.7", "192.168.20.7"), (" 10.0.0.5 ", "10.0.0.5"),
                                       ("172.20.1.2", "172.20.1.2"), ("100.101.102.103", "100.101.102.103"),
                                       ("", "")])
def test_lan_address_accepted(raw, want):
    assert config_from_dict({"lan_address": raw}).lan_address == want


@pytest.mark.parametrize("raw", ["0.0.0.0", "127.0.0.1", "8.8.8.8", "203.0.113.9", "my-pc", "::1", 5])
def test_lan_address_refuses_everything_but_home_or_tailscale(raw):
    with pytest.raises(ConfigError):
        config_from_dict({"lan_address": raw})
