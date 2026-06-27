import base64
import json
import time

from steam_hour_booster.library import OwnedGamesValidationResult
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
from steam.enums.emsg import EMsg


class FakeOwnedGamesValidator:
    def __init__(self, results=None):
        self.results = results or {}
        self.calls = []

    def validate_account(self, account: AccountProfile) -> OwnedGamesValidationResult:
        self.calls.append(account.profile_id)
        configured_app_ids = [int(game.app_id) for game in account.games if game.enabled]
        return self.results.get(
            account.profile_id,
            OwnedGamesValidationResult(
                state="valid",
                message="All configured app IDs were found in the owned-games API for this account.",
                checked_at="2026-06-27T00:00:00",
                api_key_available=True,
                validated_app_ids=configured_app_ids,
                missing_app_ids=[],
            ),
        )


def build_preview_controller(
    session_store: SessionStore,
    *,
    owned_games_validator=None,
) -> RuntimeController:
    return RuntimeController(
        session_store=session_store,
        transport=PreviewBoosterRuntime(),
        owned_games_validator=owned_games_validator,
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


def test_runtime_controller_persists_event_log_to_disk(tmp_path) -> None:
    session_store = SessionStore(base_dir=tmp_path / "sessions")
    bundle_path = session_store.save_bundle("steam_7656119", {"steam_id": "7656119", "refresh_token": "refresh"})
    log_path = tmp_path / "logs" / "runtime.log"
    controller = RuntimeController(
        session_store=session_store,
        transport=PreviewBoosterRuntime(),
        event_log_path=log_path,
    )
    account = AccountProfile(
        profile_id="steam_7656119",
        display_name="Primary",
        steam_id="7656119",
        session_bundle_path=str(bundle_path),
        games=[IdleGame(app_id=730), IdleGame(app_id=570)],
    )

    controller.refresh_accounts([account], reason="manual refresh")
    controller.start_profile("steam_7656119", [account])
    snapshot = controller.snapshot().to_dict()
    log_contents = log_path.read_text(encoding="utf-8")

    assert log_path.exists() is True
    assert snapshot["event_log_path"] == str(log_path)
    assert snapshot["event_count"] >= 3
    assert "Runtime controller initialized" in log_contents
    assert "Started Primary with 2 slots." in log_contents


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


def test_runtime_controller_keeps_disabled_accounts_idle(tmp_path) -> None:
    session_store = SessionStore(base_dir=tmp_path / "sessions")
    bundle_path = session_store.save_bundle("steam_7656119", {"steam_id": "7656119", "refresh_token": "refresh"})
    controller = build_preview_controller(session_store)
    account = AccountProfile(
        profile_id="steam_7656119",
        display_name="Primary",
        steam_id="7656119",
        boost_enabled=False,
        appear_online=False,
        session_bundle_path=str(bundle_path),
        games=[IdleGame(app_id=730), IdleGame(app_id=570)],
    )

    snapshot = controller.refresh_accounts([account]).to_dict()
    status = snapshot["statuses"][0]
    started = controller.start_profile("steam_7656119", [account])

    assert status["state"] == "idle"
    assert status["boost_enabled"] is False
    assert status["effective_persona_state"] == "Invisible"
    assert status["can_start"] is False
    assert snapshot["counts"]["disabled_accounts"] == 1
    assert started.state == RuntimeState.IDLE
    assert "disabled" in started.message.lower()


def test_preview_runtime_exposes_auto_reply_configuration(tmp_path) -> None:
    session_store = SessionStore(base_dir=tmp_path / "sessions")
    bundle_path = session_store.save_bundle("steam_7656119", {"steam_id": "7656119", "refresh_token": "refresh"})
    controller = build_preview_controller(session_store)
    account = AccountProfile(
        profile_id="steam_7656119",
        display_name="Primary",
        steam_id="7656119",
        session_bundle_path=str(bundle_path),
        auto_reply_enabled=True,
        auto_reply_message="I am hour boosting right now.",
        auto_reply_cooldown_seconds=240,
        auto_reply_timeout_seconds=1800,
        games=[IdleGame(app_id=730), IdleGame(app_id=570)],
    )

    controller.start_profile("steam_7656119", [account])
    snapshot = controller.snapshot().to_dict()
    status = snapshot["statuses"][0]

    assert snapshot["counts"]["auto_reply_enabled_accounts"] == 1
    assert status["auto_reply_enabled"] is True
    assert status["auto_reply_message"] == "I am hour boosting right now."
    assert status["auto_reply_cooldown_seconds"] == 240
    assert status["auto_reply_timeout_seconds"] == 1800


def test_runtime_controller_blocks_invalid_owned_game_configuration(tmp_path) -> None:
    session_store = SessionStore(base_dir=tmp_path / "sessions")
    bundle_path = session_store.save_bundle("steam_7656119", {"steam_id": "7656119", "refresh_token": "refresh"})
    validator = FakeOwnedGamesValidator(
        {
            "steam_7656119": OwnedGamesValidationResult(
                state="invalid",
                message="Configured app IDs are not present in the owned-games API for this account: 570.",
                checked_at="2026-06-27T00:00:00",
                api_key_available=True,
                validated_app_ids=[730, 570],
                missing_app_ids=[570],
                matched_titles={730: "Counter-Strike 2"},
            )
        }
    )
    controller = build_preview_controller(session_store, owned_games_validator=validator)
    account = AccountProfile(
        profile_id="steam_7656119",
        display_name="Primary",
        steam_id="7656119",
        session_bundle_path=str(bundle_path),
        games=[IdleGame(app_id=730), IdleGame(app_id=570)],
    )

    snapshot = controller.refresh_accounts([account]).to_dict()
    status = snapshot["statuses"][0]
    started = controller.start_profile("steam_7656119", [account])

    assert snapshot["counts"]["library_validation_blocked_accounts"] == 1
    assert status["state"] == "error"
    assert status["owned_games_validation_state"] == "invalid"
    assert status["owned_games_missing_app_ids"] == [570]
    assert status["can_start"] is False
    assert started.state == RuntimeState.ERROR
    assert "blocked" in started.message.lower()


def test_runtime_controller_allows_start_when_validation_is_unavailable(tmp_path) -> None:
    session_store = SessionStore(base_dir=tmp_path / "sessions")
    bundle_path = session_store.save_bundle("steam_7656119", {"steam_id": "7656119", "refresh_token": "refresh"})
    validator = FakeOwnedGamesValidator(
        {
            "steam_7656119": OwnedGamesValidationResult(
                state="unavailable",
                message="No Steam Web API key is registered for this account, so owned-game validation could not run.",
                checked_at="2026-06-27T00:00:00",
                api_key_available=False,
                validated_app_ids=[730],
            )
        }
    )
    controller = build_preview_controller(session_store, owned_games_validator=validator)
    account = AccountProfile(
        profile_id="steam_7656119",
        display_name="Primary",
        steam_id="7656119",
        session_bundle_path=str(bundle_path),
        games=[IdleGame(app_id=730)],
    )

    snapshot = controller.refresh_accounts([account]).to_dict()
    status = snapshot["statuses"][0]
    started = controller.start_profile("steam_7656119", [account])

    assert snapshot["counts"]["library_validation_unavailable_accounts"] == 1
    assert status["state"] == "ready"
    assert status["owned_games_validation_state"] == "unavailable"
    assert status["can_start"] is True
    assert "unavailable" in status["message"].lower()
    assert started.state == RuntimeState.BOOSTING


def test_runtime_controller_stops_active_lane_when_validation_turns_invalid(tmp_path) -> None:
    session_store = SessionStore(base_dir=tmp_path / "sessions")
    bundle_path = session_store.save_bundle("steam_7656119", {"steam_id": "7656119", "refresh_token": "refresh"})
    validator = FakeOwnedGamesValidator()
    controller = build_preview_controller(session_store, owned_games_validator=validator)
    account = AccountProfile(
        profile_id="steam_7656119",
        display_name="Primary",
        steam_id="7656119",
        session_bundle_path=str(bundle_path),
        games=[IdleGame(app_id=730), IdleGame(app_id=570)],
    )

    started = controller.start_profile("steam_7656119", [account])
    assert started.state == RuntimeState.BOOSTING

    validator.results["steam_7656119"] = OwnedGamesValidationResult(
        state="invalid",
        message="Configured app IDs are not present in the owned-games API for this account: 570.",
        checked_at="2026-06-27T00:05:00",
        api_key_available=True,
        validated_app_ids=[730, 570],
        missing_app_ids=[570],
        matched_titles={730: "Counter-Strike 2"},
    )
    snapshot = controller.refresh_accounts([account]).to_dict()
    status = snapshot["statuses"][0]

    assert status["state"] == "error"
    assert status["active_app_ids"] == []
    assert "stopped" in status["message"].lower()
    assert snapshot["counts"]["boosting_accounts"] == 0


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


def test_live_runtime_reconfigures_active_lane_without_restart(tmp_path) -> None:
    steam_id = "76561197960287930"
    created_clients = []

    class FakeLiveClient:
        def __init__(self):
            self.connected = False
            self.logged_on = False
            self.login_key = "cached-login-key"
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

        def login(self, username, password="", login_key=None, **kwargs):
            self.connected = True
            self.logged_on = True
            self.account_name = username
            self.login_key_attempted = login_key
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
        reconfigure_timeout=1.0,
    )

    runtime.start(account, session_store.load_bundle_path(str(bundle_path)))
    updated_account = AccountProfile(
        profile_id="steam_7656119",
        display_name="Primary",
        account_name="primary_account",
        steam_id=steam_id,
        session_bundle_path=str(bundle_path),
        persona_state="Away",
        games=[IdleGame(app_id=730), IdleGame(app_id=570), IdleGame(app_id=440)],
    )
    reconfigured = runtime.reconfigure(
        updated_account,
        session_store.load_bundle_path(str(bundle_path)),
    )
    telemetry = runtime.inspect()["steam_7656119"]
    runtime.stop("steam_7656119")

    assert len(created_clients) == 1
    assert reconfigured.active_app_ids == [730, 570, 440]
    assert "updated" in reconfigured.message.lower()
    assert created_clients[0].played_calls[-1] == [730, 570, 440]
    assert telemetry.state == RuntimeState.BOOSTING
    assert telemetry.auth_source == "refresh_token"


def test_live_runtime_reconnects_with_cached_login_key(tmp_path) -> None:
    steam_id = "76561197960287930"
    created_clients = []

    class ReconnectingLiveClient:
        def __init__(self):
            self.connected = False
            self.logged_on = False
            self.login_key = ""
            self.sleep_calls = 0
            self.login_key_attempted = None
            self.played_calls = []
            self.client_index = len(created_clients)
            created_clients.append(self)

        def set_credential_location(self, path):
            self.credential_location = path

        def login_with_refresh_token(self, refresh_token, steam_id, account_name=""):
            self.connected = True
            self.logged_on = True
            self.login_key = "persisted-login-key"
            self.refresh_token = refresh_token
            self.account_name = account_name
            return EResult.OK

        def login(self, username, password="", login_key=None, **kwargs):
            self.connected = True
            self.logged_on = True
            self.account_name = username
            self.login_key_attempted = login_key
            self.login_key = login_key or ""
            return EResult.OK

        def change_status(self, **kwargs):
            return None

        def games_played(self, app_ids):
            self.played_calls.append(list(app_ids))

        def sleep(self, seconds):
            time.sleep(0.01)
            self.sleep_calls += 1
            if self.client_index == 0 and self.sleep_calls >= 2:
                self.connected = False
                self.logged_on = False

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
        client_factory=ReconnectingLiveClient,
        start_timeout=2.0,
        sleep_interval=0.01,
        reconnect_max_delay=0.01,
    )

    runtime.start(account, session_store.load_bundle_path(str(bundle_path)))

    telemetry = None
    deadline = time.time() + 1.0
    while time.time() < deadline:
        telemetry = runtime.inspect().get("steam_7656119")
        if telemetry and telemetry.state == RuntimeState.BOOSTING and telemetry.reconnect_attempts >= 1 and len(created_clients) >= 2:
            break
        time.sleep(0.02)

    runtime.stop("steam_7656119")

    assert telemetry is not None
    assert telemetry.state == RuntimeState.BOOSTING
    assert telemetry.reconnect_attempts >= 1
    assert len(created_clients) >= 2
    assert created_clients[1].login_key_attempted == "persisted-login-key"


def test_live_runtime_pauses_when_playing_session_is_blocked(tmp_path) -> None:
    steam_id = "76561197960287930"
    created_clients = []

    class BlockingLiveClient:
        def __init__(self):
            self.connected = False
            self.logged_on = False
            self.played_calls = []
            self.sent_messages = []
            self._handlers = {}
            created_clients.append(self)

        def on(self, event, callback):
            self._handlers.setdefault(event, []).append(callback)

        def emit_playing_state(self, *, blocked: bool, app_id: int):
            body = type("Body", (), {"playing_blocked": blocked, "playing_app": app_id})()
            message = type("Message", (), {"body": body})()
            for callback in self._handlers.get(EMsg.ClientPlayingSessionState, []):
                callback(message)

        def set_credential_location(self, path):
            self.credential_location = path

        def login_with_refresh_token(self, refresh_token, steam_id, account_name=""):
            self.connected = True
            self.logged_on = True
            self.refresh_token = refresh_token
            self.account_name = account_name
            return EResult.OK

        def change_status(self, **kwargs):
            return None

        def games_played(self, app_ids):
            self.played_calls.append(list(app_ids))

        def send(self, message):
            self.sent_messages.append(message)

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
        conflict_policy="pause",
        games=[IdleGame(app_id=730), IdleGame(app_id=570)],
    )
    runtime = SteamNetworkBoosterRuntime(
        session_store=session_store,
        client_factory=BlockingLiveClient,
        start_timeout=2.0,
        sleep_interval=0.01,
    )

    runtime.start(account, session_store.load_bundle_path(str(bundle_path)))
    created_clients[0].emit_playing_state(blocked=True, app_id=730)

    telemetry = None
    deadline = time.time() + 1.0
    while time.time() < deadline:
        telemetry = runtime.inspect().get("steam_7656119")
        if telemetry and telemetry.state == RuntimeState.PAUSED and telemetry.blocked_by_playing_session:
            break
        time.sleep(0.02)

    runtime.stop("steam_7656119")

    assert telemetry is not None
    assert telemetry.state == RuntimeState.PAUSED
    assert telemetry.blocked_by_playing_session is True
    assert telemetry.blocked_app_id == 730
    assert created_clients[0].played_calls[-1] == [730, 570]
    assert created_clients[0].sent_messages == []


def test_live_runtime_kicks_blocking_session_and_recovers(tmp_path) -> None:
    steam_id = "76561197960287930"
    created_clients = []

    class KickingLiveClient:
        def __init__(self):
            self.connected = False
            self.logged_on = False
            self.played_calls = []
            self.sent_messages = []
            self._handlers = {}
            created_clients.append(self)

        def on(self, event, callback):
            self._handlers.setdefault(event, []).append(callback)

        def emit_playing_state(self, *, blocked: bool, app_id: int):
            body = type("Body", (), {"playing_blocked": blocked, "playing_app": app_id})()
            message = type("Message", (), {"body": body})()
            for callback in self._handlers.get(EMsg.ClientPlayingSessionState, []):
                callback(message)

        def set_credential_location(self, path):
            self.credential_location = path

        def login_with_refresh_token(self, refresh_token, steam_id, account_name=""):
            self.connected = True
            self.logged_on = True
            self.refresh_token = refresh_token
            self.account_name = account_name
            return EResult.OK

        def change_status(self, **kwargs):
            return None

        def games_played(self, app_ids):
            self.played_calls.append(list(app_ids))

        def send(self, message):
            self.sent_messages.append(message)

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
        conflict_policy="kick",
        games=[IdleGame(app_id=730), IdleGame(app_id=570)],
    )
    runtime = SteamNetworkBoosterRuntime(
        session_store=session_store,
        client_factory=KickingLiveClient,
        start_timeout=2.0,
        sleep_interval=0.01,
    )

    runtime.start(account, session_store.load_bundle_path(str(bundle_path)))
    created_clients[0].emit_playing_state(blocked=True, app_id=570)

    blocked = None
    deadline = time.time() + 1.0
    while time.time() < deadline:
        blocked = runtime.inspect().get("steam_7656119")
        if blocked and blocked.state == RuntimeState.PAUSED and created_clients[0].sent_messages:
            break
        time.sleep(0.02)

    created_clients[0].emit_playing_state(blocked=False, app_id=0)

    recovered = None
    deadline = time.time() + 1.0
    while time.time() < deadline:
        recovered = runtime.inspect().get("steam_7656119")
        if recovered and recovered.state == RuntimeState.BOOSTING and not recovered.blocked_by_playing_session and len(created_clients[0].played_calls) >= 2:
            break
        time.sleep(0.02)

    runtime.stop("steam_7656119")

    assert blocked is not None
    assert blocked.state == RuntimeState.PAUSED
    assert blocked.blocked_app_id == 570
    assert created_clients[0].sent_messages
    assert created_clients[0].sent_messages[-1].msg == EMsg.ClientKickPlayingSession
    assert recovered is not None
    assert recovered.state == RuntimeState.BOOSTING
    assert recovered.blocked_by_playing_session is False
    assert created_clients[0].played_calls[-1] == [730, 570]


def test_live_runtime_yields_to_newer_session(tmp_path) -> None:
    steam_id = "76561197960287930"
    created_clients = []

    class YieldingLiveClient:
        def __init__(self):
            self.connected = False
            self.logged_on = False
            self.played_calls = []
            self._handlers = {}
            created_clients.append(self)

        def on(self, event, callback):
            self._handlers.setdefault(event, []).append(callback)

        def emit_playing_state(self, *, blocked: bool, app_id: int):
            body = type("Body", (), {"playing_blocked": blocked, "playing_app": app_id})()
            message = type("Message", (), {"body": body})()
            for callback in self._handlers.get(EMsg.ClientPlayingSessionState, []):
                callback(message)

        def set_credential_location(self, path):
            self.credential_location = path

        def login_with_refresh_token(self, refresh_token, steam_id, account_name=""):
            self.connected = True
            self.logged_on = True
            self.refresh_token = refresh_token
            self.account_name = account_name
            return EResult.OK

        def change_status(self, **kwargs):
            return None

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
        conflict_policy="yield",
        games=[IdleGame(app_id=730), IdleGame(app_id=570)],
    )
    runtime = SteamNetworkBoosterRuntime(
        session_store=session_store,
        client_factory=YieldingLiveClient,
        start_timeout=2.0,
        sleep_interval=0.01,
    )

    runtime.start(account, session_store.load_bundle_path(str(bundle_path)))
    created_clients[0].emit_playing_state(blocked=True, app_id=570)

    yielded = None
    deadline = time.time() + 1.0
    while time.time() < deadline:
        yielded = runtime.inspect().get("steam_7656119")
        if yielded and yielded.state == RuntimeState.IDLE and yielded.blocked_by_playing_session:
            break
        time.sleep(0.02)

    created_clients[0].emit_playing_state(blocked=False, app_id=0)

    cleared = None
    deadline = time.time() + 1.0
    while time.time() < deadline:
        cleared = runtime.inspect().get("steam_7656119")
        if cleared and cleared.state == RuntimeState.IDLE and not cleared.blocked_by_playing_session:
            break
        time.sleep(0.02)

    runtime.stop("steam_7656119")

    assert yielded is not None
    assert yielded.state == RuntimeState.IDLE
    assert yielded.blocked_app_id == 570
    assert created_clients[0].played_calls[-1] == []
    assert cleared is not None
    assert cleared.state == RuntimeState.IDLE
    assert cleared.blocked_by_playing_session is False


def test_live_runtime_auto_replies_with_cooldown_and_timeout(tmp_path) -> None:
    steam_id = "76561197960287930"
    created_clients = []

    class FakeChatUser:
        def __init__(self, steam_id, name):
            self.steam_id = steam_id
            self.name = name
            self.sent_messages = []

        def send_message(self, message):
            self.sent_messages.append(message)

    class AutoReplyLiveClient:
        def __init__(self):
            self.connected = False
            self.logged_on = False
            self.played_calls = []
            self._handlers = {}
            self.chat_user = FakeChatUser(76561198000000001, "Friend One")
            created_clients.append(self)

        def on(self, event, callback):
            self._handlers.setdefault(event, []).append(callback)

        def emit_chat_message(self, text):
            for callback in self._handlers.get("chat_message", []):
                callback(self.chat_user, text)

        def set_credential_location(self, path):
            self.credential_location = path

        def login_with_refresh_token(self, refresh_token, steam_id, account_name=""):
            self.connected = True
            self.logged_on = True
            self.refresh_token = refresh_token
            self.account_name = account_name
            return EResult.OK

        def change_status(self, **kwargs):
            return None

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
        auto_reply_enabled=True,
        auto_reply_message="I am hour boosting right now.",
        auto_reply_cooldown_seconds=30,
        auto_reply_timeout_seconds=60,
        games=[IdleGame(app_id=730), IdleGame(app_id=570)],
    )
    runtime = SteamNetworkBoosterRuntime(
        session_store=session_store,
        client_factory=AutoReplyLiveClient,
        start_timeout=2.0,
        sleep_interval=0.01,
    )

    original_time = time.time
    timeline = {"value": 1000.0}

    def fake_time():
        return timeline["value"]

    try:
        time.time = fake_time
        runtime.start(account, session_store.load_bundle_path(str(bundle_path)))

        created_clients[0].emit_chat_message("hey")
        first = runtime.inspect()["steam_7656119"]

        timeline["value"] += 10.0
        created_clients[0].emit_chat_message("still there?")
        second = runtime.inspect()["steam_7656119"]

        timeline["value"] += 31.0
        created_clients[0].emit_chat_message("checking again")
        third = runtime.inspect()["steam_7656119"]

        timeline["value"] += 61.0
        created_clients[0].emit_chat_message("new burst")
        fourth = runtime.inspect()["steam_7656119"]
    finally:
        time.time = original_time
        runtime.stop("steam_7656119")

    assert created_clients[0].chat_user.sent_messages == [
        "I am hour boosting right now.",
        "I am hour boosting right now.",
        "I am hour boosting right now.",
    ]
    assert first.auto_reply_sent_count == 1
    assert second.auto_reply_sent_count == 1
    assert third.auto_reply_sent_count == 2
    assert fourth.auto_reply_sent_count == 3
    assert fourth.auto_reply_last_sender == "Friend One"
