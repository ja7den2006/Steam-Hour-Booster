import base64
import json
from dataclasses import dataclass
from pathlib import Path

from steam_hour_booster.auth.community import AuthSession
from steam_hour_booster.config_store import ConfigStore
from steam_hour_booster.models import AccountProfile, AppConfig, IdleGame
from steam_hour_booster.runtime import PreviewBoosterRuntime, RuntimeController
from steam_hour_booster.session_store import SessionStore
from steam_hour_booster.web.bridge import DesktopApi


def build_client_refresh_token(steam_id: str) -> str:
    header = base64.urlsafe_b64encode(json.dumps({"alg": "none"}).encode("utf-8")).decode("ascii").rstrip("=")
    payload = base64.urlsafe_b64encode(
        json.dumps({"iss": "steam", "aud": ["client"], "sub": steam_id}).encode("utf-8")
    ).decode("ascii").rstrip("=")
    return "%s.%s.signature" % (header, payload)


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


class RecordingPathOpener:
    def __init__(self) -> None:
        self.paths = []

    def __call__(self, path: Path) -> None:
        self.paths.append(Path(path))


def preview_runtime_controller(session_store: SessionStore) -> RuntimeController:
    return RuntimeController(
        session_store=session_store,
        transport=PreviewBoosterRuntime(),
    )


def test_bootstrap_state_includes_counts_and_paths(tmp_path) -> None:
    store = ConfigStore(path=tmp_path / "config.json")
    session_store = SessionStore(base_dir=tmp_path / "sessions")
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
    api = DesktopApi(config_store=store, config=config, session_store=session_store)

    state = api.get_bootstrap_state()

    assert state["counts"]["accounts"] == 1
    assert state["counts"]["configured_slots"] == 2
    assert state["build"]["desktop_stack"] == "pywebview + HTML/CSS/JS"
    assert "Online" in state["persona_states"]
    assert any(item["value"] == "kick" for item in state["conflict_policies"])
    assert any(item["app_id"] == 730 for item in state["popular_games"])
    assert state["paths"]["config"] == str(store.path)
    assert state["paths"]["sessions"] == str(session_store.base_dir)
    assert state["runtime"]["event_log_path"].endswith("runtime.log")
    assert state["paths"]["logs"] == str(Path(state["runtime"]["event_log_path"]).parent)
    assert Path(state["runtime"]["event_log_path"]).exists()


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
            "boost_enabled": False,
            "appear_online": False,
            "persona_state": "Invisible",
            "conflict_policy": "kick",
            "custom_status": "Boosting quietly",
            "games_text": "730: Counter-Strike 2\n570: Dota 2\n730",
            "notes": "Night queue",
        }
    )

    assert result["ok"] is True
    saved = store.load().accounts[0]
    assert saved.display_name == "Primary Updated"
    assert saved.boost_enabled is False
    assert saved.appear_online is False
    assert saved.persona_state == "Invisible"
    assert saved.conflict_policy == "kick"
    assert saved.custom_status == "Boosting quietly"
    assert saved.notes == "Night queue"
    assert len(saved.games) == 2
    assert saved.games[0].title == "Counter-Strike 2"
    assert result["account"]["effective_persona_state"] == "Invisible"


def test_save_account_profile_reconfigures_active_preview_lane(tmp_path) -> None:
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
        runtime_controller=preview_runtime_controller(session_store),
    )

    started = api.start_account_runtime("steam_7656119")
    saved = api.save_account_profile(
        {
            "profile_id": "steam_7656119",
            "display_name": "Primary",
            "persona_state": "Away",
            "games_text": "730: Counter-Strike 2\n570: Dota 2",
        }
    )

    assert started["ok"] is True
    assert saved["ok"] is True
    assert "updated" in saved["message"].lower()
    runtime_status = saved["state"]["runtime"]["statuses"][0]
    assert runtime_status["state"] == "boosting"
    assert runtime_status["active_app_ids"] == [730, 570]


def test_save_account_profile_disables_active_preview_lane(tmp_path) -> None:
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
        runtime_controller=preview_runtime_controller(session_store),
    )

    started = api.start_account_runtime("steam_7656119")
    saved = api.save_account_profile(
        {
            "profile_id": "steam_7656119",
            "display_name": "Primary",
            "boost_enabled": False,
            "appear_online": False,
            "persona_state": "Online",
            "games_text": "730: Counter-Strike 2",
        }
    )

    assert started["ok"] is True
    assert saved["ok"] is True
    assert "disabled" in saved["message"].lower()
    runtime_status = saved["state"]["runtime"]["statuses"][0]
    assert runtime_status["state"] == "idle"
    assert runtime_status["boost_enabled"] is False
    assert runtime_status["can_start"] is False


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


def test_runtime_controls_start_and_stop_lanes(tmp_path) -> None:
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
                games=[IdleGame(app_id=730), IdleGame(app_id=570)],
            )
        ]
    )
    api = DesktopApi(
        config_store=store,
        config=config,
        auth_gateway=FakeAuthGateway(),
        session_store=session_store,
        runtime_controller=preview_runtime_controller(session_store),
    )

    started = api.start_account_runtime("steam_7656119")
    stopped = api.stop_account_runtime("steam_7656119")

    assert started["ok"] is True
    assert started["state"]["runtime"]["counts"]["boosting_accounts"] == 1
    assert stopped["ok"] is True
    assert stopped["state"]["runtime"]["counts"]["boosting_accounts"] == 0
    runtime_status = stopped["state"]["runtime"]["statuses"][0]
    assert runtime_status["state"] == "ready"


def test_open_path_actions_and_snapshot_export(tmp_path) -> None:
    store = ConfigStore(path=tmp_path / "config.json")
    session_store = SessionStore(base_dir=tmp_path / "sessions")
    bundle_path = session_store.save_bundle(
        "steam_7656119",
        {"steam_id": "7656119", "refresh_token": build_client_refresh_token("7656119")},
    )
    opener = RecordingPathOpener()
    config = AppConfig(
        accounts=[
            AccountProfile(
                profile_id="steam_7656119",
                display_name="Primary",
                steam_id="7656119",
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
        path_opener=opener,
    )

    open_config = api.open_config_file()
    open_sessions = api.open_sessions_directory()
    open_logs = api.open_logs_directory()
    open_runtime_log = api.open_runtime_log_file()
    open_bundle = api.open_account_session_bundle("steam_7656119")
    export_snapshot = api.export_runtime_snapshot()

    assert open_config["ok"] is True
    assert Path(open_config["path"]).exists()
    assert open_sessions["ok"] is True
    assert Path(open_sessions["path"]).is_dir()
    assert open_logs["ok"] is True
    assert Path(open_logs["path"]).is_dir()
    assert open_runtime_log["ok"] is True
    assert Path(open_runtime_log["path"]).exists()
    assert open_bundle["ok"] is True
    assert Path(open_bundle["path"]) == bundle_path
    assert export_snapshot["ok"] is True
    assert Path(export_snapshot["path"]).exists()
    assert len(opener.paths) == 5
    assert opener.paths[0] == store.path
    exported_payload = json.loads(Path(export_snapshot["path"]).read_text(encoding="utf-8"))
    assert exported_payload["runtime"]["transport_name"] == "valvepython-steam"
    assert exported_payload["accounts"][0]["profile_id"] == "steam_7656119"


def test_runtime_refresh_exposes_transport_status(tmp_path) -> None:
    store = ConfigStore(path=tmp_path / "config.json")
    session_store = SessionStore(base_dir=tmp_path / "sessions")
    bundle_path = session_store.save_bundle(
        "steam_7656119",
        {"steam_id": "7656119", "refresh_token": build_client_refresh_token("7656119")},
    )
    config = AppConfig(
        accounts=[
            AccountProfile(
                profile_id="steam_7656119",
                display_name="Primary",
                steam_id="7656119",
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

    result = api.refresh_runtime_state()

    assert result["ok"] is True
    runtime_state = result["state"]["runtime"]
    assert runtime_state["transport_name"] == "valvepython-steam"
    assert runtime_state["preview_mode"] is False
    assert runtime_state["counts"]["ready_accounts"] == 1


def test_runtime_poll_returns_current_state(tmp_path) -> None:
    store = ConfigStore(path=tmp_path / "config.json")
    session_store = SessionStore(base_dir=tmp_path / "sessions")
    bundle_path = session_store.save_bundle(
        "steam_7656119",
        {"steam_id": "7656119", "refresh_token": build_client_refresh_token("7656119")},
    )
    config = AppConfig(
        accounts=[
            AccountProfile(
                profile_id="steam_7656119",
                display_name="Primary",
                steam_id="7656119",
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

    result = api.poll_runtime_state()

    assert result["ok"] is True
    assert result["status"] == "polled"
    assert result["state"]["runtime"]["counts"]["ready_accounts"] == 1
