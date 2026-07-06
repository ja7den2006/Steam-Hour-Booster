from dataclasses import dataclass

from steam.enums import EResult

from steam_hour_booster.auth.client import SteamClientAuthError, SteamClientAuthGateway
from steam_hour_booster.auth.community import SteamCommunityAuthGateway
from steam_hour_booster.session_store import SessionStore


@dataclass
class FakeCredentials:
    steam_id: str
    session_id: str
    access_token: str = "access"
    refresh_token: str = "refresh"
    steam_login_secure: str = "cookie"


@dataclass
class FakeQRSession:
    client_id: int
    request_id: str
    interval: float
    challenge_url: str


class FakeAuthService:
    def begin_auth_session_via_qr(self, device_friendly_name: str = "Steam Hour Booster"):
        assert device_friendly_name
        return FakeQRSession(
            client_id=10,
            request_id="qr-request",
            interval=5.0,
            challenge_url="https://example.test/challenge",
        )

    def build_qr_image_url(self, challenge_url: str) -> str:
        return "https://example.test/qr?challenge=%s" % challenge_url

    def wait_for_qr_approval(self, session, *, timeout: float = 300.0):
        assert timeout == 90.0
        return {
            "refresh_token": "qr-refresh",
            "account_name": "qr-user",
        }

    def poll_auth_session_status(self, client_id: int, request_id: str):
        assert client_id == 10
        assert request_id == "qr-request"
        return {
            "refresh_token": "qr-refresh",
            "access_token": "qr-access",
            "account_name": "qr-user",
        }

    def community_credentials_from_refresh_token(self, refresh_token: str):
        assert refresh_token == "qr-refresh"
        return FakeCredentials(steam_id="7656119", session_id="session")


class FakeClient:
    def __init__(self) -> None:
        self.auth = FakeAuthService()
        self._bundle = {
            "steam_id": "7656119",
            "session_id": "session",
            "refresh_token": "refresh-token",
        }
        self._state = {
            "logged_in": True,
            "steam_id": "7656119",
            "has_refresh_token": True,
        }
        self.community_credentials = None

    def login_to_community(self, account_name: str, password: str, **kwargs):
        assert account_name == "demo"
        assert password == "secret"
        return type("Result", (), {"account_name": account_name})()

    def login_to_community_with_refresh_token(self, refresh_token: str):
        assert refresh_token == "refresh-token"
        return FakeCredentials(steam_id="7656119", session_id="session")

    def export_community_session_bundle(self):
        return dict(self._bundle)

    def get_community_session_state(self):
        return dict(self._state)

    def set_community_credentials(self, credentials):
        self.community_credentials = credentials


def test_gateway_wraps_credential_login() -> None:
    gateway = SteamCommunityAuthGateway(client_factory=FakeClient)
    session = gateway.login_with_credentials("demo", "secret")

    assert session.steam_id == "7656119"
    assert session.account_name == "demo"
    assert session.refresh_token == "refresh-token"


def test_gateway_starts_qr_login() -> None:
    gateway = SteamCommunityAuthGateway(client_factory=FakeClient)
    pending = gateway.begin_qr_login()

    assert pending.challenge_url == "https://example.test/challenge"
    assert pending.qr_image_url.startswith("https://example.test/qr?")


def test_gateway_finishes_qr_login() -> None:
    gateway = SteamCommunityAuthGateway(client_factory=FakeClient)
    pending = gateway.begin_qr_login()
    session = gateway.wait_for_qr_approval(pending, timeout=90.0)

    assert session.account_name == "qr-user"
    assert session.session_bundle["steam_id"] == "7656119"


def test_gateway_polls_qr_login() -> None:
    gateway = SteamCommunityAuthGateway(client_factory=FakeClient)
    pending = gateway.begin_qr_login()
    session = gateway.poll_qr_approval(pending)

    assert session is not None
    assert session.account_name == "qr-user"


class LoginKeyEventClient:
    EVENT_NEW_LOGIN_KEY = "new_login_key"

    def __init__(self) -> None:
        self.login_key = ""
        self.connected = False
        self.logged_on = False

    def set_credential_location(self, path):
        self.credential_location = path

    def connect(self, retry=0):
        self.connected = True
        return True

    def login(self, username, password="", login_key=None, auth_code=None, two_factor_code=None):
        self.username = username
        self.password = password
        self.login_key_attempted = login_key
        self.auth_code = auth_code
        self.two_factor_code = two_factor_code
        self.logged_on = True
        return EResult.OK

    def wait_event(self, event, timeout=None):
        if event == self.EVENT_NEW_LOGIN_KEY:
            self.login_key = "persisted-login-key"
            return tuple()
        return None

    def sleep(self, seconds):
        return None

    def logout(self):
        self.logged_on = False

    def disconnect(self):
        self.connected = False


class MissingLoginKeyClient(LoginKeyEventClient):
    def wait_event(self, event, timeout=None):
        del event, timeout
        return None


class FailingGuardCodeClient:
    def __init__(self) -> None:
        self.login_calls = []
        self.connected = False
        self.logged_on = False

    def set_credential_location(self, path):
        self.credential_location = path

    def connect(self, retry=0):
        self.connected = True
        return True

    def login(self, username, password="", login_key=None, auth_code=None, two_factor_code=None):
        self.login_calls.append(
            {
                "username": username,
                "password": password,
                "login_key": login_key,
                "auth_code": auth_code,
                "two_factor_code": two_factor_code,
            }
        )
        return EResult.Fail

    def logout(self):
        self.logged_on = False

    def disconnect(self):
        self.connected = False


def test_client_auth_gateway_waits_for_login_key_event(tmp_path) -> None:
    session_store = SessionStore(base_dir=tmp_path / "sessions")
    gateway = SteamClientAuthGateway(
        session_store=session_store,
        client_factory=LoginKeyEventClient,
        login_key_timeout_seconds=0.1,
    )

    result = gateway.authorize_credentials(
        profile_id="steam_7656119",
        account_name="demo",
        steam_id="7656119",
        password="secret",
    )

    assert result.login_key == "persisted-login-key"
    assert session_store.client_auth_cache_path("steam_7656119").exists() is True


def test_client_auth_gateway_errors_when_login_key_never_arrives(tmp_path) -> None:
    session_store = SessionStore(base_dir=tmp_path / "sessions")
    gateway = SteamClientAuthGateway(
        session_store=session_store,
        client_factory=MissingLoginKeyClient,
        login_key_timeout_seconds=0.1,
    )

    try:
        gateway.authorize_credentials(
            profile_id="steam_7656119",
            account_name="demo",
            steam_id="7656119",
            password="secret",
        )
    except SteamClientAuthError as exc:
        assert "did not issue a reusable login key" in str(exc)
    else:
        raise AssertionError("Expected SteamClientAuthError when login key never arrives.")


def test_client_auth_gateway_does_not_retry_unknown_guard_code_as_email(tmp_path) -> None:
    session_store = SessionStore(base_dir=tmp_path / "sessions")
    failing_client = FailingGuardCodeClient()
    gateway = SteamClientAuthGateway(
        session_store=session_store,
        client_factory=lambda: failing_client,
        login_key_timeout_seconds=0.1,
    )

    try:
        gateway.authorize_credentials(
            profile_id="steam_7656119",
            account_name="demo",
            steam_id="7656119",
            password="secret",
            steam_guard_code="ABCDE",
            steam_guard_code_kind="",
        )
    except SteamClientAuthError:
        pass
    else:
        raise AssertionError("Expected SteamClientAuthError for failing guard-code login.")

    assert len(failing_client.login_calls) == 1
    assert failing_client.login_calls[0]["two_factor_code"] == "ABCDE"
    assert failing_client.login_calls[0]["auth_code"] is None
