from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

CONFLICT_POLICY_PAUSE = "pause"
CONFLICT_POLICY_KICK = "kick"
CONFLICT_POLICY_YIELD = "yield"
CONFLICT_POLICIES = (
    CONFLICT_POLICY_PAUSE,
    CONFLICT_POLICY_KICK,
    CONFLICT_POLICY_YIELD,
)

VISIBLE_PERSONA_STATES = (
    "Online",
    "Busy",
    "Away",
    "Snooze",
    "LookingToTrade",
    "LookingToPlay",
)


@dataclass
class IdleGame:
    app_id: int
    title: str = ""
    enabled: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "app_id": int(self.app_id),
            "title": self.title,
            "enabled": bool(self.enabled),
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "IdleGame":
        return cls(
            app_id=int(payload["app_id"]),
            title=str(payload.get("title", "")),
            enabled=bool(payload.get("enabled", True)),
        )


@dataclass
class AccountProfile:
    profile_id: str
    display_name: str
    account_name: str = ""
    steam_id: str = ""
    login_mode: str = "credentials"
    boost_enabled: bool = True
    appear_online: bool = True
    persona_state: str = "Online"
    conflict_policy: str = CONFLICT_POLICY_PAUSE
    custom_status: str = ""
    auto_reply_enabled: bool = False
    auto_reply_message: str = ""
    auto_reply_cooldown_seconds: int = 180
    auto_reply_timeout_seconds: int = 1800
    session_bundle_path: Optional[str] = None
    notes: str = ""
    games: List[IdleGame] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "display_name": self.display_name,
            "account_name": self.account_name,
            "steam_id": self.steam_id,
            "login_mode": self.login_mode,
            "boost_enabled": bool(self.boost_enabled),
            "appear_online": bool(self.appear_online),
            "persona_state": self.persona_state,
            "conflict_policy": self.conflict_policy,
            "custom_status": self.custom_status,
            "auto_reply_enabled": bool(self.auto_reply_enabled),
            "auto_reply_message": self.auto_reply_message,
            "auto_reply_cooldown_seconds": int(self.auto_reply_cooldown_seconds),
            "auto_reply_timeout_seconds": int(self.auto_reply_timeout_seconds),
            "session_bundle_path": self.session_bundle_path,
            "notes": self.notes,
            "games": [game.to_dict() for game in self.games],
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "AccountProfile":
        return cls(
            profile_id=str(payload["profile_id"]),
            display_name=str(payload.get("display_name") or payload["profile_id"]),
            account_name=str(payload.get("account_name", "")),
            steam_id=str(payload.get("steam_id", "")),
            login_mode=str(payload.get("login_mode", "credentials")),
            boost_enabled=bool(payload.get("boost_enabled", True)),
            appear_online=bool(payload.get("appear_online", True)),
            persona_state=str(payload.get("persona_state", "Online")),
            conflict_policy=str(payload.get("conflict_policy", CONFLICT_POLICY_PAUSE)),
            custom_status=str(payload.get("custom_status", "")),
            auto_reply_enabled=bool(payload.get("auto_reply_enabled", False)),
            auto_reply_message=str(payload.get("auto_reply_message", "")),
            auto_reply_cooldown_seconds=int(payload.get("auto_reply_cooldown_seconds", 180)),
            auto_reply_timeout_seconds=int(payload.get("auto_reply_timeout_seconds", 1800)),
            session_bundle_path=payload.get("session_bundle_path"),
            notes=str(payload.get("notes", "")),
            games=[IdleGame.from_dict(item) for item in payload.get("games", [])],
        )


@dataclass
class AppConfig:
    theme: str = "ember"
    window_width: int = 1460
    window_height: int = 920
    window_x: Optional[int] = None
    window_y: Optional[int] = None
    last_page: str = "dashboard"
    accounts: List[AccountProfile] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "theme": self.theme,
            "window_width": int(self.window_width),
            "window_height": int(self.window_height),
            "window_x": self.window_x,
            "window_y": self.window_y,
            "last_page": self.last_page,
            "accounts": [account.to_dict() for account in self.accounts],
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "AppConfig":
        return cls(
            theme=str(payload.get("theme", "ember")),
            window_width=int(payload.get("window_width", 1460)),
            window_height=int(payload.get("window_height", 920)),
            window_x=payload.get("window_x"),
            window_y=payload.get("window_y"),
            last_page=str(payload.get("last_page", "dashboard")),
            accounts=[AccountProfile.from_dict(item) for item in payload.get("accounts", [])],
        )
