from steam_hour_booster.config_store import ConfigStore
from steam_hour_booster.models import AccountProfile, AppConfig, IdleGame
from steam_hour_booster.web.bridge import DesktopApi


class EventHook:
    def __init__(self) -> None:
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self


class DummyWindowEvents:
    def __init__(self) -> None:
        self.maximized = EventHook()
        self.restored = EventHook()
        self.resized = EventHook()
        self.moved = EventHook()
        self.closing = EventHook()


class DummyWindow:
    def __init__(self) -> None:
        self.events = DummyWindowEvents()
        self.width = 1500
        self.height = 920
        self.x = 40
        self.y = 60
        self.minimized = False
        self.destroyed = False
        self.maximize_calls = 0
        self.restore_calls = 0

    def minimize(self) -> None:
        self.minimized = True

    def maximize(self) -> None:
        self.maximize_calls += 1

    def restore(self) -> None:
        self.restore_calls += 1

    def destroy(self) -> None:
        self.destroyed = True


def test_bootstrap_state_includes_counts_and_paths(tmp_path) -> None:
    store = ConfigStore(path=tmp_path / "config.json")
    config = AppConfig(
        accounts=[
            AccountProfile(
                profile_id="primary",
                display_name="Primary",
                login_mode="qr",
                games=[IdleGame(app_id=730), IdleGame(app_id=570)],
            )
        ]
    )
    api = DesktopApi(config_store=store, config=config)

    state = api.get_bootstrap_state()

    assert state["counts"]["accounts"] == 1
    assert state["counts"]["configured_slots"] == 2
    assert state["build"]["desktop_stack"] == "pywebview + HTML/CSS/JS"


def test_window_actions_call_host_methods(tmp_path) -> None:
    store = ConfigStore(path=tmp_path / "config.json")
    api = DesktopApi(config_store=store, config=AppConfig())
    window = DummyWindow()
    api.attach_window(window)

    api.minimize_window()
    maximized = api.toggle_maximize_window()
    restored = api.toggle_maximize_window()
    api.close_window()

    assert window.minimized is True
    assert maximized["maximized"] is True
    assert restored["maximized"] is False
    assert window.maximize_calls == 1
    assert window.restore_calls == 1
    assert window.destroyed is True
