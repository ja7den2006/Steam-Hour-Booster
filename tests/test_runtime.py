import base64
import json
import time

from steam_hour_booster.models import AccountProfile, IdleGame
from steam_hour_booster.runtime import (
    PreviewBoosterRuntime,
    RefreshTokenSteamClient,
    RuntimeController,
    RuntimeState,
    SteamNetworkBoosterRuntime,
)
from steam_hour_booster.session_store import SessionStore
from steam.enums import EResult


def build_preview_controller(session_store: SessionStore) -> RuntimeController:
    return RuntimeController(
        session_store=session_store,
        transport=PreviewBoosterRuntime(),
    )


def build_client_refresh_token(steam_id: str) -> str:
    header = base64.urlsafe_b64encode(json.dumps({"alg": "none"}).encode("utf-8")).decode("ascii").rstrip("=")
    payload = base64.urlsafe_b64encode(
        json.dumps({"iss": "steam", "aud": ["client"], "sub": steam_id}).encode("utf-8")
    ).decode("ascii").rstrip("=")
    return "%s.%s.signature" % (header, payload)


def test_runtime_controller_marks_ready_accounts_from_saved_sessions(tmp_path) -> None:
    session_store = SessionStore(base_dir=tmp_path / "sessions")
    bundle_path = session_store.save_bundle("steam_7656119", {"steam_id": "7656119", "refresh_token": "refresh"})
    controller = build_preview_controller(session_store)
    account = AccountProfile(
        profile_id="steam_7656119",
        display_name="Primary",
        steam_id="7656119",
        session_bundle_path=str(bundle_path),
        games=[IdleGame(app_id=730), IdleGame(app_id=570)],
    )

    snapshot = controller.refresh_accounts([account]).to_dict()
    status = snapshot["statuses"][0]

    assert snapshot["counts"]["ready_accounts"] == 1
    assert status["state"] == "ready"
    assert status["session_ready"] is True
    assert status["configured_slot_count"] == 2


def test_runtime_controller_starts_and_stops_preview_lane(tmp_path) -> None:
    session_store = SessionStore(base_dir=tmp_path / "sessions")
    bundle_path = session_store.save_bundle("steam_7656119", {"steam_id": "7656119", "refresh_token": "refresh"})
    controller = build_preview_controller(session_store)
    account = AccountProfile(
        profile_id="steam_7656119",
        display_name="Primary",
        steam_id="7656119",
        session_bundle_path=str(bundle_path),
        games=[IdleGame(app_id=730), IdleGame(app_id=570)],
    )

    started = controller.start_profile("steam_7656119", [account])
    assert started.state == RuntimeState.BOOSTING
    assert started.active_app_ids == [730, 570]

    stopped = controller.stop_profile("steam_7656119", [account])

    assert stopped.state == RuntimeState.READY
    assert stopped.active_app_ids == []
    assert "Ready to start" in stopped.message


def test_runtime_controller_flags_missing_sessions_and_empty_slots(tmp_path) -> None:
    session_store = SessionStore(base_dir=tmp_path / "sessions")
    controller = build_preview_controller(session_store)
    missing_session = AccountProfile(
        profile_id="missing",
        display_name="Missing Session",
        steam_id="7656119",
        session_bundle_path=str(tmp_path / "sessions" / "missing.json"),
        games=[IdleGame(app_id=730)],
    )
    empty_bundle = session_store.save_bundle("empty", {"steam_id": "7656120", "refresh_token": "refresh"})
    empty_slots = AccountProfile(
        profile_id="empty",
        display_name="Empty Slots",
        steam_id="7656120",
        session_bundle_path=str(empty_bundle),
    )

    snapshot = controller.refresh_accounts([missing_session, empty_slots]).to_dict()
    states = {item["profile_id"]: item["state"] for item in snapshot["statuses"]}

    assert states["missing"] == "error"
    assert states["empty"] == "idle"
    assert snapshot["counts"]["error_accounts"] >= 1


def test_refresh_token_client_login_builds_client_logon_message() -> None:
    steam_id = "76561197960287930"

    class RecordingClient(RefreshTokenSteamClient):
        def __init__(self):
            self.chat_mode = 2
            self.connection = type("Connection", (), {"local_address": "127.0.0.1"})()
            self.sent = None

        def _pre_login(self):
            return EResult.OK

        def get_sentry(self, username):
            return None

        def send(self, message):
            self.sent = message

        def wait_msg(self, emsg, timeout=30):
            assert emsg is not None
            body = type("Body", (), {"eresult": EResult.OK})()
            return type("Response", (), {"body": body})()

        def sleep(self, seconds):
            return None

    client = RecordingClient()
    result = client.login_with_refresh_token(
        build_client_refresh_token(steam_id),
        steam_id,
        account_name="primary_account",
        login_id=77,
    )

    assert result == EResult.OK
    assert client.sent.body.access_token
    assert client.sent.body.account_name == "primary_account"
    assert client.sent.body.should_remember_password is True
    assert client.sent.body.obfuscated_private_ip.v4 == 77
    assert str(client.sent.header.steamid) == steam_id


def test_live_runtime_uses_refresh_token_transport_without_network(tmp_path) -> None:
    steam_id = "76561197960287930"
    created_clients = []

    class FakeLiveClient:
        def __init__(self):
            self.connected = False
            self.logged_on = False
            self.credential_location = None
            self.persona_calls = []
            self.played_calls = []
            created_clients.append(self)

        def set_credential_location(self, path):
            self.credential_location = path

        def login_with_refresh_token(self, refresh_token, steam_id, account_name=""):
            self.connected = True
            self.logged_on = True
            self.refresh_token = refresh_token
            self.steam_id = steam_id
            self.account_name = account_name
            return EResult.OK

        def change_status(self, **kwargs):
            self.persona_calls.append(kwargs)

        def games_played(self, app_ids):
            self.played_calls.append(list(app_ids))

        def sleep(self, seconds):
            time.sleep(0.01)

        def logout(self):
            self.logged_on = False
            self.connected = False

        def disconnect(self):
            self.connected = False

    session_store = SessionStore(base_dir=tmp_path / "sessions")
    refresh_token = build_client_refresh_token(steam_id)
    bundle_path = session_store.save_bundle(
        "steam_7656119",
        {"steam_id": steam_id, "refresh_token": refresh_token},
    )
    account = AccountProfile(
        profile_id="steam_7656119",
        display_name="Primary",
        account_name="primary_account",
        steam_id=steam_id,
        session_bundle_path=str(bundle_path),
        persona_state="Invisible",
        games=[IdleGame(app_id=730), IdleGame(app_id=570)],
    )
    runtime = SteamNetworkBoosterRuntime(
        session_store=session_store,
        client_factory=FakeLiveClient,
        start_timeout=2.0,
        sleep_interval=0.01,
    )

    start_result = runtime.start(
        account,
        session_store.load_bundle_path(str(bundle_path)),
    )
    stop_message = runtime.stop("steam_7656119")

    assert start_result.active_app_ids == [730, 570]
    assert "active" in start_result.message.lower()
    assert stop_message
    assert len(created_clients) == 1
    assert created_clients[0].account_name == "primary_account"
    assert created_clients[0].played_calls[-1] == [730, 570]
