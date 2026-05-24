from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional
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
from steam_hour_booster.models import AccountProfile, AppConfig, IdleGame
from steam_hour_booster.paths import config_path, logs_dir, sessions_dir
from steam_hour_booster.session_store import SessionStore


@dataclass
class PendingQRLoginRecord:
    pending_id: str
    display_name: str
    pending: PendingQRLogin
    device_friendly_name: str
    poll_attempts: int = 0


PERSONA_STATES = [
    "Online",
    "Busy",
    "Away",
    "Snooze",
    "LookingToTrade",
    "LookingToPlay",
    "Invisible",
    "Offline",
]


class DesktopApi:
    def __init__(
        self,
        *,
        config_store: ConfigStore,
        config: AppConfig,
        auth_gateway: Optional[SteamCommunityAuthGateway] = None,
        session_store: Optional[SessionStore] = None,
    ) -> None:
        self._config_store = config_store
        self._config = config
        self._auth_gateway = auth_gateway or SteamCommunityAuthGateway()
        self._session_store = session_store or SessionStore()
        self._window = None
        self._maximized = False
        self._pending_qr_logins: Dict[str, PendingQRLoginRecord] = {}

    @property
    def config(self) -> AppConfig:
        return self._config

    def attach_window(self, window: Any) -> None:
        self._window = window
        self._register_window_events()

    def get_bootstrap_state(self) -> Dict[str, Any]:
        return {
            "theme": self._config.theme,
            "last_page": self._config.last_page,
            "counts": {
                "accounts": len(self._config.accounts),
                "configured_slots": sum(len(account.games) for account in self._config.accounts),
            },
            "paths": {
                "config": str(config_path()),
                "sessions": str(sessions_dir()),
                "logs": str(logs_dir()),
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
                "conflict_policy": "Pause before force-kick",
                "reconnect_posture": "Backoff and resume",
            },
            "onboarding": {
                "pending_qr_login_count": len(self._pending_qr_logins),
            },
            "persona_states": list(PERSONA_STATES),
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
            persona_state = self._normalize_persona_state(values.get("persona_state"))
            custom_status = self._normalize_optional_string(values.get("custom_status"))
            notes = self._normalize_optional_string(values.get("notes"))
            games = self._parse_games_text(values.get("games_text"))

            updated = AccountProfile(
                profile_id=account.profile_id,
                display_name=display_name,
                account_name=account.account_name,
                steam_id=account.steam_id,
                login_mode=account.login_mode,
                persona_state=persona_state,
                custom_status=custom_status,
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
            return {
                "ok": True,
                "status": "saved",
                "message": "Account profile saved.",
                "account": self._serialize_account(updated),
                "state": self.get_bootstrap_state(),
            }
        except Exception as exc:
            return self._error_result(exc)

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
        persona_state = existing.persona_state if existing else "Online"
        custom_status = existing.custom_status if existing else ""
        notes = existing.notes if existing else ""
        games = list(existing.games) if existing else []

        updated = AccountProfile(
            profile_id=profile_id,
            display_name=resolved_display_name,
            account_name=resolved_account_name,
            steam_id=steam_id,
            login_mode=login_mode,
            persona_state=persona_state,
            custom_status=custom_status,
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
        if normalized not in PERSONA_STATES:
            raise SteamValidationError("Persona state is invalid.")
        return normalized

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
            "persona_state": account.persona_state,
            "custom_status": account.custom_status,
            "notes": account.notes,
            "session_bundle_path": session_path,
            "has_session_bundle": has_session_bundle,
            "session_summary": session_summary,
            "game_count": len(account.games),
            "games": [game.to_dict() for game in account.games],
            "games_text": self._format_games_text(account.games),
        }

    @staticmethod
    def _format_games_text(games: List[IdleGame]) -> str:
        lines = []
        for game in games:
            if game.title:
                lines.append("%s: %s" % (game.app_id, game.title))
            else:
                lines.append(str(game.app_id))
        return "\n".join(lines)
