from __future__ import annotations

import base64
import json
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Callable, Dict, List, Optional, Protocol

from steam_hour_booster.library import OwnedGamesValidationResult, OwnedGamesValidator
from steam_hour_booster.models import (
    CONFLICT_POLICY_KICK,
    CONFLICT_POLICY_PAUSE,
    CONFLICT_POLICY_YIELD,
    AccountProfile,
)
from steam_hour_booster.session_store import SessionStore

try:
    from steam.client import SteamClient as ValvePythonSteamClient
    from steam.core.crypto import sha1_hash
    from steam.core.msg import MsgProto
    from steam.enums import EOSType, EResult
    from steam.enums.common import EPersonaState
    from steam.enums.emsg import EMsg
    from steam.steamid import SteamID
    from steam.utils import ip4_to_int
except ImportError:
    ValvePythonSteamClient = None
    sha1_hash = None
    MsgProto = None
    EOSType = None
    EResult = None
    EPersonaState = None
    EMsg = None
    SteamID = None
    ip4_to_int = None


def _iso_timestamp() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _conflict_policy_label(policy: str) -> str:
    if policy == CONFLICT_POLICY_KICK:
        return "Force Kick"
    if policy == CONFLICT_POLICY_YIELD:
        return "Yield to New Session"
    return "Pause and Wait"


def _effective_persona_state(persona_state: str, appear_online: bool) -> str:
    if not appear_online:
        return "Invisible"
    normalized = str(persona_state or "").strip() or "Online"
    if normalized in ("Invisible", "Offline"):
        return "Online"
    return normalized


def _owned_games_validation_label(state: str) -> str:
    if state == "valid":
        return "Verified"
    if state == "invalid":
        return "Blocked"
    if state == "unavailable":
        return "Unavailable"
    if state == "skipped":
        return "Skipped"
    return "Pending"


class RuntimeState(str, Enum):
    IDLE = "idle"
    NEEDS_AUTH = "needs_auth"
    READY = "ready"
    STARTING = "starting"
    BOOSTING = "boosting"
    PAUSED = "paused"
    ERROR = "error"


@dataclass
class AccountRuntimeStatus:
    profile_id: str
    display_name: str
    state: RuntimeState = RuntimeState.IDLE
    session_ready: bool = False
    runtime_ready: bool = False
    runtime_auth_message: str = ""
    login_mode: str = ""
    boost_enabled: bool = True
    appear_online: bool = True
    persona_state: str = "Online"
    effective_persona_state: str = "Online"
    conflict_policy: str = CONFLICT_POLICY_PAUSE
    session_bundle_path: str = ""
    configured_app_ids: List[int] = field(default_factory=list)
    active_app_ids: List[int] = field(default_factory=list)
    owned_games_validation_state: str = "skipped"
    owned_games_validation_message: str = ""
    owned_games_validation_checked_at: str = ""
    owned_games_api_key_available: bool = False
    owned_games_validated_app_ids: List[int] = field(default_factory=list)
    owned_games_missing_app_ids: List[int] = field(default_factory=list)
    owned_games_matched_titles: Dict[int, str] = field(default_factory=dict)
    custom_status: str = ""
    auto_reply_enabled: bool = False
    auto_reply_message: str = ""
    auto_reply_cooldown_seconds: int = 180
    auto_reply_timeout_seconds: int = 1800
    auto_reply_sent_count: int = 0
    auto_reply_last_sent_at: str = ""
    auto_reply_last_sender: str = ""
    auto_reply_last_message_at: str = ""
    auth_source: str = ""
    reconnect_attempts: int = 0
    connected_at: str = ""
    blocked_by_playing_session: bool = False
    blocked_app_id: int = 0
    last_error: str = ""
    message: str = ""
    updated_at: str = ""

    def to_dict(self) -> Dict[str, object]:
        can_start = self.state == RuntimeState.READY
        can_stop = self.state in (RuntimeState.STARTING, RuntimeState.BOOSTING, RuntimeState.PAUSED)
        return {
            "profile_id": self.profile_id,
            "display_name": self.display_name,
            "state": self.state.value,
            "state_label": self.state.value.replace("_", " ").title(),
            "session_ready": self.session_ready,
            "runtime_ready": self.runtime_ready,
            "runtime_auth_message": self.runtime_auth_message,
            "login_mode": self.login_mode,
            "boost_enabled": self.boost_enabled,
            "appear_online": self.appear_online,
            "persona_state": self.persona_state,
            "effective_persona_state": self.effective_persona_state,
            "conflict_policy": self.conflict_policy,
            "conflict_policy_label": _conflict_policy_label(self.conflict_policy),
            "session_bundle_path": self.session_bundle_path,
            "configured_app_ids": list(self.configured_app_ids),
            "configured_slot_count": len(self.configured_app_ids),
            "active_app_ids": list(self.active_app_ids),
            "active_slot_count": len(self.active_app_ids),
            "owned_games_validation_state": self.owned_games_validation_state,
            "owned_games_validation_label": _owned_games_validation_label(self.owned_games_validation_state),
            "owned_games_validation_message": self.owned_games_validation_message,
            "owned_games_validation_checked_at": self.owned_games_validation_checked_at,
            "owned_games_api_key_available": self.owned_games_api_key_available,
            "owned_games_validated_app_ids": list(self.owned_games_validated_app_ids),
            "owned_games_missing_app_ids": list(self.owned_games_missing_app_ids),
            "owned_games_matched_titles": dict(self.owned_games_matched_titles),
            "custom_status": self.custom_status,
            "auto_reply_enabled": self.auto_reply_enabled,
            "auto_reply_message": self.auto_reply_message,
            "auto_reply_cooldown_seconds": self.auto_reply_cooldown_seconds,
            "auto_reply_timeout_seconds": self.auto_reply_timeout_seconds,
            "auto_reply_sent_count": self.auto_reply_sent_count,
            "auto_reply_last_sent_at": self.auto_reply_last_sent_at,
            "auto_reply_last_sender": self.auto_reply_last_sender,
            "auto_reply_last_message_at": self.auto_reply_last_message_at,
            "auth_source": self.auth_source,
            "reconnect_attempts": self.reconnect_attempts,
            "connected_at": self.connected_at,
            "blocked_by_playing_session": self.blocked_by_playing_session,
            "blocked_app_id": self.blocked_app_id,
            "last_error": self.last_error,
            "message": self.message,
            "updated_at": self.updated_at,
            "can_start": can_start,
            "can_stop": can_stop,
        }


@dataclass
class RuntimeSnapshot:
    accounts: Dict[str, AccountRuntimeStatus] = field(default_factory=dict)
    transport_name: str = "local-preview"
    preview_mode: bool = True
    event_log_path: str = ""
    event_count: int = 0
    recent_events: List[str] = field(default_factory=list)
    updated_at: str = ""

    def to_dict(self) -> Dict[str, object]:
        statuses = [
            status.to_dict()
            for status in sorted(
                self.accounts.values(),
                key=lambda item: item.display_name.lower(),
            )
        ]
        ready_count = sum(1 for status in self.accounts.values() if status.state == RuntimeState.READY)
        needs_auth_count = sum(1 for status in self.accounts.values() if status.state == RuntimeState.NEEDS_AUTH)
        boosting_count = sum(1 for status in self.accounts.values() if status.state == RuntimeState.BOOSTING)
        paused_count = sum(1 for status in self.accounts.values() if status.state == RuntimeState.PAUSED)
        error_count = sum(1 for status in self.accounts.values() if status.state == RuntimeState.ERROR)
        blocked_count = sum(1 for status in self.accounts.values() if status.blocked_by_playing_session)
        disabled_count = sum(1 for status in self.accounts.values() if not status.boost_enabled)
        auto_reply_enabled_count = sum(1 for status in self.accounts.values() if status.auto_reply_enabled)
        library_validation_blocked_count = sum(
            1 for status in self.accounts.values() if status.owned_games_validation_state == "invalid"
        )
        library_validation_unavailable_count = sum(
            1 for status in self.accounts.values() if status.owned_games_validation_state == "unavailable"
        )
        library_validation_verified_count = sum(
            1 for status in self.accounts.values() if status.owned_games_validation_state == "valid"
        )
        active_slot_count = sum(len(status.active_app_ids) for status in self.accounts.values())
        return {
            "transport_name": self.transport_name,
            "preview_mode": self.preview_mode,
            "event_log_path": self.event_log_path,
            "event_count": self.event_count,
            "updated_at": self.updated_at,
            "counts": {
                "tracked_accounts": len(statuses),
                "ready_accounts": ready_count,
                "needs_auth_accounts": needs_auth_count,
                "boosting_accounts": boosting_count,
                "paused_accounts": paused_count,
                "error_accounts": error_count,
                "blocked_accounts": blocked_count,
                "disabled_accounts": disabled_count,
                "auto_reply_enabled_accounts": auto_reply_enabled_count,
                "library_validation_blocked_accounts": library_validation_blocked_count,
                "library_validation_unavailable_accounts": library_validation_unavailable_count,
                "library_validation_verified_accounts": library_validation_verified_count,
                "active_slots": active_slot_count,
            },
            "statuses": statuses,
            "recent_events": list(self.recent_events),
        }


@dataclass
class RuntimeStartResult:
    active_app_ids: List[int]
    message: str


@dataclass
class RuntimeLaneTelemetry:
    state: RuntimeState
    active_app_ids: List[int] = field(default_factory=list)
    auth_source: str = ""
    reconnect_attempts: int = 0
    connected_at: str = ""
    conflict_policy: str = CONFLICT_POLICY_PAUSE
    auto_reply_enabled: bool = False
    auto_reply_message: str = ""
    auto_reply_cooldown_seconds: int = 180
    auto_reply_timeout_seconds: int = 1800
    auto_reply_sent_count: int = 0
    auto_reply_last_sent_at: str = ""
    auto_reply_last_sender: str = ""
    auto_reply_last_message_at: str = ""
    blocked_by_playing_session: bool = False
    blocked_app_id: int = 0
    last_error: str = ""
    message: str = ""
    updated_at: str = ""


class BoosterRuntime(Protocol):
    name: str
    preview_mode: bool

    def start(self, account: AccountProfile, session_bundle: Dict[str, object]) -> RuntimeStartResult:
        ...

    def stop(self, profile_id: str) -> str:
        ...

    def reconfigure(self, account: AccountProfile, session_bundle: Dict[str, object]) -> RuntimeStartResult:
        ...

    def inspect(self) -> Dict[str, RuntimeLaneTelemetry]:
        ...

    def shutdown(self) -> None:
        ...


class PreviewBoosterRuntime:
    name = "local-preview"
    preview_mode = True

    def __init__(self) -> None:
        self._active_profiles: Dict[str, Dict[str, object]] = {}

    def start(self, account: AccountProfile, session_bundle: Dict[str, object]) -> RuntimeStartResult:
        del session_bundle
        active_app_ids = [int(game.app_id) for game in account.games if game.enabled]
        self._active_profiles[account.profile_id] = {
            "active_app_ids": list(active_app_ids),
            "conflict_policy": account.conflict_policy,
            "auto_reply_enabled": account.auto_reply_enabled,
            "auto_reply_message": account.auto_reply_message,
            "auto_reply_cooldown_seconds": max(15, int(account.auto_reply_cooldown_seconds)),
            "auto_reply_timeout_seconds": max(30, int(account.auto_reply_timeout_seconds)),
        }
        slot_count = len(active_app_ids)
        return RuntimeStartResult(
            active_app_ids=active_app_ids,
            message="Preview runtime armed with %s slot%s." % (slot_count, "" if slot_count == 1 else "s"),
        )

    def stop(self, profile_id: str) -> str:
        self._active_profiles.pop(profile_id, None)
        return "Boost lane stopped."

    def reconfigure(self, account: AccountProfile, session_bundle: Dict[str, object]) -> RuntimeStartResult:
        del session_bundle
        active_app_ids = [int(game.app_id) for game in account.games if game.enabled]
        self._active_profiles[account.profile_id] = {
            "active_app_ids": list(active_app_ids),
            "conflict_policy": account.conflict_policy,
            "auto_reply_enabled": account.auto_reply_enabled,
            "auto_reply_message": account.auto_reply_message,
            "auto_reply_cooldown_seconds": max(15, int(account.auto_reply_cooldown_seconds)),
            "auto_reply_timeout_seconds": max(30, int(account.auto_reply_timeout_seconds)),
        }
        return RuntimeStartResult(
            active_app_ids=active_app_ids,
            message="Preview lane updated to %s slot%s." % (len(active_app_ids), "" if len(active_app_ids) == 1 else "s"),
        )

    def inspect(self) -> Dict[str, RuntimeLaneTelemetry]:
        now = _iso_timestamp()
        return {
            profile_id: RuntimeLaneTelemetry(
                state=RuntimeState.BOOSTING,
                active_app_ids=list(payload.get("active_app_ids", [])),
                auth_source="preview",
                conflict_policy=str(payload.get("conflict_policy", CONFLICT_POLICY_PAUSE)),
                auto_reply_enabled=bool(payload.get("auto_reply_enabled", False)),
                auto_reply_message=str(payload.get("auto_reply_message", "")),
                auto_reply_cooldown_seconds=int(payload.get("auto_reply_cooldown_seconds", 180)),
                auto_reply_timeout_seconds=int(payload.get("auto_reply_timeout_seconds", 1800)),
                message="Preview runtime lane active with %s slot%s." % (
                    len(payload.get("active_app_ids", [])),
                    "" if len(payload.get("active_app_ids", [])) == 1 else "s",
                ),
                updated_at=now,
            )
            for profile_id, payload in self._active_profiles.items()
        }

    def shutdown(self) -> None:
        self._active_profiles.clear()


def _decode_jwt_claims(token: str) -> Dict[str, object]:
    parts = str(token or "").split(".")
    if len(parts) < 2:
        return {}
    payload = parts[1]
    padding = "=" * (-len(payload) % 4)
    try:
        decoded = base64.urlsafe_b64decode(payload + padding).decode("utf-8")
        parsed = json.loads(decoded)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _refresh_token_is_client_usable(refresh_token: str) -> bool:
    claims = _decode_jwt_claims(refresh_token)
    issuer = str(claims.get("iss", "")).strip()
    audiences = claims.get("aud") or []
    if isinstance(audiences, str):
        audiences = [audiences]
    return issuer == "steam" and "client" in audiences


if ValvePythonSteamClient is not None:
    class RefreshTokenSteamClient(ValvePythonSteamClient):
        def login_with_refresh_token(
            self,
            refresh_token: str,
            steam_id: str,
            *,
            account_name: str = "",
            login_id: Optional[int] = None,
        ):
            if not str(refresh_token or "").strip():
                raise ValueError("refresh_token is required")
            if not str(steam_id or "").strip():
                raise ValueError("steam_id is required")

            eresult = self._pre_login()
            if eresult != EResult.OK:
                return eresult

            self.username = account_name or str(steam_id)

            message = MsgProto(EMsg.ClientLogon)
            message.header.steamid = SteamID(int(steam_id))
            message.body.protocol_version = 65580
            message.body.client_package_version = 1561159470
            message.body.client_os_type = EOSType.Windows10
            message.body.client_language = "english"
            message.body.should_remember_password = True
            message.body.supports_rate_limit_response = True
            message.body.chat_mode = self.chat_mode

            if login_id is None:
                message.body.obfuscated_private_ip.v4 = ip4_to_int(self.connection.local_address) ^ 0xF00DBAAD
            else:
                message.body.obfuscated_private_ip.v4 = int(login_id)

            if account_name:
                message.body.account_name = str(account_name)

            sentry = self.get_sentry(self.username)
            if sentry is None:
                message.body.eresult_sentryfile = EResult.FileNotFound
            else:
                message.body.eresult_sentryfile = EResult.OK
                message.body.sha_sentryfile = sha1_hash(sentry)

            message.body.access_token = str(refresh_token)
            self.send(message)

            resp = self.wait_msg(EMsg.ClientLogOnResponse, timeout=30)
            if resp and resp.body.eresult == EResult.OK:
                self.sleep(0.5)
            return EResult(resp.body.eresult) if resp else EResult.Fail
else:
    RefreshTokenSteamClient = None


@dataclass
class _LiveWorkerConfig:
    profile_id: str
    display_name: str
    account_name: str
    steam_id: str
    refresh_token: str
    appear_online: bool
    persona_state: str
    conflict_policy: str
    custom_status: str
    auto_reply_enabled: bool
    auto_reply_message: str
    auto_reply_cooldown_seconds: int
    auto_reply_timeout_seconds: int
    app_ids: List[int]


@dataclass
class _LoginAttemptResult:
    client: Optional[RefreshTokenSteamClient]
    auth_source: str = ""
    error_message: str = ""


@dataclass
class _AutoReplyConversation:
    window_started_at: float
    last_incoming_at: float
    last_reply_at: float = 0.0


class _LiveBoostWorker:
    def __init__(
        self,
        *,
        config: _LiveWorkerConfig,
        client_factory: Callable[[], RefreshTokenSteamClient],
        credential_dir: Path,
        sleep_interval: float = 1.0,
        reconnect_max_delay: float = 30.0,
    ) -> None:
        self._client_factory = client_factory
        self._credential_dir = credential_dir
        self._sleep_interval = max(0.05, float(sleep_interval))
        self._reconnect_max_delay = max(0.1, float(reconnect_max_delay))
        self._client = None
        self._thread = None
        self._stop_event = threading.Event()
        self._started_event = threading.Event()
        self._stopped_event = threading.Event()
        self._condition = threading.Condition()
        self._desired_config = config
        self._desired_revision = 0
        self._applied_revision = -1
        self._active_app_ids: List[int] = []
        self._state = RuntimeState.STARTING
        self._message = "Boost lane not started."
        self._auth_source = ""
        self._reconnect_attempts = 0
        self._connected_at = ""
        self._blocked_by_playing_session = False
        self._blocked_app_id = 0
        self._awaiting_unblock_reapply = False
        self._yielded_to_new_session = False
        self._last_kick_attempt_at = 0.0
        self._last_error = ""
        self._auto_reply_sent_count = 0
        self._auto_reply_last_sent_at = ""
        self._auto_reply_last_sender = ""
        self._auto_reply_last_message_at = ""
        self._chat_sessions: Dict[int, _AutoReplyConversation] = {}
        self._updated_at = _iso_timestamp()
        self._error_message = ""
        self._cached_login_key = self._load_cached_login_key()

    @property
    def active_app_ids(self) -> List[int]:
        with self._condition:
            return list(self._active_app_ids)

    def start_and_wait(self, timeout: float) -> RuntimeStartResult:
        if self._thread is not None:
            raise RuntimeError("Boost worker already started.")

        self._thread = threading.Thread(
            target=self._run,
            name="steam-hour-booster-%s" % self._desired_config.profile_id,
            daemon=True,
        )
        self._thread.start()

        if not self._started_event.wait(max(timeout, 1.0)):
            self.stop_and_wait(timeout=5.0)
            raise RuntimeError("Timed out waiting for the Steam client session to start.")

        with self._condition:
            if self._error_message:
                raise RuntimeError(self._error_message)
            return RuntimeStartResult(
                active_app_ids=list(self._active_app_ids),
                message=self._message,
            )

    def stop_and_wait(self, timeout: float = 10.0) -> str:
        self._stop_event.set()
        with self._condition:
            self._condition.notify_all()
        if self._thread is not None:
            self._thread.join(max(timeout, 0.5))
        with self._condition:
            return self._message or "Boost lane stopped."

    def reconfigure_and_wait(self, config: _LiveWorkerConfig, timeout: float = 6.0) -> RuntimeStartResult:
        if self._thread is None or not self._thread.is_alive():
            raise RuntimeError("Boost lane is not running.")

        with self._condition:
            self._desired_config = config
            self._desired_revision += 1
            requested_revision = self._desired_revision
            self._message = "Applying live lane update."
            self._updated_at = _iso_timestamp()
            self._condition.notify_all()

        deadline = time.time() + max(0.5, float(timeout))
        while time.time() < deadline:
            with self._condition:
                if self._error_message:
                    raise RuntimeError(self._error_message)
                if self._applied_revision >= requested_revision:
                    return RuntimeStartResult(
                        active_app_ids=list(self._active_app_ids),
                        message=self._message,
                    )
                remaining = deadline - time.time()
                self._condition.wait(timeout=min(0.15, max(remaining, 0.01)))

        with self._condition:
            queued_message = self._message
            if self._state == RuntimeState.PAUSED:
                queued_message = "Live lane update queued while the Steam client reconnects."
            elif not queued_message or queued_message == "Applying live lane update.":
                queued_message = "Live lane update queued."
            return RuntimeStartResult(
                active_app_ids=list(self._active_app_ids),
                message=queued_message,
            )

    def snapshot(self) -> RuntimeLaneTelemetry:
        with self._condition:
            return RuntimeLaneTelemetry(
                state=self._state,
                active_app_ids=list(self._active_app_ids),
                auth_source=self._auth_source,
                reconnect_attempts=self._reconnect_attempts,
                connected_at=self._connected_at,
                conflict_policy=self._desired_config.conflict_policy,
                auto_reply_enabled=self._desired_config.auto_reply_enabled,
                auto_reply_message=self._desired_config.auto_reply_message,
                auto_reply_cooldown_seconds=self._desired_config.auto_reply_cooldown_seconds,
                auto_reply_timeout_seconds=self._desired_config.auto_reply_timeout_seconds,
                auto_reply_sent_count=self._auto_reply_sent_count,
                auto_reply_last_sent_at=self._auto_reply_last_sent_at,
                auto_reply_last_sender=self._auto_reply_last_sender,
                auto_reply_last_message_at=self._auto_reply_last_message_at,
                blocked_by_playing_session=self._blocked_by_playing_session,
                blocked_app_id=self._blocked_app_id,
                last_error=self._last_error,
                message=self._message,
                updated_at=self._updated_at,
            )

    def _run(self) -> None:
        try:
            initial = self._attempt_login(allow_login_key=True)
            if initial.client is None:
                failure = initial.error_message or "Steam client logon failed."
                with self._condition:
                    self._error_message = failure
                self._set_status(RuntimeState.ERROR, failure, last_error=failure)
                return

            self._client = initial.client
            self._apply_desired_config(initial.auth_source, mode="started")
            self._started_event.set()

            while not self._stop_event.is_set():
                client = self._client
                if client is None:
                    client = self._recover_from_disconnect()
                    if client is None:
                        return

                if not client.connected or not client.logged_on:
                    disconnect_error = "Steam client session disconnected."
                    self._set_status(
                        RuntimeState.PAUSED,
                        "Steam client session lost. Reconnecting with backoff.",
                        active_app_ids=self.active_app_ids,
                        last_error=disconnect_error,
                    )
                    self._safe_disconnect_client(client)
                    self._client = None
                    continue

                self._maybe_persist_login_key(client)

                with self._condition:
                    needs_apply = self._applied_revision < self._desired_revision
                    awaiting_unblock_reapply = self._awaiting_unblock_reapply

                if self._synchronize_playing_lock(client):
                    continue

                if needs_apply or awaiting_unblock_reapply:
                    self._apply_desired_config(self._auth_source or "refresh_token", mode="updated")

                if self._stop_event.is_set():
                    break
                client.sleep(self._sleep_interval)
        except Exception as exc:
            failure = str(exc).strip() or "Unexpected Steam client runtime failure."
            with self._condition:
                self._error_message = failure
            self._set_status(RuntimeState.ERROR, failure, last_error=failure)
        finally:
            if not self._started_event.is_set():
                self._started_event.set()
            self._safe_disconnect_client(self._client)
            self._client = None
            with self._condition:
                self._active_app_ids = []
                self._condition.notify_all()
            self._stopped_event.set()

    def _recover_from_disconnect(self) -> Optional[RefreshTokenSteamClient]:
        while not self._stop_event.is_set():
            self._reconnect_attempts += 1
            attempt_number = self._reconnect_attempts
            delay_seconds = self._reconnect_delay_for_attempt(attempt_number)
            self._set_status(
                RuntimeState.PAUSED,
                "Reconnect attempt %s in %.1fs." % (attempt_number, delay_seconds),
                active_app_ids=self.active_app_ids,
                reconnect_attempts=attempt_number,
            )
            if self._stop_event.wait(delay_seconds):
                return None

            attempt = self._attempt_login(allow_login_key=True)
            if attempt.client is not None:
                self._client = attempt.client
                self._apply_desired_config(attempt.auth_source, mode="resumed")
                return attempt.client

            failure = attempt.error_message or "Reconnect attempt failed."
            self._set_status(
                RuntimeState.PAUSED,
                "Reconnect attempt %s failed: %s" % (attempt_number, failure),
                active_app_ids=self.active_app_ids,
                reconnect_attempts=attempt_number,
                last_error=failure,
            )
        return None

    def _attempt_login(self, *, allow_login_key: bool) -> _LoginAttemptResult:
        self._credential_dir.mkdir(parents=True, exist_ok=True)
        errors: List[str] = []
        config = self._clone_config()

        if allow_login_key and self._cached_login_key and config.account_name:
            login_key_client = self._client_factory()
            self._attach_client_hooks(login_key_client)
            login_key_client.set_credential_location(str(self._credential_dir))
            result = login_key_client.login(config.account_name, login_key=self._cached_login_key)
            if result == EResult.OK:
                return _LoginAttemptResult(login_key_client, auth_source="login_key")
            errors.append("login-key logon failed: %s." % getattr(result, "name", result))
            self._clear_cached_login_key()
            self._safe_disconnect_client(login_key_client)

        refresh_client = self._client_factory()
        self._attach_client_hooks(refresh_client)
        refresh_client.set_credential_location(str(self._credential_dir))
        result = refresh_client.login_with_refresh_token(
            config.refresh_token,
            config.steam_id,
            account_name=config.account_name,
        )
        if result == EResult.OK:
            return _LoginAttemptResult(refresh_client, auth_source="refresh_token")

        errors.append("refresh-token logon failed: %s." % getattr(result, "name", result))
        self._safe_disconnect_client(refresh_client)
        return _LoginAttemptResult(None, error_message=" ".join(errors).strip())

    def _apply_desired_config(self, auth_source: str, *, mode: str) -> RuntimeStartResult:
        client = self._client
        if client is None:
            raise RuntimeError("Steam client session is not attached.")

        config, revision = self._clone_config(with_revision=True)
        effective_persona_state = _effective_persona_state(
            config.persona_state,
            config.appear_online,
        )
        persona_state = getattr(EPersonaState, effective_persona_state, None)
        if persona_state is not None:
            client.change_status(persona_state=persona_state)
        client.games_played(list(config.app_ids))
        self._maybe_persist_login_key(client)
        connected_at = _iso_timestamp() if mode in ("started", "resumed") or not self._connected_at else self._connected_at

        message = self._build_active_message(config, auth_source=auth_source, mode=mode)
        if config.custom_status:
            message = "%s Custom status stays stored locally for now." % message
        if config.auto_reply_enabled and config.auto_reply_message:
            message = (
                "%s Auto-reply is armed at %ss cooldown with a %ss session timeout."
                % (
                    message,
                    config.auto_reply_cooldown_seconds,
                    config.auto_reply_timeout_seconds,
                )
            )

        self._set_status(
            RuntimeState.BOOSTING,
            message,
            active_app_ids=list(config.app_ids),
            auth_source=auth_source,
            reconnect_attempts=self._reconnect_attempts,
            connected_at=connected_at,
            conflict_policy=config.conflict_policy,
            blocked_by_playing_session=False,
            blocked_app_id=0,
            last_error="",
        )
        with self._condition:
            self._applied_revision = revision
            self._awaiting_unblock_reapply = False
            self._yielded_to_new_session = False
            self._condition.notify_all()
            return RuntimeStartResult(
                active_app_ids=list(self._active_app_ids),
                message=self._message,
            )

    def _build_active_message(self, config: _LiveWorkerConfig, *, auth_source: str, mode: str) -> str:
        slot_count = len(config.app_ids)
        effective_persona_state = _effective_persona_state(
            config.persona_state,
            config.appear_online,
        )
        auth_label = {
            "refresh_token": "refresh token",
            "login_key": "login key",
            "preview": "preview state",
        }.get(auth_source, auth_source.replace("_", " ") or "session")

        if mode == "updated":
            prefix = "Live lane updated"
        elif mode == "resumed":
            prefix = "Steam client lane resumed after reconnect"
        else:
            prefix = "Steam client lane active"

        return "%s with %s slot%s via %s." % (
            prefix,
            slot_count,
            "" if slot_count == 1 else "s",
            auth_label,
        ) + " Presence is %s." % effective_persona_state.lower()

    def _set_status(
        self,
        state: RuntimeState,
        message: str,
        *,
        active_app_ids: Optional[List[int]] = None,
        auth_source: Optional[str] = None,
        reconnect_attempts: Optional[int] = None,
        connected_at: Optional[str] = None,
        conflict_policy: Optional[str] = None,
        blocked_by_playing_session: Optional[bool] = None,
        blocked_app_id: Optional[int] = None,
        last_error: Optional[str] = None,
    ) -> None:
        with self._condition:
            self._state = state
            self._message = message
            if active_app_ids is not None:
                self._active_app_ids = list(active_app_ids)
            if auth_source is not None:
                self._auth_source = auth_source
            if reconnect_attempts is not None:
                self._reconnect_attempts = reconnect_attempts
            if connected_at is not None:
                self._connected_at = connected_at
            if conflict_policy is not None:
                self._desired_config.conflict_policy = conflict_policy
            if blocked_by_playing_session is not None:
                self._blocked_by_playing_session = blocked_by_playing_session
            if blocked_app_id is not None:
                self._blocked_app_id = int(blocked_app_id)
            if last_error is not None:
                self._last_error = last_error
            self._updated_at = _iso_timestamp()
            self._condition.notify_all()

    def _maybe_persist_login_key(self, client: RefreshTokenSteamClient) -> None:
        login_key = str(getattr(client, "login_key", "") or "").strip()
        if not login_key or login_key == self._cached_login_key:
            return
        self._cached_login_key = login_key
        payload = {
            "account_name": self._desired_config.account_name,
            "steam_id": self._desired_config.steam_id,
            "login_key": login_key,
            "updated_at": _iso_timestamp(),
        }
        self._credential_dir.mkdir(parents=True, exist_ok=True)
        self._cache_path().write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _clear_cached_login_key(self) -> None:
        self._cached_login_key = ""
        path = self._cache_path()
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                data = {}
            data["login_key"] = ""
            data["updated_at"] = _iso_timestamp()
            try:
                path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            except Exception:
                pass

    def _load_cached_login_key(self) -> str:
        path = self._cache_path()
        if not path.exists():
            return ""
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return ""
        return str(data.get("login_key", "") or "").strip()

    def _cache_path(self) -> Path:
        return self._credential_dir / "client_auth.json"

    def _clone_config(self, *, with_revision: bool = False):
        with self._condition:
            config = _LiveWorkerConfig(
                profile_id=self._desired_config.profile_id,
                display_name=self._desired_config.display_name,
                account_name=self._desired_config.account_name,
                steam_id=self._desired_config.steam_id,
                refresh_token=self._desired_config.refresh_token,
                appear_online=self._desired_config.appear_online,
                persona_state=self._desired_config.persona_state,
                conflict_policy=self._desired_config.conflict_policy,
                custom_status=self._desired_config.custom_status,
                auto_reply_enabled=self._desired_config.auto_reply_enabled,
                auto_reply_message=self._desired_config.auto_reply_message,
                auto_reply_cooldown_seconds=self._desired_config.auto_reply_cooldown_seconds,
                auto_reply_timeout_seconds=self._desired_config.auto_reply_timeout_seconds,
                app_ids=list(self._desired_config.app_ids),
            )
            if with_revision:
                return config, self._desired_revision
            return config

    def _reconnect_delay_for_attempt(self, attempt_number: int) -> float:
        attempt = max(1, int(attempt_number))
        return min(self._reconnect_max_delay, float((2 ** min(attempt, 5)) - 1))

    @staticmethod
    def _safe_disconnect_client(client: Optional[RefreshTokenSteamClient]) -> None:
        if client is None:
            return
        try:
            if getattr(client, "logged_on", False):
                client.logout()
            elif getattr(client, "connected", False):
                client.disconnect()
        except Exception:
            pass

    def _attach_client_hooks(self, client: RefreshTokenSteamClient) -> None:
        if not hasattr(client, "on"):
            return
        client.on(EMsg.ClientPlayingSessionState, self._handle_playing_session_state)
        client.on("chat_message", self._handle_chat_message)

    def _handle_chat_message(self, user, message: str) -> None:
        config = self._clone_config()
        if not config.auto_reply_enabled:
            return

        with self._condition:
            if self._state not in (RuntimeState.STARTING, RuntimeState.BOOSTING, RuntimeState.PAUSED):
                return

        reply_message = str(config.auto_reply_message or "").strip()
        if not reply_message:
            return

        timeout_seconds = max(30, int(config.auto_reply_timeout_seconds))
        cooldown_seconds = max(15, int(config.auto_reply_cooldown_seconds))
        now = time.time()
        friend_id = int(getattr(user, "steam_id", 0) or 0)
        if friend_id <= 0:
            return

        with self._condition:
            conversation = self._chat_sessions.get(friend_id)
            if conversation is None or (now - conversation.last_incoming_at) > timeout_seconds:
                conversation = _AutoReplyConversation(
                    window_started_at=now,
                    last_incoming_at=now,
                )
                self._chat_sessions[friend_id] = conversation
            else:
                conversation.last_incoming_at = now

            self._auto_reply_last_message_at = _iso_timestamp()

            if (now - conversation.window_started_at) > timeout_seconds:
                self._updated_at = _iso_timestamp()
                self._condition.notify_all()
                return

            if conversation.last_reply_at and (now - conversation.last_reply_at) < cooldown_seconds:
                self._updated_at = _iso_timestamp()
                self._condition.notify_all()
                return

        try:
            user.send_message(reply_message)
        except Exception as exc:
            failure = str(exc).strip() or "Auto-reply dispatch failed."
            self._set_status(
                self._state,
                "%s Auto-reply failed." % self._message,
                active_app_ids=self.active_app_ids,
                auth_source=self._auth_source,
                reconnect_attempts=self._reconnect_attempts,
                connected_at=self._connected_at,
                conflict_policy=config.conflict_policy,
                blocked_by_playing_session=self._blocked_by_playing_session,
                blocked_app_id=self._blocked_app_id,
                last_error=failure,
            )
            return

        sender_name = str(getattr(user, "name", "") or getattr(user, "steam_id", "") or friend_id).strip()
        sent_timestamp = _iso_timestamp()
        with self._condition:
            conversation = self._chat_sessions.get(friend_id)
            if conversation is None:
                conversation = _AutoReplyConversation(
                    window_started_at=now,
                    last_incoming_at=now,
                )
                self._chat_sessions[friend_id] = conversation
            conversation.last_reply_at = now
            conversation.last_incoming_at = now
            self._auto_reply_sent_count += 1
            self._auto_reply_last_sent_at = sent_timestamp
            self._auto_reply_last_sender = sender_name
            self._last_error = ""
            self._message = (
                "Live lane active with auto-reply. Last reply sent to %s." % sender_name
            )
            self._updated_at = sent_timestamp
            self._condition.notify_all()

    def _handle_playing_session_state(self, message) -> None:
        body = getattr(message, "body", None)
        blocked = bool(getattr(body, "playing_blocked", False))
        app_id = int(getattr(body, "playing_app", 0) or 0)
        with self._condition:
            previously_blocked = self._blocked_by_playing_session
            self._blocked_by_playing_session = blocked
            self._blocked_app_id = app_id if blocked else 0
            if blocked:
                self._awaiting_unblock_reapply = not self._yielded_to_new_session
            elif previously_blocked:
                self._awaiting_unblock_reapply = not self._yielded_to_new_session
                self._last_kick_attempt_at = 0.0
            self._condition.notify_all()

    def _synchronize_playing_lock(self, client: RefreshTokenSteamClient) -> bool:
        config = self._clone_config()
        with self._condition:
            blocked = self._blocked_by_playing_session
            blocked_app_id = self._blocked_app_id

        if not blocked:
            return False

        message = self._build_conflict_message(config, blocked_app_id)
        if config.conflict_policy == CONFLICT_POLICY_KICK:
            last_error = ""
            now = time.time()
            if now - self._last_kick_attempt_at >= 5.0:
                try:
                    self._send_kick_playing_session(client)
                    self._last_kick_attempt_at = now
                    message = "%s Kick request sent to the blocking session." % message
                except Exception as exc:
                    last_error = str(exc).strip() or "Kick request failed."
                    message = "%s Kick request failed; retrying while the session remains blocked." % message
            else:
                message = "%s Waiting for Steam to release the lane after the kick request." % message
            self._set_status(
                RuntimeState.PAUSED,
                message,
                active_app_ids=list(config.app_ids),
                auth_source=self._auth_source or "refresh_token",
                reconnect_attempts=self._reconnect_attempts,
                connected_at=self._connected_at,
                conflict_policy=config.conflict_policy,
                blocked_by_playing_session=True,
                blocked_app_id=blocked_app_id,
                last_error=last_error,
            )
            client.sleep(self._sleep_interval)
            return True

        if config.conflict_policy == CONFLICT_POLICY_YIELD:
            last_error = ""
            if not self._yielded_to_new_session:
                try:
                    client.games_played([])
                except Exception as exc:
                    last_error = str(exc).strip() or "Unable to clear the played-state lane."
                self._yielded_to_new_session = True
            self._set_status(
                RuntimeState.IDLE,
                "%s Yield policy stood this lane down for the newer Steam session. Start it again when you want to resume boosting." % message,
                active_app_ids=[],
                auth_source=self._auth_source or "refresh_token",
                reconnect_attempts=self._reconnect_attempts,
                connected_at=self._connected_at,
                conflict_policy=config.conflict_policy,
                blocked_by_playing_session=blocked,
                blocked_app_id=blocked_app_id,
                last_error=last_error,
            )
            client.sleep(self._sleep_interval)
            return True

        self._set_status(
            RuntimeState.PAUSED,
            "%s Pause policy is holding this lane until the other session ends." % message,
            active_app_ids=list(config.app_ids),
            auth_source=self._auth_source or "refresh_token",
            reconnect_attempts=self._reconnect_attempts,
            connected_at=self._connected_at,
            conflict_policy=config.conflict_policy,
            blocked_by_playing_session=True,
            blocked_app_id=blocked_app_id,
            last_error="",
        )
        client.sleep(self._sleep_interval)
        return True

    @staticmethod
    def _send_kick_playing_session(client: RefreshTokenSteamClient) -> None:
        message = MsgProto(EMsg.ClientKickPlayingSession)
        if hasattr(message.body, "only_stop_game"):
            message.body.only_stop_game = False
        client.send(message)

    @staticmethod
    def _build_conflict_message(config: _LiveWorkerConfig, blocked_app_id: int) -> str:
        app_fragment = (
            " on app %s" % blocked_app_id
            if blocked_app_id > 0
            else ""
        )
        return "Playing session conflict detected%s for %s." % (app_fragment, config.display_name)


class SteamNetworkBoosterRuntime:
    name = "valvepython-steam"
    preview_mode = False

    def __init__(
        self,
        *,
        session_store: SessionStore,
        client_factory: Optional[Callable[[], RefreshTokenSteamClient]] = None,
        start_timeout: float = 20.0,
        sleep_interval: float = 1.0,
        reconnect_max_delay: float = 30.0,
        reconfigure_timeout: float = 6.0,
    ) -> None:
        if RefreshTokenSteamClient is None:
            raise RuntimeError("The steam client protocol package is not installed.")
        self._session_store = session_store
        self._client_factory = client_factory or RefreshTokenSteamClient
        self._start_timeout = max(5.0, float(start_timeout))
        self._sleep_interval = max(0.05, float(sleep_interval))
        self._reconnect_max_delay = max(0.1, float(reconnect_max_delay))
        self._reconfigure_timeout = max(1.0, float(reconfigure_timeout))
        self._workers: Dict[str, _LiveBoostWorker] = {}
        self._credential_root = Path(self._session_store.base_dir) / "cm_credentials"

    def start(self, account: AccountProfile, session_bundle: Dict[str, object]) -> RuntimeStartResult:
        config = self._build_worker_config(account, session_bundle)
        if account.profile_id in self._workers:
            self.stop(account.profile_id)

        worker = _LiveBoostWorker(
            config=config,
            client_factory=self._client_factory,
            credential_dir=self._credential_root / account.profile_id,
            sleep_interval=self._sleep_interval,
            reconnect_max_delay=self._reconnect_max_delay,
        )
        self._workers[account.profile_id] = worker
        try:
            return worker.start_and_wait(timeout=self._start_timeout)
        except Exception:
            self._workers.pop(account.profile_id, None)
            raise

    def stop(self, profile_id: str) -> str:
        worker = self._workers.pop(profile_id, None)
        if worker is None:
            return "Boost lane was not running."
        message = worker.stop_and_wait(timeout=8.0)
        return message if message else "Boost lane stopped."

    def reconfigure(self, account: AccountProfile, session_bundle: Dict[str, object]) -> RuntimeStartResult:
        worker = self._workers.get(account.profile_id)
        if worker is None:
            raise RuntimeError("Boost lane is not running.")
        config = self._build_worker_config(account, session_bundle)
        return worker.reconfigure_and_wait(config, timeout=self._reconfigure_timeout)

    def inspect(self) -> Dict[str, RuntimeLaneTelemetry]:
        return {
            profile_id: worker.snapshot()
            for profile_id, worker in self._workers.items()
        }

    def shutdown(self) -> None:
        for profile_id in list(self._workers.keys()):
            self.stop(profile_id)

    @staticmethod
    def _build_worker_config(account: AccountProfile, session_bundle: Dict[str, object]) -> _LiveWorkerConfig:
        refresh_token = str(session_bundle.get("refresh_token", "") or "").strip()
        steam_id = str(session_bundle.get("steam_id", "") or account.steam_id or "").strip()
        if not refresh_token:
            raise RuntimeError("Saved session bundle does not contain a Steam refresh token.")
        if not steam_id:
            raise RuntimeError("Saved session bundle does not include a SteamID.")
        if not _refresh_token_is_client_usable(refresh_token):
            raise RuntimeError("The saved refresh token is not valid for Steam client logon.")

        return _LiveWorkerConfig(
            profile_id=account.profile_id,
            display_name=account.display_name,
            account_name=account.account_name,
            steam_id=steam_id,
            refresh_token=refresh_token,
            appear_online=account.appear_online,
            persona_state=account.persona_state,
            conflict_policy=account.conflict_policy,
            custom_status=account.custom_status,
            auto_reply_enabled=account.auto_reply_enabled,
            auto_reply_message=account.auto_reply_message,
            auto_reply_cooldown_seconds=max(15, int(account.auto_reply_cooldown_seconds)),
            auto_reply_timeout_seconds=max(30, int(account.auto_reply_timeout_seconds)),
            app_ids=[int(game.app_id) for game in account.games if game.enabled],
        )


def build_default_transport(session_store: SessionStore) -> BoosterRuntime:
    if RefreshTokenSteamClient is not None:
        return SteamNetworkBoosterRuntime(session_store=session_store)
    return PreviewBoosterRuntime()


class RuntimeController:
    def __init__(
        self,
        *,
        session_store: Optional[SessionStore] = None,
        transport: Optional[BoosterRuntime] = None,
        owned_games_validator: Optional[OwnedGamesValidator] = None,
        event_limit: int = 60,
        event_log_path: Optional[Path] = None,
    ) -> None:
        self._session_store = session_store or SessionStore()
        self._transport = transport or build_default_transport(self._session_store)
        self._owned_games_validator = owned_games_validator or OwnedGamesValidator()
        self._event_limit = max(10, int(event_limit))
        default_event_log_path = Path(self._session_store.base_dir).parent / "logs" / "runtime.log"
        self._event_log_path = Path(event_log_path) if event_log_path is not None else default_event_log_path
        self._event_log_lock = threading.Lock()
        self._snapshot = RuntimeSnapshot(
            transport_name=self._transport.name,
            preview_mode=bool(self._transport.preview_mode),
            event_log_path=str(self._event_log_path),
            updated_at=_iso_timestamp(),
        )
        self._log_event("Runtime controller initialized with %s transport." % self._transport.name)

    def snapshot(self) -> RuntimeSnapshot:
        self._sync_transport_status()
        return self._snapshot

    def refresh_accounts(self, accounts: List[AccountProfile], *, reason: str = "") -> RuntimeSnapshot:
        known_ids = set()
        for account in accounts:
            known_ids.add(account.profile_id)
            self._sync_account_status(account)

        stale_ids = [profile_id for profile_id in self._snapshot.accounts if profile_id not in known_ids]
        for profile_id in stale_ids:
            stale_status = self._snapshot.accounts.pop(profile_id, None)
            if stale_status and stale_status.state in (RuntimeState.STARTING, RuntimeState.BOOSTING, RuntimeState.PAUSED):
                try:
                    self._transport.stop(profile_id)
                except Exception:
                    pass
            self._log_event("Removed runtime lane for %s." % profile_id)

        self._sync_transport_status()
        self._touch_snapshot()
        if reason:
            tracked_count = len(self._snapshot.accounts)
            self._log_event(
                "Runtime readiness refreshed for %s account%s."
                % (tracked_count, "" if tracked_count == 1 else "s")
            )
        return self._snapshot

    def start_profile(self, profile_id: str, accounts: List[AccountProfile]) -> AccountRuntimeStatus:
        self.refresh_accounts(accounts)
        account = self._find_account(profile_id, accounts)
        if account is None:
            raise KeyError("That account profile was not found.")

        status = self._snapshot.accounts[profile_id]
        if status.state in (RuntimeState.STARTING, RuntimeState.BOOSTING, RuntimeState.PAUSED):
            return status

        if not status.session_ready:
            status.state = RuntimeState.ERROR
            status.message = "Saved session bundle missing or incomplete."
            status.updated_at = _iso_timestamp()
            self._touch_snapshot()
            self._log_event("Unable to start %s because the saved session bundle is unavailable." % account.display_name)
            return status

        if not status.boost_enabled:
            status.state = RuntimeState.IDLE
            status.message = "Booster is disabled for this account."
            status.updated_at = _iso_timestamp()
            self._touch_snapshot()
            self._log_event("Skipped starting %s because the booster is disabled." % account.display_name)
            return status

        if not status.configured_app_ids:
            status.state = RuntimeState.IDLE
            status.message = "No game slots configured."
            status.updated_at = _iso_timestamp()
            self._touch_snapshot()
            self._log_event("Unable to start %s because no game slots are configured." % account.display_name)
            return status

        if status.owned_games_validation_state == "invalid":
            status.state = RuntimeState.ERROR
            status.message = (
                "Runtime start blocked: %s"
                % (status.owned_games_validation_message or "Configured app IDs failed owned-game validation.")
            )
            status.updated_at = _iso_timestamp()
            self._touch_snapshot()
            self._log_event("Unable to start %s because owned-game validation failed." % account.display_name)
            return status

        if not status.runtime_ready:
            status.state = RuntimeState.NEEDS_AUTH
            status.message = status.runtime_auth_message or "Steam client authorization is still required for boosting."
            status.updated_at = _iso_timestamp()
            self._touch_snapshot()
            self._log_event(
                "Skipped starting %s because Steam client authorization is not ready."
                % account.display_name
            )
            return status

        session_bundle = self._load_session_bundle(account)
        status.state = RuntimeState.STARTING
        status.message = "Preparing boost lane."
        status.updated_at = _iso_timestamp()
        self._touch_snapshot()

        try:
            result = self._transport.start(account, session_bundle or {})
            self._sync_transport_status()
            status = self._snapshot.accounts[profile_id]
            status.state = RuntimeState.BOOSTING
            status.active_app_ids = list(result.active_app_ids)
            status.message = result.message
            status.updated_at = _iso_timestamp()
            self._touch_snapshot()
            self._log_event(
                "Started %s with %s slot%s."
                % (
                    account.display_name,
                    len(status.active_app_ids),
                    "" if len(status.active_app_ids) == 1 else "s",
                )
            )
            return status
        except Exception as exc:
            status.state = RuntimeState.ERROR
            status.active_app_ids = []
            status.message = "Runtime start failed: %s" % (str(exc).strip() or "Unknown error.")
            status.updated_at = _iso_timestamp()
            self._touch_snapshot()
            self._log_event("Runtime start failed for %s." % account.display_name)
            return status

    def stop_profile(self, profile_id: str, accounts: List[AccountProfile]) -> AccountRuntimeStatus:
        self.refresh_accounts(accounts)
        account = self._find_account(profile_id, accounts)
        if account is None:
            raise KeyError("That account profile was not found.")

        status = self._snapshot.accounts[profile_id]
        if status.state not in (RuntimeState.STARTING, RuntimeState.BOOSTING, RuntimeState.PAUSED):
            return status

        try:
            stop_message = self._transport.stop(profile_id)
        except Exception as exc:
            status.state = RuntimeState.ERROR
            status.active_app_ids = []
            status.message = "Runtime stop failed: %s" % (str(exc).strip() or "Unknown error.")
            status.updated_at = _iso_timestamp()
            self._touch_snapshot()
            self._log_event("Runtime stop failed for %s." % account.display_name)
            return status

        status.active_app_ids = []
        status.auto_reply_sent_count = 0
        status.auto_reply_last_sent_at = ""
        status.auto_reply_last_sender = ""
        status.auto_reply_last_message_at = ""
        status.auth_source = ""
        status.reconnect_attempts = 0
        status.connected_at = ""
        status.blocked_by_playing_session = False
        status.blocked_app_id = 0
        status.last_error = ""
        self._apply_ready_state(status)
        if stop_message:
            if status.state == RuntimeState.READY:
                status.message = "%s %s" % (stop_message, self._ready_message_for(status))
            else:
                status.message = "%s %s" % (stop_message, status.message)
        status.updated_at = _iso_timestamp()
        self._touch_snapshot()
        self._log_event("Stopped %s." % account.display_name)
        return status

    def reconfigure_profile(self, profile_id: str, accounts: List[AccountProfile]) -> AccountRuntimeStatus:
        self.refresh_accounts(accounts)
        account = self._find_account(profile_id, accounts)
        if account is None:
            raise KeyError("That account profile was not found.")

        status = self._snapshot.accounts[profile_id]
        if status.state not in (RuntimeState.STARTING, RuntimeState.BOOSTING, RuntimeState.PAUSED):
            return status

        if not status.session_ready:
            status.state = RuntimeState.ERROR
            status.message = "Saved session bundle missing or incomplete."
            status.updated_at = _iso_timestamp()
            self._touch_snapshot()
            return status

        session_bundle = self._load_session_bundle(account)
        try:
            result = self._transport.reconfigure(account, session_bundle or {})
            self._sync_transport_status()
            status = self._snapshot.accounts[profile_id]
            if result.active_app_ids:
                status.active_app_ids = list(result.active_app_ids)
            status.message = result.message
            status.updated_at = _iso_timestamp()
            self._touch_snapshot()
            self._log_event("Updated live lane for %s." % account.display_name)
            return status
        except Exception as exc:
            status.state = RuntimeState.ERROR
            status.message = "Runtime update failed: %s" % (str(exc).strip() or "Unknown error.")
            status.updated_at = _iso_timestamp()
            self._touch_snapshot()
            self._log_event("Runtime update failed for %s." % account.display_name)
            return status

    def start_all(self, accounts: List[AccountProfile]) -> Dict[str, int]:
        self.refresh_accounts(accounts)
        started = 0
        skipped = 0
        failed = 0
        for account in accounts:
            status = self._snapshot.accounts.get(account.profile_id)
            if status is None:
                skipped += 1
                continue
            if status.state in (RuntimeState.STARTING, RuntimeState.BOOSTING, RuntimeState.PAUSED):
                skipped += 1
                continue
            if status.state == RuntimeState.ERROR:
                failed += 1
                continue
            if not status.boost_enabled:
                skipped += 1
                continue
            if status.state != RuntimeState.READY:
                skipped += 1
                continue
            result = self.start_profile(account.profile_id, accounts)
            if result.state == RuntimeState.BOOSTING:
                started += 1
            elif result.state == RuntimeState.ERROR:
                failed += 1
            else:
                skipped += 1
        return {
            "started": started,
            "skipped": skipped,
            "failed": failed,
        }

    def stop_all(self, accounts: List[AccountProfile]) -> Dict[str, int]:
        self.refresh_accounts(accounts)
        stopped = 0
        for account in accounts:
            status = self._snapshot.accounts.get(account.profile_id)
            if status is None or status.state not in (RuntimeState.STARTING, RuntimeState.BOOSTING, RuntimeState.PAUSED):
                continue
            self.stop_profile(account.profile_id, accounts)
            stopped += 1
        return {
            "stopped": stopped,
        }

    def shutdown(self, accounts: List[AccountProfile]) -> None:
        self.stop_all(accounts)
        self._transport.shutdown()

    def _sync_account_status(self, account: AccountProfile) -> None:
        status = self._snapshot.accounts.get(account.profile_id)
        if status is None:
            status = AccountRuntimeStatus(
                profile_id=account.profile_id,
                display_name=account.display_name,
            )
        was_active = status.state in (RuntimeState.STARTING, RuntimeState.BOOSTING, RuntimeState.PAUSED)

        status.display_name = account.display_name
        status.login_mode = account.login_mode
        status.boost_enabled = account.boost_enabled
        status.appear_online = account.appear_online
        status.persona_state = account.persona_state
        status.effective_persona_state = _effective_persona_state(
            account.persona_state,
            account.appear_online,
        )
        status.conflict_policy = account.conflict_policy
        status.custom_status = account.custom_status
        status.auto_reply_enabled = account.auto_reply_enabled
        status.auto_reply_message = account.auto_reply_message
        status.auto_reply_cooldown_seconds = max(15, int(account.auto_reply_cooldown_seconds))
        status.auto_reply_timeout_seconds = max(30, int(account.auto_reply_timeout_seconds))
        status.session_bundle_path = str(account.session_bundle_path or "")
        status.configured_app_ids = [int(game.app_id) for game in account.games if game.enabled]
        status.session_ready = self._has_saved_session_bundle(account)
        status.runtime_ready = self._has_runtime_auth(account)
        status.runtime_auth_message = self._runtime_auth_message(account)
        self._apply_owned_games_validation(
            status,
            self._owned_games_validator.validate_account(account),
        )

        if was_active:
            if not status.session_ready:
                status.state = RuntimeState.ERROR
                status.active_app_ids = []
                status.blocked_by_playing_session = False
                status.blocked_app_id = 0
                status.message = "Saved session bundle disappeared while the boost lane was active."
            elif not status.configured_app_ids:
                status.state = RuntimeState.PAUSED
                status.active_app_ids = []
                status.blocked_by_playing_session = False
                status.blocked_app_id = 0
                status.message = "No configured slots remain for this active lane."
            elif status.owned_games_validation_state == "invalid":
                try:
                    self._transport.stop(account.profile_id)
                except Exception:
                    pass
                status.state = RuntimeState.ERROR
                status.active_app_ids = []
                status.blocked_by_playing_session = False
                status.blocked_app_id = 0
                status.last_error = status.owned_games_validation_message
                status.message = "Active lane stopped because configured app IDs failed owned-game validation."
                self._log_event(
                    "Stopped %s because owned-game validation rejected the configured app IDs."
                    % account.display_name
                )
        else:
            status.active_app_ids = []
            status.auto_reply_sent_count = 0
            status.auto_reply_last_sent_at = ""
            status.auto_reply_last_sender = ""
            status.auto_reply_last_message_at = ""
            status.auth_source = ""
            status.reconnect_attempts = 0
            status.connected_at = ""
            status.blocked_by_playing_session = False
            status.blocked_app_id = 0
            status.last_error = ""
            self._apply_ready_state(status)

        status.updated_at = _iso_timestamp()
        self._snapshot.accounts[account.profile_id] = status

    def _apply_ready_state(self, status: AccountRuntimeStatus) -> None:
        if not status.session_ready:
            status.state = RuntimeState.ERROR
            status.message = "Saved session bundle missing or incomplete."
            return
        if not status.boost_enabled:
            status.state = RuntimeState.IDLE
            status.message = "Booster is disabled for this account."
            return
        if not status.configured_app_ids:
            status.state = RuntimeState.IDLE
            status.message = "No game slots configured."
            return
        if not status.runtime_ready:
            status.state = RuntimeState.NEEDS_AUTH
            status.message = status.runtime_auth_message or "Steam client authorization is still required for boosting."
            return
        if status.owned_games_validation_state == "invalid":
            status.state = RuntimeState.ERROR
            status.last_error = status.owned_games_validation_message
            status.message = "Owned-game validation blocked this lane. Review the configured app IDs."
            return
        status.state = RuntimeState.READY
        status.message = self._ready_message_for(status)

    @staticmethod
    def _ready_message_for(status: AccountRuntimeStatus) -> str:
        slot_count = len(status.configured_app_ids)
        base = "Ready to start with %s configured slot%s." % (slot_count, "" if slot_count == 1 else "s")
        if status.owned_games_validation_state == "valid":
            return "%s Owned-game validation passed." % base
        if status.owned_games_validation_state == "unavailable":
            return "%s Owned-game validation is unavailable for this session." % base
        return base

    @staticmethod
    def _apply_owned_games_validation(
        status: AccountRuntimeStatus,
        validation: OwnedGamesValidationResult,
    ) -> None:
        status.owned_games_validation_state = validation.state
        status.owned_games_validation_message = validation.message
        status.owned_games_validation_checked_at = validation.checked_at
        status.owned_games_api_key_available = bool(validation.api_key_available)
        status.owned_games_validated_app_ids = list(validation.validated_app_ids)
        status.owned_games_missing_app_ids = list(validation.missing_app_ids)
        status.owned_games_matched_titles = dict(validation.matched_titles)

    def _has_saved_session_bundle(self, account: AccountProfile) -> bool:
        bundle = self._load_session_bundle(account)
        if not bundle:
            return False

        steam_id = str(bundle.get("steam_id", "") or "").strip()
        if not steam_id:
            return False

        return True

    def _has_runtime_auth(self, account: AccountProfile) -> bool:
        bundle = self._load_session_bundle(account)
        if not bundle:
            return False

        if self._transport.preview_mode:
            return True

        refresh_token = str(bundle.get("refresh_token", "") or "").strip()
        if refresh_token and _refresh_token_is_client_usable(refresh_token):
            return True

        return self._has_cached_login_key(account)

    def _runtime_auth_message(self, account: AccountProfile) -> str:
        if self._transport.preview_mode:
            return ""

        bundle = self._load_session_bundle(account)
        if not bundle:
            return "Saved session bundle missing or incomplete."

        if self._has_cached_login_key(account):
            return "Saved Steam client login key is ready."

        refresh_token = str(bundle.get("refresh_token", "") or "").strip()
        if refresh_token and _refresh_token_is_client_usable(refresh_token):
            return "Saved Steam client refresh token is ready."

        if refresh_token:
            return (
                "Saved web session is ready, but Steam client authorization is still required before this account can boost."
            )

        return "Saved session bundle is missing a Steam client authorization path."

    def _has_cached_login_key(self, account: AccountProfile) -> bool:
        cache = self._session_store.load_client_auth_cache(account.profile_id)
        login_key = str(cache.get("login_key", "") or "").strip()
        if not login_key:
            return False

        cached_account_name = str(cache.get("account_name", "") or "").strip()
        cached_steam_id = str(cache.get("steam_id", "") or "").strip()
        if account.account_name and cached_account_name and cached_account_name != account.account_name:
            return False
        if account.steam_id and cached_steam_id and cached_steam_id != account.steam_id:
            return False
        return True

    def _load_session_bundle(self, account: AccountProfile) -> Optional[Dict[str, object]]:
        if not account.session_bundle_path:
            return None
        return self._session_store.load_bundle_path(str(account.session_bundle_path))

    @staticmethod
    def _find_account(profile_id: str, accounts: List[AccountProfile]) -> Optional[AccountProfile]:
        for account in accounts:
            if account.profile_id == profile_id:
                return account
        return None

    def _sync_transport_status(self) -> None:
        live_statuses = self._transport.inspect()
        for profile_id, telemetry in live_statuses.items():
            status = self._snapshot.accounts.get(profile_id)
            if status is None:
                continue
            previous_state = status.state
            previous_blocked = status.blocked_by_playing_session
            previous_reconnect_attempts = status.reconnect_attempts
            previous_active_slot_count = len(status.active_app_ids)
            previous_last_error = status.last_error
            status.state = telemetry.state
            status.active_app_ids = list(telemetry.active_app_ids)
            status.auth_source = telemetry.auth_source
            status.reconnect_attempts = telemetry.reconnect_attempts
            status.connected_at = telemetry.connected_at
            status.conflict_policy = telemetry.conflict_policy or status.conflict_policy
            status.auto_reply_enabled = telemetry.auto_reply_enabled
            status.auto_reply_message = telemetry.auto_reply_message
            status.auto_reply_cooldown_seconds = telemetry.auto_reply_cooldown_seconds
            status.auto_reply_timeout_seconds = telemetry.auto_reply_timeout_seconds
            status.auto_reply_sent_count = telemetry.auto_reply_sent_count
            status.auto_reply_last_sent_at = telemetry.auto_reply_last_sent_at
            status.auto_reply_last_sender = telemetry.auto_reply_last_sender
            status.auto_reply_last_message_at = telemetry.auto_reply_last_message_at
            status.blocked_by_playing_session = telemetry.blocked_by_playing_session
            status.blocked_app_id = telemetry.blocked_app_id
            status.last_error = telemetry.last_error
            status.message = telemetry.message or status.message
            status.updated_at = telemetry.updated_at or _iso_timestamp()
            self._log_transport_transition(
                status=status,
                previous_state=previous_state,
                previous_blocked=previous_blocked,
                previous_reconnect_attempts=previous_reconnect_attempts,
                previous_active_slot_count=previous_active_slot_count,
                previous_last_error=previous_last_error,
            )
        self._touch_snapshot()

    def _touch_snapshot(self) -> None:
        self._snapshot.transport_name = self._transport.name
        self._snapshot.preview_mode = bool(self._transport.preview_mode)
        self._snapshot.event_log_path = str(self._event_log_path)
        self._snapshot.updated_at = _iso_timestamp()

    def _log_event(self, message: str) -> None:
        short_timestamp = datetime.now().strftime("%H:%M:%S")
        full_timestamp = datetime.now().isoformat(timespec="seconds")
        self._snapshot.recent_events.append("[%s] %s" % (short_timestamp, message))
        self._snapshot.recent_events = self._snapshot.recent_events[-self._event_limit :]
        self._snapshot.event_count += 1
        self._append_event_to_log("[%s] %s" % (full_timestamp, message))
        self._touch_snapshot()

    def _append_event_to_log(self, line: str) -> None:
        self._event_log_path.parent.mkdir(parents=True, exist_ok=True)
        with self._event_log_lock:
            with self._event_log_path.open("a", encoding="utf-8") as handle:
                handle.write("%s\n" % line)

    def _log_transport_transition(
        self,
        *,
        status: AccountRuntimeStatus,
        previous_state: RuntimeState,
        previous_blocked: bool,
        previous_reconnect_attempts: int,
        previous_active_slot_count: int,
        previous_last_error: str,
    ) -> None:
        if status.state != previous_state:
            self._log_event(
                "%s transitioned to %s."
                % (status.display_name, status.state.value.replace("_", " "))
            )

        if status.blocked_by_playing_session and not previous_blocked:
            blocked_fragment = (
                " on app %s" % status.blocked_app_id
                if status.blocked_app_id > 0
                else ""
            )
            self._log_event(
                "Playing session conflict detected for %s%s."
                % (status.display_name, blocked_fragment)
            )
        elif previous_blocked and not status.blocked_by_playing_session:
            self._log_event("Playing session conflict cleared for %s." % status.display_name)

        if status.reconnect_attempts > previous_reconnect_attempts:
            self._log_event(
                "%s queued reconnect attempt %s."
                % (status.display_name, status.reconnect_attempts)
            )

        if status.last_error and status.last_error != previous_last_error:
            self._log_event("%s reported: %s" % (status.display_name, status.last_error))

        if len(status.active_app_ids) != previous_active_slot_count and status.active_app_ids:
            self._log_event(
                "%s now has %s active slot%s."
                % (
                    status.display_name,
                    len(status.active_app_ids),
                    "" if len(status.active_app_ids) == 1 else "s",
                )
            )
