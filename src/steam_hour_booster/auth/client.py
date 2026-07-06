from __future__ import annotations

import json
import time
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


def _console_log(message: str) -> None:
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] [finish-booster-login] {message}", flush=True)


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
        login_key_timeout_seconds: float = 10.0,
    ) -> None:
        self._session_store = session_store or SessionStore()
        self._client_factory = client_factory or ValvePythonSteamClient
        self._login_key_timeout_seconds = max(1.0, float(login_key_timeout_seconds))

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
        _console_log(
            "Starting Steam client authorization for account=%s steam_id=%s profile_id=%s credential_dir=%s guard_kind=%s guard_supplied=%s"
            % (
                account_name,
                steam_id,
                profile_id,
                credential_dir,
                str(steam_guard_code_kind or "").strip().lower() or "none",
                "yes" if str(steam_guard_code or "").strip() else "no",
            )
        )
        client = self._client_factory()
        client.set_credential_location(str(credential_dir))

        connected = False
        try:
            connected = bool(client.connect(retry=0))
            _console_log(
                "Steam network connect finished: connected=%s logged_on=%s"
                % (connected, bool(getattr(client, "logged_on", False)))
            )
            if not connected:
                raise SteamClientAuthError("Unable to connect to the Steam client network.")

            result = self._attempt_login(
                client,
                account_name=account_name,
                password=password,
                steam_guard_code=steam_guard_code,
                steam_guard_code_kind=steam_guard_code_kind,
            )
            _console_log(
                "Steam client login returned result=%s connected=%s logged_on=%s"
                % (
                    self._result_label(result),
                    bool(getattr(client, "connected", False)),
                    bool(getattr(client, "logged_on", False)),
                )
            )

            if result == EResult.AccountLoginDeniedNeedTwoFactor:
                _console_log("Steam requested an app-based Steam Guard code.")
                raise SteamClientGuardRequiredError(
                    code_kind="app",
                    message="A Steam Guard app code is required for Steam client authorization.",
                )
            if result == EResult.AccountLogonDenied:
                _console_log("Steam requested an email-based Steam Guard code.")
                raise SteamClientGuardRequiredError(
                    code_kind="email",
                    message="A Steam Guard email code is required for Steam client authorization.",
                )
            if result == EResult.TwoFactorCodeMismatch:
                _console_log("Steam rejected the supplied app-based Steam Guard code.")
                raise SteamClientGuardRequiredError(
                    code_kind="app",
                    message="The Steam Guard app code was incorrect. Try again.",
                )
            if result == EResult.InvalidLoginAuthCode:
                _console_log("Steam rejected the supplied email-based Steam Guard code.")
                raise SteamClientGuardRequiredError(
                    code_kind="email",
                    message="The Steam Guard email code was incorrect. Try again.",
                )
            if result != EResult.OK:
                raise SteamClientAuthError(
                    "Steam client authorization failed: %s. Check the PowerShell console for finish-booster-login traces."
                    % self._result_label(result)
                )

            _console_log(
                "Steam login succeeded. Waiting up to %.1fs for a reusable login key."
                % self._login_key_timeout_seconds
            )
            login_key = self._wait_for_login_key(client, timeout=self._login_key_timeout_seconds)
            if not login_key:
                _console_log("Steam did not provide a reusable login key before the timeout expired.")
                raise SteamClientAuthError(
                    "Steam client authorization completed, but Steam did not issue a reusable login key. Check the PowerShell console for finish-booster-login traces."
                )

            cache_path = self._session_store.save_client_auth_cache(
                profile_id,
                {
                    "account_name": account_name,
                    "steam_id": steam_id,
                    "login_key": login_key,
                    "updated_at": _iso_timestamp(),
                },
            )
            _console_log(
                "Saved reusable login key to %s and completed Steam client authorization."
                % cache_path
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
                    _console_log("Logging out the temporary Steam client authorization session.")
                    client.logout()
            except Exception:
                pass
            try:
                _console_log("Disconnecting the temporary Steam client authorization session.")
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
                _console_log("Submitting Steam client login with an email Steam Guard code.")
                return client.login(account_name, password, auth_code=code)
            if normalized_kind == "app":
                _console_log("Submitting Steam client login with an app Steam Guard code.")
                return client.login(account_name, password, two_factor_code=code)

            _console_log("Submitting Steam client login with a guard code using app-first fallback.")
            first_attempt = client.login(account_name, password, two_factor_code=code)
            if first_attempt in (EResult.OK, EResult.AccountLoginDeniedNeedTwoFactor, EResult.TwoFactorCodeMismatch):
                return first_attempt
            _console_log(
                "App-code attempt returned %s, retrying the same guard code as an email code."
                % SteamClientAuthGateway._result_label(first_attempt)
            )
            return client.login(account_name, password, auth_code=code)

        _console_log("Submitting Steam client login with username/password and no Steam Guard code.")
        return client.login(account_name, password)

    @staticmethod
    def _wait_for_login_key(client, *, timeout: float) -> str:
        login_key = str(getattr(client, "login_key", "") or "").strip()
        if login_key:
            _console_log("A reusable login key was already present immediately after login.")
            return login_key

        event_name = getattr(client, "EVENT_NEW_LOGIN_KEY", "")
        if event_name and hasattr(client, "wait_event"):
            try:
                _console_log("Waiting for Steam event %s." % event_name)
                client.wait_event(event_name, timeout=timeout)
            except Exception:
                _console_log("Waiting for the Steam login-key event raised or timed out; falling back to polling.")
                pass
            login_key = str(getattr(client, "login_key", "") or "").strip()
            if login_key:
                _console_log("Received a reusable login key from the Steam login-key event.")
                return login_key

        deadline = time.monotonic() + max(0.0, float(timeout))
        _console_log("Polling for a reusable login key until the timeout expires.")
        while time.monotonic() < deadline:
            login_key = str(getattr(client, "login_key", "") or "").strip()
            if login_key:
                _console_log("A reusable login key appeared during fallback polling.")
                return login_key
            try:
                client.sleep(0.25)
            except Exception:
                time.sleep(0.25)

        return str(getattr(client, "login_key", "") or "").strip()

    @staticmethod
    def _result_label(result) -> str:
        if result is None:
            return "None"
        name = getattr(result, "name", "")
        value = getattr(result, "value", None)
        if name and value is not None:
            return "%s (%s)" % (name, value)
        if name:
            return str(name)
        return str(result)
