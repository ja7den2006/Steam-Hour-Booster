from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional, Protocol

from steam_hour_booster.models import AccountProfile
from steam_hour_booster.session_store import SessionStore


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


class RuntimeController:
    def __init__(
        self,
        *,
        session_store: Optional[SessionStore] = None,
        transport: Optional[BoosterRuntime] = None,
        event_limit: int = 60,
    ) -> None:
        self._session_store = session_store or SessionStore()
        self._transport = transport or PreviewBoosterRuntime()
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
