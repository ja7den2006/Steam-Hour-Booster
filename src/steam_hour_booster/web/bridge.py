from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from uuid import uuid4

from steamcommunitykit.exceptions import (
    SteamAuthenticationError,
    SteamHTTPError,
    SteamNetworkError,
    SteamResponseError,
    SteamValidationError,
)
from steam_hour_booster.auth.community import AuthSession, PendingQRLogin, SteamCommunityAuthGateway
from steam_hour_booster.config_store import ConfigStore
from steam_hour_booster.models import (
    CONFLICT_POLICIES,
    CONFLICT_POLICY_KICK,
    CONFLICT_POLICY_PAUSE,
    CONFLICT_POLICY_YIELD,
    AccountProfile,
    AppConfig,
    IdleGame,
    VISIBLE_PERSONA_STATES,
)
from steam_hour_booster.runtime import RuntimeController, RuntimeState
from steam_hour_booster.session_store import SessionStore


@dataclass
class PendingQRLoginRecord:
    pending_id: str
    display_name: str
    pending: PendingQRLogin
    device_friendly_name: str
    poll_attempts: int = 0


PERSONA_STATES = list(VISIBLE_PERSONA_STATES)
ALL_PERSONA_STATES = PERSONA_STATES + ["Invisible", "Offline"]

CONFLICT_POLICY_OPTIONS = [
    {
        "value": CONFLICT_POLICY_PAUSE,
        "label": "Pause and Wait",
        "description": "Hold the lane until the other Steam playing session ends.",
    },
    {
        "value": CONFLICT_POLICY_KICK,
        "label": "Force Kick",
        "description": "Ask Steam to remove the other playing session and reclaim the lane.",
    },
    {
        "value": CONFLICT_POLICY_YIELD,
        "label": "Yield to New Session",
        "description": "Stand this lane down when another Steam playing session takes priority.",
    },
]

POPULAR_GAMES = [
    {"app_id": 730, "title": "Counter-Strike 2"},
    {"app_id": 570, "title": "Dota 2"},
    {"app_id": 252490, "title": "Rust"},
    {"app_id": 440, "title": "Team Fortress 2"},
    {"app_id": 578080, "title": "PUBG: BATTLEGROUNDS"},
    {"app_id": 4000, "title": "Garry's Mod"},
    {"app_id": 271590, "title": "Grand Theft Auto V"},
    {"app_id": 1172470, "title": "Apex Legends"},
]


class DesktopApi:
    def __init__(
        self,
        *,
        config_store: ConfigStore,
        config: AppConfig,
        auth_gateway: Optional[SteamCommunityAuthGateway] = None,
        session_store: Optional[SessionStore] = None,
        runtime_controller: Optional[RuntimeController] = None,
        path_opener: Optional[Callable[[Path], None]] = None,
    ) -> None:
        self._config_store = config_store
        self._config = config
        self._auth_gateway = auth_gateway or SteamCommunityAuthGateway()
        self._session_store = session_store or SessionStore()
        self._runtime_controller = runtime_controller or RuntimeController(
            session_store=self._session_store
        )
        self._window = None
        self._maximized = False
        self._pending_qr_logins: Dict[str, PendingQRLoginRecord] = {}
        self._path_opener = path_opener or self._default_path_opener
        self._sync_runtime_profiles()

    @property
    def config(self) -> AppConfig:
        return self._config

    def attach_window(self, window: Any) -> None:
        self._window = window
        self._register_window_events()

    def get_bootstrap_state(self) -> Dict[str, Any]:
        runtime_snapshot = self._runtime_controller.snapshot().to_dict()
        return {
            "theme": self._config.theme,
            "last_page": self._config.last_page,
            "counts": {
                "accounts": len(self._config.accounts),
                "configured_slots": sum(
                    1
                    for account in self._config.accounts
                    for game in account.games
                    if game.enabled
                ),
            },
            "paths": {
                "config": str(self._config_store.path),
                "sessions": str(self._session_store.base_dir),
                "logs": str(self._runtime_log_path().parent),
            },
            "build": {
                "desktop_stack": "pywebview + HTML/CSS/JS",
                "auth_reference": "SteamCommunityKit",
                "repo_flow": "Local first, GitHub backup",
                "default_branch": "main",
            },
            "window": {
                "maximized": self._maximized,
                "width": self._config.window_width,
                "height": self._config.window_height,
            },
            "accounts": [self._serialize_account(account) for account in self._config.accounts],
            "runtime": {
                "slot_ceiling": 32,
                "conflict_policy": "Per-account policy with pause, force-kick, or yield",
                "reconnect_posture": "Backoff and resume",
                **runtime_snapshot,
            },
            "onboarding": {
                "pending_qr_login_count": len(self._pending_qr_logins),
            },
            "persona_states": list(PERSONA_STATES),
            "conflict_policies": list(CONFLICT_POLICY_OPTIONS),
            "popular_games": list(POPULAR_GAMES),
        }

    def set_last_page(self, page_key: str) -> Dict[str, Any]:
        self._config.last_page = page_key or "dashboard"
        self._persist()
        return {"ok": True}

    def login_account_with_credentials(self, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        values = payload or {}
        display_name = self._normalize_optional_string(values.get("display_name"))
        account_name = self._normalize_required_string(values.get("account_name"), "account name")
        password = self._normalize_required_string(values.get("password"), "password")
        steam_guard_code = self._normalize_optional_string(values.get("steam_guard_code")) or None

        try:
            session = self._auth_gateway.login_with_credentials(
                account_name,
                password,
                persistence=True,
                steam_guard_code=steam_guard_code,
                prompt_for_steam_guard=False,
            )
            return self._upsert_authenticated_account(
                session=session,
                display_name=display_name or account_name,
                login_mode="credentials",
                success_message="Credential login completed and session bundle saved.",
            )
        except SteamAuthenticationError as exc:
            steam_guard_result = self._steam_guard_required_result(exc)
            if steam_guard_result is not None:
                return steam_guard_result
            return self._error_result(exc)
        except Exception as exc:
            return self._error_result(exc)

    def login_account_with_refresh_token(self, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        values = payload or {}
        display_name = self._normalize_optional_string(values.get("display_name"))
        refresh_token = self._normalize_required_string(values.get("refresh_token"), "refresh token")

        try:
            session = self._auth_gateway.login_with_refresh_token(refresh_token)
            return self._upsert_authenticated_account(
                session=session,
                display_name=display_name,
                login_mode="refresh_token",
                success_message="Refresh-token login completed and session bundle saved.",
            )
        except Exception as exc:
            return self._error_result(exc)

    def begin_qr_account_login(self, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        values = payload or {}
        display_name = self._normalize_optional_string(values.get("display_name"))
        device_friendly_name = (
            self._normalize_optional_string(values.get("device_friendly_name"))
            or "Steam Hour Booster Desktop"
        )

        try:
            pending = self._auth_gateway.begin_qr_login(
                device_friendly_name=device_friendly_name
            )
            pending_id = uuid4().hex
            record = PendingQRLoginRecord(
                pending_id=pending_id,
                display_name=display_name,
                pending=pending,
                device_friendly_name=device_friendly_name,
            )
            self._pending_qr_logins[pending_id] = record
            return {
                "ok": True,
                "status": "waiting",
                "pending_id": pending_id,
                "display_name": display_name,
                "device_friendly_name": device_friendly_name,
                "qr_image_url": pending.qr_image_url,
                "challenge_url": pending.challenge_url,
                "message": "Approve the login from the Steam mobile app, then keep this screen open.",
            }
        except Exception as exc:
            return self._error_result(exc)

    def poll_qr_account_login(self, pending_id: str) -> Dict[str, Any]:
        record = self._pending_qr_logins.get(str(pending_id))
        if record is None:
            return self._message_result(
                ok=False,
                status="not_found",
                message="This QR login session is no longer available.",
            )

        try:
            record.poll_attempts += 1
            session = self._auth_gateway.poll_qr_approval(record.pending)
            if session is None:
                return {
                    "ok": True,
                    "status": "waiting",
                    "pending_id": record.pending_id,
                    "poll_attempts": record.poll_attempts,
                    "message": "Waiting for Steam mobile confirmation.",
                }

            self._pending_qr_logins.pop(record.pending_id, None)
            result = self._upsert_authenticated_account(
                session=session,
                display_name=record.display_name,
                login_mode="qr",
                success_message="QR login approved and session bundle saved.",
            )
            result["status"] = "approved"
            result["poll_attempts"] = record.poll_attempts
            return result
        except Exception as exc:
            self._pending_qr_logins.pop(record.pending_id, None)
            return self._error_result(exc, status="error")

    def cancel_qr_account_login(self, pending_id: str) -> Dict[str, Any]:
        removed = self._pending_qr_logins.pop(str(pending_id), None)
        if removed is None:
            return self._message_result(
                ok=False,
                status="not_found",
                message="This QR login session is no longer available.",
            )
        return self._message_result(
            ok=True,
            status="cancelled",
            message="QR login cancelled.",
        )

    def remove_account(self, profile_id: str) -> Dict[str, Any]:
        resolved_profile_id = str(profile_id or "").strip()
        if not resolved_profile_id:
            return self._message_result(
                ok=False,
                status="invalid",
                message="A profile id is required.",
            )

        for index, account in enumerate(self._config.accounts):
            if account.profile_id != resolved_profile_id:
                continue
            if account.session_bundle_path:
                SessionStore.delete_bundle_path(account.session_bundle_path)
            else:
                self._session_store.delete_bundle(account.profile_id)
            self._config.accounts.pop(index)
            self._persist()
            self._sync_runtime_profiles()
            return {
                "ok": True,
                "status": "removed",
                "message": "Account removed and session bundle deleted.",
                "state": self.get_bootstrap_state(),
            }

        return self._message_result(
            ok=False,
            status="not_found",
            message="That account profile was not found.",
        )

    def save_account_profile(self, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        values = payload or {}
        profile_id = self._normalize_required_string(values.get("profile_id"), "profile id")
        account = self._find_account_by_profile_id(profile_id)
        if account is None:
            return self._message_result(
                ok=False,
                status="not_found",
                message="That account profile was not found.",
            )

        try:
            display_name = (
                self._normalize_optional_string(values.get("display_name"))
                or account.account_name
                or account.steam_id
                or account.profile_id
            )
            boost_enabled = self._normalize_bool(values.get("boost_enabled"), default=account.boost_enabled)
            appear_online = self._normalize_bool(values.get("appear_online"), default=account.appear_online)
            persona_state = self._normalize_persona_state(values.get("persona_state"))
            conflict_policy = self._normalize_conflict_policy(values.get("conflict_policy"))
            custom_status = self._normalize_optional_string(values.get("custom_status"))
            auto_reply_enabled = self._normalize_bool(
                values.get("auto_reply_enabled"),
                default=account.auto_reply_enabled,
            )
            auto_reply_message = self._normalize_optional_string(values.get("auto_reply_message"))
            auto_reply_cooldown_seconds = self._normalize_bounded_int(
                values.get("auto_reply_cooldown_seconds"),
                label="auto-reply cooldown",
                default=account.auto_reply_cooldown_seconds,
                minimum=15,
                maximum=86400,
            )
            auto_reply_timeout_seconds = self._normalize_bounded_int(
                values.get("auto_reply_timeout_seconds"),
                label="auto-reply timeout",
                default=account.auto_reply_timeout_seconds,
                minimum=30,
                maximum=86400,
            )
            notes = self._normalize_optional_string(values.get("notes"))
            games = self._parse_games_value(
                values.get("games"),
                fallback_value=values.get("games_text"),
            )

            if auto_reply_enabled and not auto_reply_message:
                raise SteamValidationError("Auto-reply message is required when auto-reply is enabled.")

            updated = AccountProfile(
                profile_id=account.profile_id,
                display_name=display_name,
                account_name=account.account_name,
                steam_id=account.steam_id,
                login_mode=account.login_mode,
                boost_enabled=boost_enabled,
                appear_online=appear_online,
                persona_state=persona_state,
                conflict_policy=conflict_policy,
                custom_status=custom_status,
                auto_reply_enabled=auto_reply_enabled,
                auto_reply_message=auto_reply_message,
                auto_reply_cooldown_seconds=auto_reply_cooldown_seconds,
                auto_reply_timeout_seconds=auto_reply_timeout_seconds,
                session_bundle_path=account.session_bundle_path,
                notes=notes,
                games=games,
            )

            for index, existing in enumerate(self._config.accounts):
                if existing.profile_id == account.profile_id:
                    self._config.accounts[index] = updated
                    break

            self._config.accounts.sort(key=lambda item: item.display_name.lower())
            self._persist()
            self._sync_runtime_profiles()
            runtime_status = self._runtime_controller.snapshot().accounts.get(updated.profile_id)
            success_message = "Account profile saved."

            if runtime_status and runtime_status.state in (
                RuntimeState.STARTING,
                RuntimeState.BOOSTING,
                RuntimeState.PAUSED,
            ):
                enabled_games = [game for game in updated.games if game.enabled]
                if not updated.boost_enabled:
                    runtime_status = self._runtime_controller.stop_profile(
                        updated.profile_id,
                        self._config.accounts,
                    )
                    success_message = (
                        "Account profile saved and the active lane was stopped because boosting is disabled."
                    )
                elif enabled_games:
                    runtime_status = self._runtime_controller.reconfigure_profile(
                        updated.profile_id,
                        self._config.accounts,
                    )
                    success_message = runtime_status.message or "Account profile saved and live lane updated."
                else:
                    runtime_status = self._runtime_controller.stop_profile(
                        updated.profile_id,
                        self._config.accounts,
                    )
                    success_message = (
                        "Account profile saved and the active lane was stopped because no enabled slots remain."
                    )

            return {
                "ok": True,
                "status": "saved",
                "message": success_message,
                "account": self._serialize_account(updated),
                "state": self.get_bootstrap_state(),
            }
        except Exception as exc:
            return self._error_result(exc)

    def set_account_boost_enabled(self, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        values = payload or {}

        try:
            profile_id = self._normalize_required_string(values.get("profile_id"), "profile id")
            if "boost_enabled" not in values:
                raise SteamValidationError("Boost enabled state is required.")
            boost_enabled = self._normalize_bool(values.get("boost_enabled"))
        except Exception as exc:
            return self._error_result(exc)

        account = self._find_account_by_profile_id(profile_id)
        if account is None:
            return self._message_result(
                ok=False,
                status="not_found",
                message="That account profile was not found.",
            )

        if boost_enabled == account.boost_enabled:
            state_label = "enabled" if boost_enabled else "disabled"
            return {
                "ok": True,
                "status": "unchanged",
                "message": "Booster is already %s for %s." % (
                    state_label,
                    self._account_identity_label(account),
                ),
                "account": self._serialize_account(account),
                "state": self.get_bootstrap_state(),
            }

        result = self.save_account_profile(
            {
                "profile_id": account.profile_id,
                "display_name": account.display_name,
                "boost_enabled": boost_enabled,
                "appear_online": account.appear_online,
                "persona_state": account.persona_state,
                "conflict_policy": account.conflict_policy,
                "custom_status": account.custom_status,
                "auto_reply_enabled": account.auto_reply_enabled,
                "auto_reply_message": account.auto_reply_message,
                "auto_reply_cooldown_seconds": account.auto_reply_cooldown_seconds,
                "auto_reply_timeout_seconds": account.auto_reply_timeout_seconds,
                "games": [game.to_dict() for game in account.games],
                "notes": account.notes,
            }
        )

        if result.get("ok") and result.get("message") == "Account profile saved.":
            result["status"] = "updated"
            result["message"] = "Booster %s for %s." % (
                "enabled" if boost_enabled else "disabled",
                self._account_identity_label(account),
            )

        return result

    def refresh_runtime_state(self) -> Dict[str, Any]:
        self._sync_runtime_profiles(reason="manual refresh")
        return {
            "ok": True,
            "status": "refreshed",
            "message": "Runtime readiness refreshed.",
            "state": self.get_bootstrap_state(),
        }

    def poll_runtime_state(self) -> Dict[str, Any]:
        return {
            "ok": True,
            "status": "polled",
            "state": self.get_bootstrap_state(),
        }

    def start_account_runtime(self, profile_id: str) -> Dict[str, Any]:
        resolved_profile_id = self._normalize_required_string(profile_id, "profile id")
        try:
            status = self._runtime_controller.start_profile(
                resolved_profile_id,
                self._config.accounts,
            )
        except KeyError as exc:
            return self._message_result(
                ok=False,
                status="not_found",
                message=str(exc).strip("'"),
            )

        ok = status.state == RuntimeState.BOOSTING
        return {
            "ok": ok,
            "status": "started" if ok else "blocked",
            "message": "Boost lane started for %s." % status.display_name if ok else status.message,
            "state": self.get_bootstrap_state(),
        }

    def stop_account_runtime(self, profile_id: str) -> Dict[str, Any]:
        resolved_profile_id = self._normalize_required_string(profile_id, "profile id")
        try:
            status = self._runtime_controller.stop_profile(
                resolved_profile_id,
                self._config.accounts,
            )
        except KeyError as exc:
            return self._message_result(
                ok=False,
                status="not_found",
                message=str(exc).strip("'"),
            )

        ok = status.state in (RuntimeState.IDLE, RuntimeState.READY, RuntimeState.ERROR)
        return {
            "ok": ok,
            "status": "stopped" if ok else "blocked",
            "message": "Boost lane stopped for %s." % status.display_name if ok else status.message,
            "state": self.get_bootstrap_state(),
        }

    def start_all_runtime(self) -> Dict[str, Any]:
        summary = self._runtime_controller.start_all(self._config.accounts)
        return {
            "ok": True,
            "status": "started",
            "message": "Runtime start sweep finished. Started %s, skipped %s, failed %s."
            % (summary["started"], summary["skipped"], summary["failed"]),
            "summary": summary,
            "state": self.get_bootstrap_state(),
        }

    def stop_all_runtime(self) -> Dict[str, Any]:
        summary = self._runtime_controller.stop_all(self._config.accounts)
        return {
            "ok": True,
            "status": "stopped",
            "message": "Stopped %s boost lane%s."
            % (summary["stopped"], "" if summary["stopped"] == 1 else "s"),
            "summary": summary,
            "state": self.get_bootstrap_state(),
        }

    def open_config_file(self) -> Dict[str, Any]:
        self._persist()
        return self._open_runtime_path(
            self._config_store.path,
            success_message="Opened the config file.",
            ensure_file=True,
        )

    def open_sessions_directory(self) -> Dict[str, Any]:
        return self._open_runtime_path(
            Path(self._session_store.base_dir),
            success_message="Opened the sessions directory.",
            ensure_directory=True,
        )

    def open_logs_directory(self) -> Dict[str, Any]:
        return self._open_runtime_path(
            self._runtime_log_path().parent,
            success_message="Opened the logs directory.",
            ensure_directory=True,
        )

    def open_runtime_log_file(self) -> Dict[str, Any]:
        runtime_log_path = self._runtime_log_path()
        return self._open_runtime_path(
            runtime_log_path,
            success_message="Opened the runtime log file.",
            ensure_file=True,
        )

    def open_account_session_bundle(self, profile_id: str) -> Dict[str, Any]:
        resolved_profile_id = self._normalize_required_string(profile_id, "profile id")
        account = self._find_account_by_profile_id(resolved_profile_id)
        if account is None:
            return self._message_result(
                ok=False,
                status="not_found",
                message="That account profile was not found.",
            )
        if not account.session_bundle_path:
            return self._message_result(
                ok=False,
                status="missing",
                message="This account does not have a saved session bundle path.",
            )
        return self._open_runtime_path(
            Path(account.session_bundle_path),
            success_message="Opened the session bundle file.",
            ensure_file=False,
        )

    def export_runtime_snapshot(self) -> Dict[str, Any]:
        snapshot = self._runtime_controller.snapshot().to_dict()
        export_dir = self._runtime_log_path().parent
        export_dir.mkdir(parents=True, exist_ok=True)
        export_path = export_dir / ("runtime-snapshot-%s.json" % datetime.now().strftime("%Y%m%d-%H%M%S"))
        payload = {
            "exported_at": datetime.now().isoformat(timespec="seconds"),
            "runtime": snapshot,
            "accounts": [self._serialize_account(account) for account in self._config.accounts],
        }
        export_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return {
            "ok": True,
            "status": "exported",
            "message": "Exported the runtime snapshot.",
            "path": str(export_path),
            "state": self.get_bootstrap_state(),
        }

    def minimize_window(self) -> Dict[str, Any]:
        if self._window is not None:
            self._window.minimize()
        return {"ok": True}

    def toggle_maximize_window(self) -> Dict[str, Any]:
        if self._window is None:
            return {"maximized": self._maximized}

        if self._maximized:
            self._window.restore()
            self._maximized = False
        else:
            self._window.maximize()
            self._maximized = True

        return {"maximized": self._maximized}

    def close_window(self) -> Dict[str, Any]:
        self._persist_window_state()
        self._runtime_controller.shutdown(self._config.accounts)
        if self._window is not None:
            self._window.destroy()
        return {"ok": True}

    def _register_window_events(self) -> None:
        if self._window is None:
            return
        self._window.events.maximized += self._on_maximized
        self._window.events.restored += self._on_restored
        self._window.events.resized += self._on_resized
        self._window.events.moved += self._on_moved
        self._window.events.closing += self._on_closing

    def _persist_window_state(self) -> None:
        self._capture_window_geometry()
        self._persist()

    def _capture_window_geometry(self) -> None:
        if self._window is None or self._maximized:
            return
        self._config.window_width = int(self._window.width)
        self._config.window_height = int(self._window.height)
        self._config.window_x = int(self._window.x)
        self._config.window_y = int(self._window.y)

    def _persist(self) -> None:
        self._config_store.save(self._config)

    def _sync_runtime_profiles(self, *, reason: str = "") -> None:
        self._runtime_controller.refresh_accounts(self._config.accounts, reason=reason)

    def _runtime_log_path(self) -> Path:
        snapshot = self._runtime_controller.snapshot().to_dict()
        runtime_log_path = str(snapshot.get("event_log_path", "") or "").strip()
        if runtime_log_path:
            return Path(runtime_log_path)
        return Path(self._session_store.base_dir).parent / "logs" / "runtime.log"

    def _open_runtime_path(
        self,
        target: Path,
        *,
        success_message: str,
        ensure_directory: bool = False,
        ensure_file: bool = False,
    ) -> Dict[str, Any]:
        resolved_target = Path(target)
        try:
            if ensure_directory:
                resolved_target.mkdir(parents=True, exist_ok=True)
            elif ensure_file:
                resolved_target.parent.mkdir(parents=True, exist_ok=True)
                if not resolved_target.exists():
                    resolved_target.write_text("", encoding="utf-8")
            self._path_opener(resolved_target)
        except Exception as exc:
            return self._error_result(exc)
        return {
            "ok": True,
            "status": "opened",
            "message": success_message,
            "path": str(resolved_target),
            "state": self.get_bootstrap_state(),
        }

    @staticmethod
    def _default_path_opener(target: Path) -> None:
        resolved_target = Path(target)
        if hasattr(os, "startfile"):
            os.startfile(str(resolved_target))
            return
        if os.name == "nt":
            subprocess.Popen(["explorer", str(resolved_target)])
            return
        raise RuntimeError("Opening local paths is not supported on this platform.")

    def _upsert_authenticated_account(
        self,
        *,
        session: AuthSession,
        display_name: str,
        login_mode: str,
        success_message: str,
    ) -> Dict[str, Any]:
        steam_id = str(session.steam_id).strip()
        existing = self._find_account_by_steam_id(steam_id)
        profile_id = existing.profile_id if existing else self._session_store.build_profile_id(steam_id)
        session_path = self._session_store.save_bundle(profile_id, session.session_bundle)

        resolved_display_name = (
            self._normalize_optional_string(display_name)
            or (existing.display_name if existing else "")
            or (session.account_name or "")
            or steam_id
        )
        resolved_account_name = (
            (session.account_name or "").strip()
            or (existing.account_name if existing else "")
        )
        boost_enabled = existing.boost_enabled if existing else True
        appear_online = existing.appear_online if existing else True
        persona_state = existing.persona_state if existing else "Online"
        conflict_policy = existing.conflict_policy if existing else CONFLICT_POLICY_PAUSE
        custom_status = existing.custom_status if existing else ""
        auto_reply_enabled = existing.auto_reply_enabled if existing else False
        auto_reply_message = existing.auto_reply_message if existing else ""
        auto_reply_cooldown_seconds = existing.auto_reply_cooldown_seconds if existing else 180
        auto_reply_timeout_seconds = existing.auto_reply_timeout_seconds if existing else 1800
        notes = existing.notes if existing else ""
        games = list(existing.games) if existing else []

        updated = AccountProfile(
            profile_id=profile_id,
            display_name=resolved_display_name,
            account_name=resolved_account_name,
            steam_id=steam_id,
            login_mode=login_mode,
            boost_enabled=boost_enabled,
            appear_online=appear_online,
            persona_state=persona_state,
            conflict_policy=conflict_policy,
            custom_status=custom_status,
            auto_reply_enabled=auto_reply_enabled,
            auto_reply_message=auto_reply_message,
            auto_reply_cooldown_seconds=auto_reply_cooldown_seconds,
            auto_reply_timeout_seconds=auto_reply_timeout_seconds,
            session_bundle_path=str(session_path),
            notes=notes,
            games=games,
        )

        if existing is None:
            self._config.accounts.append(updated)
        else:
            for index, account in enumerate(self._config.accounts):
                if account.profile_id == existing.profile_id:
                    self._config.accounts[index] = updated
                    break

        self._config.accounts.sort(key=lambda account: account.display_name.lower())
        self._persist()
        self._sync_runtime_profiles()

        return {
            "ok": True,
            "status": "authenticated",
            "message": success_message,
            "account": self._serialize_account(updated),
            "state": self.get_bootstrap_state(),
        }

    def _find_account_by_steam_id(self, steam_id: str) -> Optional[AccountProfile]:
        for account in self._config.accounts:
            if account.steam_id == steam_id:
                return account
        return None

    def _find_account_by_profile_id(self, profile_id: str) -> Optional[AccountProfile]:
        for account in self._config.accounts:
            if account.profile_id == profile_id:
                return account
        return None

    def _message_result(self, *, ok: bool, status: str, message: str) -> Dict[str, Any]:
        return {
            "ok": ok,
            "status": status,
            "message": message,
        }

    def _error_result(self, exc: Exception, *, status: str = "error") -> Dict[str, Any]:
        error_type = exc.__class__.__name__
        message = str(exc).strip() or "The operation failed."

        if isinstance(
            exc,
            (
                SteamAuthenticationError,
                SteamValidationError,
                SteamResponseError,
                SteamNetworkError,
                SteamHTTPError,
            ),
        ):
            return {
                "ok": False,
                "status": status,
                "message": message,
                "error_type": error_type,
            }

        return {
            "ok": False,
            "status": status,
            "message": "Unexpected error: %s" % message,
            "error_type": error_type,
        }

    def _steam_guard_required_result(self, exc: SteamAuthenticationError) -> Optional[Dict[str, Any]]:
        payload = getattr(exc, "payload", None)
        if not isinstance(payload, dict):
            return None

        confirmation = self._select_steam_guard_confirmation(payload)
        if confirmation is None:
            return None

        code_kind = self._steam_guard_code_kind(confirmation)
        associated_message = self._normalize_optional_string(confirmation.get("associated_message"))
        return {
            "ok": False,
            "status": "steam_guard_required",
            "message": str(exc).strip() or "A Steam Guard code is required for this login.",
            "error_type": exc.__class__.__name__,
            "code_kind": code_kind,
            "code_label": self._steam_guard_code_label(code_kind),
            "code_placeholder": self._steam_guard_code_placeholder(code_kind, associated_message),
            "associated_message": associated_message,
        }

    @staticmethod
    def _select_steam_guard_confirmation(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        confirmations = payload.get("allowed_confirmations") or []
        for preferred_type in (3, 2):
            for confirmation in confirmations:
                try:
                    if int(confirmation.get("confirmation_type", 0)) == preferred_type:
                        return confirmation
                except Exception:
                    continue
        for confirmation in confirmations:
            if confirmation.get("confirmation_type"):
                return confirmation
        return None

    @staticmethod
    def _steam_guard_code_kind(confirmation: Dict[str, Any]) -> str:
        confirmation_type = int(confirmation.get("confirmation_type", 0) or 0)
        if confirmation_type == 3:
            return "app"
        if confirmation_type == 2:
            return "email"
        return "generic"

    @staticmethod
    def _steam_guard_code_label(code_kind: str) -> str:
        if code_kind == "app":
            return "Steam Guard App Code"
        if code_kind == "email":
            return "Steam Guard Email Code"
        return "Steam Guard Code"

    @staticmethod
    def _steam_guard_code_placeholder(code_kind: str, associated_message: str) -> str:
        if code_kind == "app":
            return "Enter the Steam mobile authenticator code"
        if code_kind == "email" and associated_message:
            return "Enter the email code sent to %s" % associated_message
        if code_kind == "email":
            return "Enter the Steam Guard email code"
        return "Enter the required Steam Guard code"

    @staticmethod
    def _normalize_optional_string(value: Any) -> str:
        if value is None:
            return ""
        return str(value).strip()

    def _normalize_required_string(self, value: Any, label: str) -> str:
        normalized = self._normalize_optional_string(value)
        if normalized:
            return normalized
        raise SteamValidationError("%s is required." % label.capitalize())

    def _normalize_persona_state(self, value: Any) -> str:
        normalized = self._normalize_optional_string(value) or "Online"
        if normalized not in ALL_PERSONA_STATES:
            raise SteamValidationError("Persona state is invalid.")
        return normalized

    def _normalize_conflict_policy(self, value: Any) -> str:
        normalized = self._normalize_optional_string(value) or CONFLICT_POLICY_PAUSE
        if normalized not in CONFLICT_POLICIES:
            raise SteamValidationError("Conflict policy is invalid.")
        return normalized

    @staticmethod
    def _normalize_bool(value: Any, *, default: bool = False) -> bool:
        if value is None:
            return bool(default)
        if isinstance(value, bool):
            return value
        normalized = str(value).strip().lower()
        if normalized in ("1", "true", "yes", "on"):
            return True
        if normalized in ("0", "false", "no", "off"):
            return False
        return bool(default)

    def _normalize_bounded_int(
        self,
        value: Any,
        *,
        label: str,
        default: int,
        minimum: int,
        maximum: int,
    ) -> int:
        if value is None or str(value).strip() == "":
            resolved = int(default)
        else:
            normalized = self._normalize_optional_string(value)
            if not normalized.isdigit():
                raise SteamValidationError("%s must be a whole number." % label.capitalize())
            resolved = int(normalized)
        if resolved < minimum or resolved > maximum:
            raise SteamValidationError(
                "%s must be between %s and %s seconds."
                % (label.capitalize(), minimum, maximum)
            )
        return resolved

    def _parse_games_value(
        self,
        value: Any,
        *,
        fallback_value: Any = None,
    ) -> List[IdleGame]:
        if value is None:
            return self._parse_games_text(fallback_value)
        if not isinstance(value, list):
            raise SteamValidationError("Game queue payload is invalid.")

        games: List[IdleGame] = []
        seen_app_ids = set()
        for item in value:
            if not isinstance(item, dict):
                raise SteamValidationError("Game queue payload is invalid.")

            app_id_text = self._normalize_optional_string(item.get("app_id"))
            if not app_id_text.isdigit():
                raise SteamValidationError("Game app ids must be positive integers.")
            app_id = int(app_id_text)
            if app_id <= 0:
                raise SteamValidationError("Game app ids must be positive integers.")
            if app_id in seen_app_ids:
                continue

            seen_app_ids.add(app_id)
            games.append(
                IdleGame(
                    app_id=app_id,
                    title=self._normalize_optional_string(item.get("title")),
                    enabled=self._normalize_bool(item.get("enabled"), default=True),
                )
            )

        if len(games) > 32:
            raise SteamValidationError("A single account can only queue up to 32 game slots.")

        return games

    @staticmethod
    def _effective_persona_state(account: AccountProfile) -> str:
        if not account.appear_online:
            return "Invisible"
        persona_state = str(account.persona_state or "").strip() or "Online"
        if persona_state in ("Invisible", "Offline"):
            return "Online"
        return persona_state

    def _parse_games_text(self, raw_value: Any) -> List[IdleGame]:
        text = self._normalize_optional_string(raw_value)
        if not text:
            return []

        games: List[IdleGame] = []
        seen_app_ids = set()
        raw_lines = []
        for line in text.splitlines():
            normalized_line = line.strip()
            if not normalized_line:
                continue
            if "|" not in normalized_line and ":" not in normalized_line and "," in normalized_line:
                segments = [segment.strip() for segment in normalized_line.split(",") if segment.strip()]
                raw_lines.extend(segments)
                continue
            raw_lines.append(normalized_line)

        for line in raw_lines:
            app_id_text = line
            title = ""
            if "|" in line:
                app_id_text, title = line.split("|", 1)
            elif ":" in line:
                left, right = line.split(":", 1)
                if left.strip().isdigit():
                    app_id_text, title = left, right
            app_id_text = app_id_text.strip()
            if not app_id_text.isdigit():
                raise SteamValidationError(
                    "Game entries must start with a numeric app id. Use formats like '730' or '730: Counter-Strike 2'."
                )
            app_id = int(app_id_text)
            if app_id <= 0:
                raise SteamValidationError("Game app ids must be positive integers.")
            if app_id in seen_app_ids:
                continue
            seen_app_ids.add(app_id)
            games.append(
                IdleGame(
                    app_id=app_id,
                    title=title.strip(),
                    enabled=True,
                )
            )

        if len(games) > 32:
            raise SteamValidationError("A single account can only queue up to 32 game slots.")

        return games

    def _on_maximized(self, *args) -> None:
        self._maximized = True
        self._persist()

    def _on_restored(self, *args) -> None:
        self._maximized = False
        self._capture_window_geometry()
        self._persist()

    def _on_resized(self, *args) -> None:
        self._capture_window_geometry()

    def _on_moved(self, *args) -> None:
        self._capture_window_geometry()

    def _on_closing(self, *args) -> None:
        self._persist_window_state()

    def _serialize_account(self, account: AccountProfile) -> Dict[str, Any]:
        session_path = str(account.session_bundle_path or "")
        has_session_bundle = bool(session_path and Path(session_path).exists())
        session_summary = (
            SessionStore.summarize_bundle_path(session_path)
            if session_path
            else {"exists": False, "path": ""}
        )
        return {
            "profile_id": account.profile_id,
            "display_name": account.display_name,
            "account_name": account.account_name,
            "steam_id": account.steam_id,
            "login_mode": account.login_mode,
            "boost_enabled": account.boost_enabled,
            "appear_online": account.appear_online,
            "persona_state": account.persona_state,
            "effective_persona_state": self._effective_persona_state(account),
            "conflict_policy": account.conflict_policy,
            "custom_status": account.custom_status,
            "auto_reply_enabled": account.auto_reply_enabled,
            "auto_reply_message": account.auto_reply_message,
            "auto_reply_cooldown_seconds": account.auto_reply_cooldown_seconds,
            "auto_reply_timeout_seconds": account.auto_reply_timeout_seconds,
            "notes": account.notes,
            "session_bundle_path": session_path,
            "has_session_bundle": has_session_bundle,
            "session_summary": session_summary,
            "game_count": len(account.games),
            "enabled_game_count": sum(1 for game in account.games if game.enabled),
            "games": [game.to_dict() for game in account.games],
            "games_text": self._format_games_text(account.games),
        }

    @staticmethod
    def _account_identity_label(account: AccountProfile) -> str:
        return (
            str(account.display_name or "").strip()
            or str(account.account_name or "").strip()
            or str(account.steam_id or "").strip()
            or str(account.profile_id or "").strip()
        )

    @staticmethod
    def _format_games_text(games: List[IdleGame]) -> str:
        lines = []
        for game in games:
            if game.title:
                lines.append("%s: %s" % (game.app_id, game.title))
            else:
                lines.append(str(game.app_id))
        return "\n".join(lines)
