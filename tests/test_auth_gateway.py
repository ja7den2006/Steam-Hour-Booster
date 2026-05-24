from dataclasses import dataclass

from steam_hour_booster.auth.community import SteamCommunityAuthGateway


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
