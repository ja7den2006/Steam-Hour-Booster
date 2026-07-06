import json

from steam_hour_booster.config_store import ConfigStore
from steam_hour_booster.models import AppConfig


def test_config_store_returns_defaults_when_missing(tmp_path) -> None:
    store = ConfigStore(path=tmp_path / "config.json")
    config = store.load()

    assert isinstance(config, AppConfig)
    assert config.last_page == "overview"
    assert config.accounts == []


def test_config_store_persists_payload(tmp_path) -> None:
    path = tmp_path / "config.json"
    store = ConfigStore(path=path)
    config = AppConfig(window_width=1500, window_height=910, last_page="settings")

    store.save(config)

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["window_width"] == 1500
    restored = store.load()
    assert restored.last_page == "settings"
