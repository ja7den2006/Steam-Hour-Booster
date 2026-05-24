from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, Optional

from steamcommunitykit import SteamClient
from steamcommunitykit.models import CommunityCredentials, QRAuthSession


@dataclass
class AuthSession:
    steam_id: str
    account_name: Optional[str]
    refresh_token: Optional[str]
    session_bundle: Dict[str, object]
    session_state: Dict[str, object]


class PendingQRLogin:
    def __init__(self, client: SteamClient, qr_session: QRAuthSession, qr_image_url: str) -> None:
        self.client = client
        self.qr_session = qr_session
        self.qr_image_url = qr_image_url

    @property
    def challenge_url(self) -> str:
        return self.qr_session.challenge_url


class SteamCommunityAuthGateway:
    def __init__(self, client_factory: Optional[Callable[[], SteamClient]] = None) -> None:
        self._client_factory = client_factory or SteamClient

    def login_with_credentials(
        self,
        account_name: str,
        password: str,
        *,
        persistence: bool = True,
        steam_guard_code: Optional[str] = None,
        steam_guard_code_provider: Optional[Callable[[dict], str]] = None,
        prompt_for_steam_guard: Optional[bool] = None,
    ) -> AuthSession:
        client = self._client_factory()
        result = client.login_to_community(
            account_name,
            password,
            persistence=persistence,
            steam_guard_code=steam_guard_code,
            steam_guard_code_provider=steam_guard_code_provider,
            prompt_for_steam_guard=prompt_for_steam_guard,
        )
        return self._build_auth_session(client, account_name=result.account_name)

    def login_with_refresh_token(self, refresh_token: str) -> AuthSession:
        client = self._client_factory()
        client.login_to_community_with_refresh_token(refresh_token)
        return self._build_auth_session(client)

    def begin_qr_login(self, device_friendly_name: str = "Steam Hour Booster") -> PendingQRLogin:
        client = self._client_factory()
        qr_session = client.auth.begin_auth_session_via_qr(
            device_friendly_name=device_friendly_name
        )
        qr_image_url = client.auth.build_qr_image_url(qr_session.challenge_url)
        return PendingQRLogin(client=client, qr_session=qr_session, qr_image_url=qr_image_url)

    def wait_for_qr_approval(
        self,
        pending: PendingQRLogin,
        *,
        timeout: float = 300.0,
    ) -> AuthSession:
        poll_result = pending.client.auth.wait_for_qr_approval(
            pending.qr_session,
            timeout=timeout,
        )
        credentials = pending.client.auth.community_credentials_from_refresh_token(
            str(poll_result["refresh_token"])
        )
        pending.client.set_community_credentials(credentials)
        return self._build_auth_session(
            pending.client,
            account_name=poll_result.get("account_name"),
        )

    def poll_qr_approval(self, pending: PendingQRLogin) -> Optional[AuthSession]:
        poll_result = pending.client.auth.poll_auth_session_status(
            pending.qr_session.client_id,
            pending.qr_session.request_id,
        )
        if not self._has_auth_tokens(poll_result):
            return None
        credentials = pending.client.auth.community_credentials_from_refresh_token(
            str(poll_result["refresh_token"])
        )
        pending.client.set_community_credentials(credentials)
        return self._build_auth_session(
            pending.client,
            account_name=poll_result.get("account_name"),
        )

    def export_session_bundle(self, credentials: CommunityCredentials) -> Dict[str, object]:
        client = self._client_factory()
        client.set_community_credentials(credentials)
        return client.export_community_session_bundle()

    @staticmethod
    def _has_auth_tokens(payload: Dict[str, object]) -> bool:
        return bool(payload.get("access_token") and payload.get("refresh_token"))

    @staticmethod
    def _build_auth_session(
        client: SteamClient,
        *,
        account_name: Optional[str] = None,
    ) -> AuthSession:
        bundle = client.export_community_session_bundle()
        state = client.get_community_session_state()
        return AuthSession(
            steam_id=str(bundle["steam_id"]),
            account_name=account_name,
            refresh_token=bundle.get("refresh_token"),
            session_bundle=bundle,
            session_state=state,
        )
