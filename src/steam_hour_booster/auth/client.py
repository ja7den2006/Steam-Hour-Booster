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
    from steam.core.crypto import sha1_hash
    from steam.core.msg import MsgProto
    from steam.enums import EOSType, EResult
    from steam.enums.emsg import EMsg
    from steam.steamid import SteamID
    from steam.utils import ip4_to_int
except ImportError:
    ValvePythonSteamClient = None
    EResult = None
    EOSType = None
    EMsg = None
    MsgProto = None
    SteamID = None
    ip4_to_int = None
    sha1_hash = None


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
        login_response_timeout_seconds: float = 12.0,
    ) -> None:
        self._session_store = session_store or SessionStore()
        self._client_factory = client_factory or ValvePythonSteamClient
        self._login_key_timeout_seconds = max(1.0, float(login_key_timeout_seconds))
        self._login_response_timeout_seconds = max(3.0, float(login_response_timeout_seconds))

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

    def _attempt_login(
        self,
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
                return self._submit_login_request(
                    client,
                    account_name=account_name,
                    password=password,
                    auth_code=code,
                )
            if normalized_kind == "app":
                _console_log("Submitting Steam client login with an app Steam Guard code.")
                return self._submit_login_request(
                    client,
                    account_name=account_name,
                    password=password,
                    two_factor_code=code,
                )

            _console_log(
                "A guard code was entered before Steam requested a specific guard type. Ignoring it on the first attempt and sending password-only login."
            )

        _console_log("Submitting Steam client login with username/password and no Steam Guard code.")
        return self._submit_login_request(
            client,
            account_name=account_name,
            password=password,
        )

    def _submit_login_request(
        self,
        client,
        *,
        account_name: str,
        password: str,
        auth_code: str = "",
        two_factor_code: str = "",
    ):
        if not self._supports_instrumented_login(client):
            return client.login(
                account_name,
                password,
                auth_code=auth_code or None,
                two_factor_code=two_factor_code or None,
            )

        pre_login_result = client._pre_login()
        _console_log("Steam pre-login result=%s" % self._result_label(pre_login_result))
        if pre_login_result != EResult.OK:
            return pre_login_result

        callbacks = self._install_login_debug_watchers(client)
        events = callbacks["events"]
        message = MsgProto(EMsg.ClientLogon)
        message.header.steamid = SteamID(type="Individual", universe="Public")
        message.body.protocol_version = 65580
        message.body.client_package_version = 1561159470
        message.body.client_os_type = EOSType.Windows10
        message.body.client_language = "english"
        message.body.should_remember_password = True
        message.body.supports_rate_limit_response = True
        message.body.chat_mode = getattr(client, "chat_mode", 2)

        local_address = getattr(getattr(client, "connection", None), "local_address", None)
        if local_address:
            try:
                message.body.obfuscated_private_ip.v4 = ip4_to_int(local_address) ^ 0xF00DBAAD
            except Exception:
                message.body.obfuscated_private_ip.v4 = 0
        else:
            message.body.obfuscated_private_ip.v4 = 0

        message.body.account_name = account_name
        message.body.password = password

        sentry = None
        try:
            sentry = client.get_sentry(account_name)
        except Exception as exc:
            _console_log("Loading the local Steam sentry failed: %s" % (str(exc).strip() or exc.__class__.__name__))

        if sentry is None:
            message.body.eresult_sentryfile = EResult.FileNotFound
        else:
            message.body.eresult_sentryfile = EResult.OK
            message.body.sha_sentryfile = sha1_hash(sentry)

        if auth_code:
            message.body.auth_code = auth_code
        if two_factor_code:
            message.body.two_factor_code = two_factor_code

        _console_log(
            "Sending ClientLogon request with sentry=%s auth_code=%s two_factor_code=%s timeout=%.1fs"
            % (
                "present" if sentry is not None else "missing",
                "yes" if auth_code else "no",
                "yes" if two_factor_code else "no",
                self._login_response_timeout_seconds,
            )
        )
        client.send(message)

        result = self._wait_for_login_result(client, events, timeout=self._login_response_timeout_seconds)
        self._remove_login_debug_watchers(client, callbacks)
        return result

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

    def _wait_for_login_result(self, client, events: dict, *, timeout: float):
        deadline = time.monotonic() + max(0.0, float(timeout))
        disconnect_seen_at = None

        while time.monotonic() < deadline:
            logon_response = events.get("logon_response")
            if logon_response is not None:
                raw_result = getattr(getattr(logon_response, "body", None), "eresult", None)
                try:
                    result = EResult(raw_result)
                except Exception:
                    result = EResult.Fail
                _console_log(
                    "Received ClientLogOnResponse with eresult=%s"
                    % self._result_label(result)
                )
                return result

            auth_required = events.get("auth_code_required")
            if auth_required is not None and not bool(getattr(client, "connected", False)):
                is_2fa = bool(auth_required[0]) if len(auth_required) >= 1 else False
                code_mismatch = bool(auth_required[1]) if len(auth_required) >= 2 else False
                if is_2fa:
                    result = EResult.TwoFactorCodeMismatch if code_mismatch else EResult.AccountLoginDeniedNeedTwoFactor
                else:
                    result = EResult.InvalidLoginAuthCode if code_mismatch else EResult.AccountLogonDenied
                _console_log(
                    "Steam emitted auth_code_required with is_2fa=%s code_mismatch=%s -> %s"
                    % (is_2fa, code_mismatch, self._result_label(result))
                )
                return result

            error_result = events.get("error")
            if error_result is not None:
                _console_log("Steam emitted error event=%s" % self._result_label(error_result))
                if disconnect_seen_at is None and not bool(getattr(client, "connected", False)):
                    disconnect_seen_at = time.monotonic()
                if disconnect_seen_at is not None and (time.monotonic() - disconnect_seen_at) >= 0.35:
                    return error_result

            if events.get("disconnected"):
                if disconnect_seen_at is None:
                    disconnect_seen_at = time.monotonic()
                    _console_log("Steam emitted disconnected during login.")
                if disconnect_seen_at is not None and (time.monotonic() - disconnect_seen_at) >= 0.35:
                    if error_result is not None:
                        return error_result
                    return EResult.Fail

            try:
                client.sleep(0.1)
            except Exception:
                time.sleep(0.1)

        _console_log("Timed out waiting for a Steam login result after %.1fs." % timeout)
        return EResult.Fail

    def _install_login_debug_watchers(self, client) -> dict:
        events = {}
        listeners = []

        def add(event_name, key, formatter=None):
            def handler(*args):
                if key == "logon_response":
                    events[key] = args[0] if args else None
                elif key == "error":
                    error_value = args[0] if args else EResult.Fail
                    try:
                        events[key] = EResult(error_value)
                    except Exception:
                        events[key] = EResult.Fail
                elif key == "disconnected":
                    events[key] = True
                else:
                    events[key] = args
                if formatter is not None:
                    _console_log(formatter(*args))

            client.once(event_name, handler)
            listeners.append((event_name, handler))

        add(
            EMsg.ClientLogOnResponse,
            "logon_response",
            lambda msg: "Raw login response event received with body.eresult=%s"
            % getattr(getattr(msg, "body", None), "eresult", None),
        )
        add(
            getattr(client, "EVENT_ERROR", "error"),
            "error",
            lambda result: "Raw error event received with result=%s" % self._result_label(result),
        )
        add(
            getattr(client, "EVENT_AUTH_CODE_REQUIRED", "auth_code_required"),
            "auth_code_required",
            lambda is_2fa=False, code_mismatch=False: (
                "Raw auth_code_required event received: is_2fa=%s code_mismatch=%s"
                % (bool(is_2fa), bool(code_mismatch))
            ),
        )
        add(
            getattr(client, "EVENT_DISCONNECTED", "disconnected"),
            "disconnected",
            lambda *args: "Raw disconnected event received during login.",
        )
        add(
            getattr(client, "EVENT_NEW_LOGIN_KEY", "new_login_key"),
            "new_login_key",
            lambda *args: "Raw new_login_key event received during login.",
        )
        return {"events": events, "listeners": listeners}

    @staticmethod
    def _remove_login_debug_watchers(client, callbacks: dict) -> None:
        listeners = callbacks.get("listeners") or []
        if not hasattr(client, "remove_listener"):
            return
        for event_name, handler in listeners:
            try:
                client.remove_listener(event_name, handler)
            except Exception:
                pass

    @staticmethod
    def _supports_instrumented_login(client) -> bool:
        required_attributes = (
            "_pre_login",
            "once",
            "remove_listener",
            "send",
            "get_sentry",
            "connection",
        )
        return all(hasattr(client, attribute) for attribute in required_attributes)

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
