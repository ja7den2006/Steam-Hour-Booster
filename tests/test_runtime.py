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
from steam.enums.emsg import EMsg


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
