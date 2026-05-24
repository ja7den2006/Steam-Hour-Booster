from __future__ import annotations

from typing import Any, Dict, List, Optional

from steam_hour_booster.config_store import ConfigStore
from steam_hour_booster.models import AccountProfile, AppConfig
from steam_hour_booster.paths import config_path, logs_dir, sessions_dir


class DesktopApi:
    def __init__(self, *, config_store: ConfigStore, config: AppConfig) -> None:
        self._config_store = config_store
        self._config = config
        self._window = None
        self._maximized = False

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
        }

    def set_last_page(self, page_key: str) -> Dict[str, Any]:
        self._config.last_page = page_key or "dashboard"
        self._persist()
        return {"ok": True}

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

    @staticmethod
    def _serialize_account(account: AccountProfile) -> Dict[str, Any]:
        return {
            "profile_id": account.profile_id,
            "display_name": account.display_name,
            "account_name": account.account_name,
            "steam_id": account.steam_id,
            "login_mode": account.login_mode,
            "persona_state": account.persona_state,
            "custom_status": account.custom_status,
            "game_count": len(account.games),
            "games": [game.to_dict() for game in account.games],
        }
