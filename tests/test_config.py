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
