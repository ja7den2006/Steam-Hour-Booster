from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from steam_hour_booster.session_store import SessionStore

try:
    from steam.client import SteamClient as ValvePythonSteamClient
    from steam.enums import EResult
except ImportError:
    ValvePythonSteamClient = None
    EResult = None


def _iso_timestamp() -> str:
    return datetime.now().isoformat(timespec="seconds")


@dataclass
class ClientAuthResult:
    account_name: str
    steam_id: str
    login_key: str
    cache_path: str


class SteamClientAuthError(RuntimeError):
    pass


class SteamClientGuardRequiredError(SteamClientAuthError):
    def __init__(self, *, code_kind: str, message: str) -> None:
        super().__init__(message)
        self.code_kind = code_kind


class SteamClientAuthGateway:
    def __init__(
        self,
        *,
        session_store: Optional[SessionStore] = None,
        client_factory: Optional[Callable[[], ValvePythonSteamClient]] = None,
    ) -> None:
        self._session_store = session_store or SessionStore()
        self._client_factory = client_factory or ValvePythonSteamClient

    def authorize_credentials(
        self,
        *,
        profile_id: str,
        account_name: str,
        steam_id: str,
        password: str,
        steam_guard_code: str = "",
        steam_guard_code_kind: str = "",
    ) -> ClientAuthResult:
        if self._client_factory is None or EResult is None:
            raise SteamClientAuthError("The Steam client protocol package is not installed.")

        credential_dir = self._session_store.client_credentials_dir(profile_id)
        credential_dir.mkdir(parents=True, exist_ok=True)
        client = self._client_factory()
        client.set_credential_location(str(credential_dir))

        connected = False
        try:
            connected = bool(client.connect(retry=0))
            if not connected:
                raise SteamClientAuthError("Unable to connect to the Steam client network.")

            result = self._attempt_login(
                client,
                account_name=account_name,
                password=password,
                steam_guard_code=steam_guard_code,
                steam_guard_code_kind=steam_guard_code_kind,
            )

            if result == EResult.AccountLoginDeniedNeedTwoFactor:
                raise SteamClientGuardRequiredError(
                    code_kind="app",
                    message="A Steam Guard app code is required for Steam client authorization.",
                )
            if result == EResult.AccountLogonDenied:
                raise SteamClientGuardRequiredError(
                    code_kind="email",
                    message="A Steam Guard email code is required for Steam client authorization.",
                )
            if result == EResult.TwoFactorCodeMismatch:
                raise SteamClientGuardRequiredError(
                    code_kind="app",
                    message="The Steam Guard app code was incorrect. Try again.",
                )
            if result == EResult.InvalidLoginAuthCode:
                raise SteamClientGuardRequiredError(
                    code_kind="email",
                    message="The Steam Guard email code was incorrect. Try again.",
                )
            if result != EResult.OK:
                raise SteamClientAuthError(
                    "Steam client authorization failed: %s." % getattr(result, "name", result)
                )

            login_key = str(getattr(client, "login_key", "") or "").strip()
            if not login_key:
                raise SteamClientAuthError("Steam client authorization succeeded, but no login key was returned.")

            cache_path = self._session_store.save_client_auth_cache(
                profile_id,
                {
                    "account_name": account_name,
                    "steam_id": steam_id,
                    "login_key": login_key,
                    "updated_at": _iso_timestamp(),
                },
            )
            return ClientAuthResult(
                account_name=account_name,
                steam_id=steam_id,
                login_key=login_key,
                cache_path=str(cache_path),
            )
        finally:
            try:
                if connected and getattr(client, "logged_on", False):
                    client.logout()
            except Exception:
                pass
            try:
                client.disconnect()
            except Exception:
                pass

    @staticmethod
    def _attempt_login(
        client,
        *,
        account_name: str,
        password: str,
        steam_guard_code: str,
        steam_guard_code_kind: str,
    ):
        normalized_kind = str(steam_guard_code_kind or "").strip().lower()
        code = str(steam_guard_code or "").strip()

        if code:
            if normalized_kind == "email":
                return client.login(account_name, password, auth_code=code)
            if normalized_kind == "app":
                return client.login(account_name, password, two_factor_code=code)

            first_attempt = client.login(account_name, password, two_factor_code=code)
            if first_attempt in (EResult.OK, EResult.AccountLoginDeniedNeedTwoFactor, EResult.TwoFactorCodeMismatch):
                return first_attempt
            return client.login(account_name, password, auth_code=code)

        return client.login(account_name, password)
