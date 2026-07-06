from dataclasses import dataclass

from steam_hour_booster.auth.client import (
    SteamClientAuthError,
    SteamClientAuthGateway,
    SteamClientGuardRequiredError,
)
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


def test_client_auth_gateway_uses_node_bridge_and_saves_client_refresh_token(tmp_path) -> None:
    session_store = SessionStore(base_dir=tmp_path / "sessions")
    calls = []

    def bridge_runner(payload):
        calls.append(payload)
        assert payload["accountName"] == "demo"
        assert payload["password"] == "secret"
        assert payload["steamGuardCode"] == "ABCDE"
        assert "cm_credentials" in payload["dataDirectory"]
        return {
            "status": "success",
            "account_name": "demo",
            "steam_id": "7656119",
            "refresh_token": "client-refresh-token",
            "token_expires_at": 1999999999,
            "source": "steam-user",
        }

    gateway = SteamClientAuthGateway(
        session_store=session_store,
        bridge_runner=bridge_runner,
    )

    result = gateway.authorize_credentials(
        profile_id="steam_7656119",
        account_name="demo",
        steam_id="7656119",
        password="secret",
        steam_guard_code="ABCDE",
    )

    cache = session_store.load_client_auth_cache("steam_7656119")

    assert len(calls) == 1
    assert result.refresh_token == "client-refresh-token"
    assert result.token_expires_at == 1999999999
    assert cache["client_refresh_token"] == "client-refresh-token"
    assert cache["source"] == "steam-user"


def test_client_auth_gateway_returns_structured_guard_requirement(tmp_path) -> None:
    session_store = SessionStore(base_dir=tmp_path / "sessions")

    def bridge_runner(payload):
        del payload
        return {
            "status": "steam_guard_required",
            "code_kind": "email",
            "associated_message": "example.com",
            "message": "A Steam Guard email code is required for example.com.",
        }

    gateway = SteamClientAuthGateway(
        session_store=session_store,
        bridge_runner=bridge_runner,
    )

    try:
        gateway.authorize_credentials(
            profile_id="steam_7656119",
            account_name="demo",
            steam_id="7656119",
            password="secret",
        )
    except SteamClientGuardRequiredError as exc:
        assert exc.code_kind == "email"
        assert exc.associated_message == "example.com"
        assert "email code" in str(exc)
    else:
        raise AssertionError("Expected SteamClientGuardRequiredError.")


def test_client_auth_gateway_surfaces_bridge_errors(tmp_path) -> None:
    session_store = SessionStore(base_dir=tmp_path / "sessions")

    def bridge_runner(payload):
        del payload
        return {
            "status": "error",
            "message": "Steam client authorization failed: InvalidPassword.",
            "eresult_name": "InvalidPassword",
        }

    gateway = SteamClientAuthGateway(
        session_store=session_store,
        bridge_runner=bridge_runner,
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
    except SteamClientAuthError as exc:
        assert "InvalidPassword" in str(exc)
        pass
    else:
        raise AssertionError("Expected SteamClientAuthError.")
