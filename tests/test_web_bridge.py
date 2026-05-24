from dataclasses import dataclass
from pathlib import Path

from steam_hour_booster.auth.community import AuthSession
from steam_hour_booster.config_store import ConfigStore
from steam_hour_booster.models import AccountProfile, AppConfig, IdleGame
from steam_hour_booster.session_store import SessionStore
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


@dataclass
class DummyPendingQR:
    qr_image_url: str
    challenge_url: str


class FakeAuthGateway:
    def __init__(self) -> None:
        self.pending = DummyPendingQR(
            qr_image_url="https://example.test/qr.png",
            challenge_url="https://example.test/challenge",
        )
        self._poll_count = 0

    def login_with_credentials(self, account_name: str, password: str, **kwargs):
        assert account_name == "primary_account"
        assert password == "password123"
        return AuthSession(
            steam_id="7656119",
            account_name=account_name,
            refresh_token="refresh",
            session_bundle={"steam_id": "7656119", "refresh_token": "refresh"},
            session_state={"logged_in": True},
        )

    def login_with_refresh_token(self, refresh_token: str):
        assert refresh_token == "refresh-token"
        return AuthSession(
            steam_id="7656120",
            account_name="token_account",
            refresh_token=refresh_token,
            session_bundle={"steam_id": "7656120", "refresh_token": refresh_token},
            session_state={"logged_in": True},
        )

    def begin_qr_login(self, device_friendly_name: str = "Steam Hour Booster Desktop"):
        assert device_friendly_name
        return self.pending

    def poll_qr_approval(self, pending):
        self._poll_count += 1
        if self._poll_count < 2:
            return None
        return AuthSession(
            steam_id="7656121",
            account_name="qr_account",
            refresh_token="qr-refresh",
            session_bundle={"steam_id": "7656121", "refresh_token": "qr-refresh"},
            session_state={"logged_in": True},
        )


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
    assert "Online" in state["persona_states"]


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


def test_credential_login_creates_account_and_bundle(tmp_path) -> None:
    store = ConfigStore(path=tmp_path / "config.json")
    session_store = SessionStore(base_dir=tmp_path / "sessions")
    api = DesktopApi(
        config_store=store,
        config=AppConfig(),
        auth_gateway=FakeAuthGateway(),
        session_store=session_store,
    )

    result = api.login_account_with_credentials(
        {
            "display_name": "Primary",
            "account_name": "primary_account",
            "password": "password123",
        }
    )

    assert result["ok"] is True
    assert result["account"]["steam_id"] == "7656119"
    assert Path(result["account"]["session_bundle_path"]).exists()
    assert store.load().accounts[0].display_name == "Primary"


def test_qr_flow_polls_then_creates_account(tmp_path) -> None:
    store = ConfigStore(path=tmp_path / "config.json")
    session_store = SessionStore(base_dir=tmp_path / "sessions")
    gateway = FakeAuthGateway()
    api = DesktopApi(
        config_store=store,
        config=AppConfig(),
        auth_gateway=gateway,
        session_store=session_store,
    )

    started = api.begin_qr_account_login({"display_name": "QR Account"})
    waiting = api.poll_qr_account_login(started["pending_id"])
    approved = api.poll_qr_account_login(started["pending_id"])

    assert started["ok"] is True
    assert waiting["status"] == "waiting"
    assert approved["ok"] is True
    assert approved["status"] == "approved"
    assert store.load().accounts[0].steam_id == "7656121"


def test_remove_account_deletes_bundle(tmp_path) -> None:
    store = ConfigStore(path=tmp_path / "config.json")
    session_store = SessionStore(base_dir=tmp_path / "sessions")
    bundle_path = session_store.save_bundle("steam_7656119", {"steam_id": "7656119"})
    config = AppConfig(
        accounts=[
            AccountProfile(
                profile_id="steam_7656119",
                display_name="Primary",
                steam_id="7656119",
                session_bundle_path=str(bundle_path),
            )
        ]
    )
    api = DesktopApi(
        config_store=store,
        config=config,
        auth_gateway=FakeAuthGateway(),
        session_store=session_store,
    )

    result = api.remove_account("steam_7656119")

    assert result["ok"] is True
    assert bundle_path.exists() is False
    assert store.load().accounts == []


def test_save_account_profile_updates_runtime_fields(tmp_path) -> None:
    store = ConfigStore(path=tmp_path / "config.json")
    session_store = SessionStore(base_dir=tmp_path / "sessions")
    bundle_path = session_store.save_bundle("steam_7656119", {"steam_id": "7656119", "refresh_token": "refresh"})
    config = AppConfig(
        accounts=[
            AccountProfile(
                profile_id="steam_7656119",
                display_name="Primary",
                account_name="primary_account",
                steam_id="7656119",
                login_mode="credentials",
                session_bundle_path=str(bundle_path),
                games=[IdleGame(app_id=730)],
            )
        ]
    )
    api = DesktopApi(
        config_store=store,
        config=config,
        auth_gateway=FakeAuthGateway(),
        session_store=session_store,
    )

    result = api.save_account_profile(
        {
            "profile_id": "steam_7656119",
            "display_name": "Primary Updated",
            "persona_state": "Invisible",
            "custom_status": "Boosting quietly",
            "games_text": "730: Counter-Strike 2\n570: Dota 2\n730",
            "notes": "Night queue",
        }
    )

    assert result["ok"] is True
    saved = store.load().accounts[0]
    assert saved.display_name == "Primary Updated"
    assert saved.persona_state == "Invisible"
    assert saved.custom_status == "Boosting quietly"
    assert saved.notes == "Night queue"
    assert len(saved.games) == 2
    assert saved.games[0].title == "Counter-Strike 2"


def test_save_account_profile_rejects_too_many_slots(tmp_path) -> None:
    store = ConfigStore(path=tmp_path / "config.json")
    session_store = SessionStore(base_dir=tmp_path / "sessions")
    config = AppConfig(
        accounts=[
            AccountProfile(
                profile_id="steam_7656119",
                display_name="Primary",
                steam_id="7656119",
            )
        ]
    )
    api = DesktopApi(
        config_store=store,
        config=config,
        auth_gateway=FakeAuthGateway(),
        session_store=session_store,
    )
    games_text = "\n".join(str(index) for index in range(1, 35))

    result = api.save_account_profile(
        {
            "profile_id": "steam_7656119",
            "display_name": "Primary",
            "persona_state": "Online",
            "games_text": games_text,
        }
    )

    assert result["ok"] is False
    assert "32 game slots" in result["message"]
