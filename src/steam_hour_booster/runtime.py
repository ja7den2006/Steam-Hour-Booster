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

from steam_hour_booster.models import AccountProfile
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


class RuntimeState(str, Enum):
    IDLE = "idle"
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
    login_mode: str = ""
    persona_state: str = "Online"
    session_bundle_path: str = ""
    configured_app_ids: List[int] = field(default_factory=list)
    active_app_ids: List[int] = field(default_factory=list)
    custom_status: str = ""
    message: str = ""
    updated_at: str = ""

    def to_dict(self) -> Dict[str, object]:
        can_start = self.session_ready and bool(self.configured_app_ids) and self.state != RuntimeState.BOOSTING
        can_stop = self.state in (RuntimeState.STARTING, RuntimeState.BOOSTING, RuntimeState.PAUSED)
        return {
            "profile_id": self.profile_id,
            "display_name": self.display_name,
            "state": self.state.value,
            "state_label": self.state.value.replace("_", " ").title(),
            "session_ready": self.session_ready,
            "login_mode": self.login_mode,
            "persona_state": self.persona_state,
            "session_bundle_path": self.session_bundle_path,
            "configured_app_ids": list(self.configured_app_ids),
            "configured_slot_count": len(self.configured_app_ids),
            "active_app_ids": list(self.active_app_ids),
            "active_slot_count": len(self.active_app_ids),
            "custom_status": self.custom_status,
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
        boosting_count = sum(1 for status in self.accounts.values() if status.state == RuntimeState.BOOSTING)
        error_count = sum(1 for status in self.accounts.values() if status.state == RuntimeState.ERROR)
        active_slot_count = sum(len(status.active_app_ids) for status in self.accounts.values())
        return {
            "transport_name": self.transport_name,
            "preview_mode": self.preview_mode,
            "updated_at": self.updated_at,
            "counts": {
                "tracked_accounts": len(statuses),
                "ready_accounts": ready_count,
                "boosting_accounts": boosting_count,
                "error_accounts": error_count,
                "active_slots": active_slot_count,
            },
            "statuses": statuses,
            "recent_events": list(self.recent_events),
        }


@dataclass
class RuntimeStartResult:
    active_app_ids: List[int]
    message: str


class BoosterRuntime(Protocol):
    name: str
    preview_mode: bool

    def start(self, account: AccountProfile, session_bundle: Dict[str, object]) -> RuntimeStartResult:
        ...

    def stop(self, profile_id: str) -> str:
        ...

    def shutdown(self) -> None:
        ...


class PreviewBoosterRuntime:
    name = "local-preview"
    preview_mode = True

    def __init__(self) -> None:
        self._active_profiles: Dict[str, List[int]] = {}

    def start(self, account: AccountProfile, session_bundle: Dict[str, object]) -> RuntimeStartResult:
        del session_bundle
        active_app_ids = [int(game.app_id) for game in account.games if game.enabled]
        self._active_profiles[account.profile_id] = list(active_app_ids)
        slot_count = len(active_app_ids)
        return RuntimeStartResult(
            active_app_ids=active_app_ids,
            message=(
                "Preview runtime armed with %s slot%s. "
                "Live Steam client transport is not attached yet."
            )
            % (slot_count, "" if slot_count == 1 else "s"),
        )

    def stop(self, profile_id: str) -> str:
        self._active_profiles.pop(profile_id, None)
        return "Boost lane stopped."

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
                message.body.obfuscated_private_ip.v4 = (
                    ip4_to_int(self.connection.local_address) ^ 0xF00DBAAD
                )
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
    persona_state: str
    custom_status: str
    app_ids: List[int]


class _LiveBoostWorker:
    def __init__(
        self,
        *,
        config: _LiveWorkerConfig,
        client_factory: Callable[[], RefreshTokenSteamClient],
        credential_dir: Path,
        sleep_interval: float = 1.0,
    ) -> None:
        self._config = config
        self._client_factory = client_factory
        self._credential_dir = credential_dir
        self._sleep_interval = max(0.2, float(sleep_interval))
        self._client = None
        self._thread = None
        self._stop_event = threading.Event()
        self._started_event = threading.Event()
        self._stopped_event = threading.Event()
        self._active_app_ids: List[int] = []
        self._message = "Boost lane not started."
        self._error_message = ""

    @property
    def active_app_ids(self) -> List[int]:
        return list(self._active_app_ids)

    def start_and_wait(self, timeout: float) -> RuntimeStartResult:
        if self._thread is not None:
            raise RuntimeError("Boost worker already started.")

        self._thread = threading.Thread(
            target=self._run,
            name="steam-hour-booster-%s" % self._config.profile_id,
            daemon=True,
        )
        self._thread.start()

        if not self._started_event.wait(max(timeout, 1.0)):
            self.stop_and_wait(timeout=5.0)
            raise RuntimeError("Timed out waiting for the Steam client session to start.")

        if self._error_message:
            raise RuntimeError(self._error_message)

        return RuntimeStartResult(
            active_app_ids=self.active_app_ids,
            message=self._message,
        )

    def stop_and_wait(self, timeout: float = 10.0) -> str:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(max(timeout, 0.5))
        return self._message or "Boost lane stopped."

    def _run(self) -> None:
        try:
            self._credential_dir.mkdir(parents=True, exist_ok=True)
            client = self._client_factory()
            self._client = client
            client.set_credential_location(str(self._credential_dir))

            result = client.login_with_refresh_token(
                self._config.refresh_token,
                self._config.steam_id,
                account_name=self._config.account_name,
            )
            if result != EResult.OK:
                self._error_message = "Steam client logon failed: %s." % getattr(result, "name", result)
                self._message = self._error_message
                return

            persona_state = getattr(EPersonaState, self._config.persona_state, None)
            if persona_state is not None:
                client.change_status(persona_state=persona_state)
            client.games_played(list(self._config.app_ids))
            self._active_app_ids = list(self._config.app_ids)
            self._message = (
                "Steam client lane active with %s slot%s."
                % (len(self._active_app_ids), "" if len(self._active_app_ids) == 1 else "s")
            )
            self._started_event.set()

            while not self._stop_event.is_set():
                if not client.connected or not client.logged_on:
                    self._error_message = "Steam client session disconnected."
                    self._message = self._error_message
                    return
                client.sleep(self._sleep_interval)
        except Exception as exc:
            self._error_message = str(exc).strip() or "Unexpected Steam client runtime failure."
            self._message = self._error_message
        finally:
            if not self._started_event.is_set():
                self._started_event.set()
            try:
                if self._client is not None:
                    if self._client.logged_on:
                        self._client.logout()
                    elif self._client.connected:
                        self._client.disconnect()
            except Exception:
                pass
            self._active_app_ids = []
            self._stopped_event.set()


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
    ) -> None:
        if RefreshTokenSteamClient is None:
            raise RuntimeError("The steam client protocol package is not installed.")
        self._session_store = session_store
        self._client_factory = client_factory or RefreshTokenSteamClient
        self._start_timeout = max(5.0, float(start_timeout))
        self._sleep_interval = max(0.2, float(sleep_interval))
        self._workers: Dict[str, _LiveBoostWorker] = {}
        self._credential_root = Path(self._session_store.base_dir) / "cm_credentials"

    def start(self, account: AccountProfile, session_bundle: Dict[str, object]) -> RuntimeStartResult:
        refresh_token = str(session_bundle.get("refresh_token", "") or "").strip()
        steam_id = str(session_bundle.get("steam_id", "") or account.steam_id or "").strip()
        if not refresh_token:
            raise RuntimeError("Saved session bundle does not contain a Steam refresh token.")
        if not steam_id:
            raise RuntimeError("Saved session bundle does not include a SteamID.")
        if not _refresh_token_is_client_usable(refresh_token):
            raise RuntimeError("The saved refresh token is not valid for Steam client logon.")

        if account.profile_id in self._workers:
            self.stop(account.profile_id)

        worker = _LiveBoostWorker(
            config=_LiveWorkerConfig(
                profile_id=account.profile_id,
                display_name=account.display_name,
                account_name=account.account_name,
                steam_id=steam_id,
                refresh_token=refresh_token,
                persona_state=account.persona_state,
                custom_status=account.custom_status,
                app_ids=[int(game.app_id) for game in account.games if game.enabled],
            ),
            client_factory=self._client_factory,
            credential_dir=self._credential_root / account.profile_id,
            sleep_interval=self._sleep_interval,
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

    def shutdown(self) -> None:
        for profile_id in list(self._workers.keys()):
            self.stop(profile_id)


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
        event_limit: int = 60,
    ) -> None:
        self._session_store = session_store or SessionStore()
        self._transport = transport or build_default_transport(self._session_store)
        self._event_limit = max(10, int(event_limit))
        self._snapshot = RuntimeSnapshot(
            transport_name=self._transport.name,
            preview_mode=bool(self._transport.preview_mode),
            updated_at=self._iso_timestamp(),
        )
        self._log_event(
            "Runtime controller initialized with %s transport." % self._transport.name
        )

    def snapshot(self) -> RuntimeSnapshot:
        return self._snapshot

    def refresh_accounts(self, accounts: List[AccountProfile], *, reason: str = "") -> RuntimeSnapshot:
        known_ids = set()
        for account in accounts:
            known_ids.add(account.profile_id)
            self._sync_account_status(account)

        stale_ids = [profile_id for profile_id in self._snapshot.accounts if profile_id not in known_ids]
        for profile_id in stale_ids:
            stale_status = self._snapshot.accounts.pop(profile_id, None)
            if stale_status and stale_status.state == RuntimeState.BOOSTING:
                try:
                    self._transport.stop(profile_id)
                except Exception:
                    pass
            self._log_event("Removed runtime lane for %s." % profile_id)

        self._touch_snapshot()
        if reason:
            tracked_count = len(self._snapshot.accounts)
            self._log_event("Runtime readiness refreshed for %s account%s." % (tracked_count, "" if tracked_count == 1 else "s"))
        return self._snapshot

    def start_profile(self, profile_id: str, accounts: List[AccountProfile]) -> AccountRuntimeStatus:
        self.refresh_accounts(accounts)
        account = self._find_account(profile_id, accounts)
        if account is None:
            raise KeyError("That account profile was not found.")

        status = self._snapshot.accounts[profile_id]
        if not status.session_ready:
            status.state = RuntimeState.ERROR
            status.message = "Saved session bundle missing or incomplete."
            status.updated_at = self._iso_timestamp()
            self._touch_snapshot()
            self._log_event("Unable to start %s because the saved session bundle is unavailable." % account.display_name)
            return status

        if not status.configured_app_ids:
            status.state = RuntimeState.IDLE
            status.message = "No game slots configured."
            status.updated_at = self._iso_timestamp()
            self._touch_snapshot()
            self._log_event("Unable to start %s because no game slots are configured." % account.display_name)
            return status

        session_bundle = self._load_session_bundle(account)
        status.state = RuntimeState.STARTING
        status.message = "Preparing boost lane."
        status.updated_at = self._iso_timestamp()
        self._touch_snapshot()

        try:
            result = self._transport.start(account, session_bundle or {})
            status.state = RuntimeState.BOOSTING
            status.active_app_ids = list(result.active_app_ids)
            status.message = result.message
            status.updated_at = self._iso_timestamp()
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
            status.updated_at = self._iso_timestamp()
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
            status.updated_at = self._iso_timestamp()
            self._touch_snapshot()
            self._log_event("Runtime stop failed for %s." % account.display_name)
            return status

        status.active_app_ids = []
        self._apply_ready_state(status)
        if stop_message:
            status.message = "%s %s" % (stop_message, self._ready_message_for(status))
        status.updated_at = self._iso_timestamp()
        self._touch_snapshot()
        self._log_event("Stopped %s." % account.display_name)
        return status

    def start_all(self, accounts: List[AccountProfile]) -> Dict[str, int]:
        self.refresh_accounts(accounts)
        started = 0
        skipped = 0
        failed = 0
        for account in accounts:
            status = self._snapshot.accounts.get(account.profile_id)
            if status is None or not status.session_ready or not status.configured_app_ids:
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

        status.display_name = account.display_name
        status.login_mode = account.login_mode
        status.persona_state = account.persona_state
        status.custom_status = account.custom_status
        status.session_bundle_path = str(account.session_bundle_path or "")
        status.configured_app_ids = [int(game.app_id) for game in account.games if game.enabled]
        status.session_ready = self._has_valid_session_bundle(account)

        if status.state == RuntimeState.BOOSTING:
            if not status.session_ready:
                status.state = RuntimeState.ERROR
                status.active_app_ids = []
                status.message = "Saved session bundle disappeared while the boost lane was active."
            elif status.configured_app_ids and status.active_app_ids == status.configured_app_ids:
                status.message = "Boost lane active."
            elif status.configured_app_ids:
                status.message = "Boost lane active. Restart this lane to apply the updated slot set."
            else:
                status.message = "Boost lane active with a previous slot set. Add slots or stop the lane."
        else:
            status.active_app_ids = []
            self._apply_ready_state(status)

        status.updated_at = self._iso_timestamp()
        self._snapshot.accounts[account.profile_id] = status

    def _apply_ready_state(self, status: AccountRuntimeStatus) -> None:
        if not status.session_ready:
            status.state = RuntimeState.ERROR
            status.message = "Saved session bundle missing or incomplete."
            return
        if not status.configured_app_ids:
            status.state = RuntimeState.IDLE
            status.message = "No game slots configured."
            return
        status.state = RuntimeState.READY
        status.message = self._ready_message_for(status)

    @staticmethod
    def _ready_message_for(status: AccountRuntimeStatus) -> str:
        slot_count = len(status.configured_app_ids)
        return "Ready to start with %s configured slot%s." % (slot_count, "" if slot_count == 1 else "s")

    def _has_valid_session_bundle(self, account: AccountProfile) -> bool:
        bundle = self._load_session_bundle(account)
        return bool(bundle and str(bundle.get("steam_id", "")).strip())

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

    def _touch_snapshot(self) -> None:
        self._snapshot.transport_name = self._transport.name
        self._snapshot.preview_mode = bool(self._transport.preview_mode)
        self._snapshot.updated_at = self._iso_timestamp()

    def _log_event(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self._snapshot.recent_events.append("[%s] %s" % (timestamp, message))
        self._snapshot.recent_events = self._snapshot.recent_events[-self._event_limit :]
        self._touch_snapshot()

    @staticmethod
    def _iso_timestamp() -> str:
        return datetime.now().isoformat(timespec="seconds")
