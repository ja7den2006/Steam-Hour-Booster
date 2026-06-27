from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from steamcommunitykit import SteamClient
from steamcommunitykit.exceptions import (
    SteamAuthenticationError,
    SteamHTTPError,
    SteamNetworkError,
    SteamResponseError,
    SteamValidationError,
)

from steam_hour_booster.models import AccountProfile
from steam_hour_booster.session_store import SessionStore


def _iso_timestamp() -> str:
    return datetime.now().isoformat(timespec="seconds")


@dataclass
class OwnedGamesValidationResult:
    state: str
    message: str
    checked_at: str = ""
    api_key_available: bool = False
    validated_app_ids: List[int] = field(default_factory=list)
    missing_app_ids: List[int] = field(default_factory=list)
    matched_titles: Dict[int, str] = field(default_factory=dict)


class OwnedGamesValidator:
    def __init__(
        self,
        *,
        client_factory: Optional[Callable[[], SteamClient]] = None,
    ) -> None:
        self._client_factory = client_factory or SteamClient
        self._cache: Dict[Tuple[str, str, Tuple[int, ...]], OwnedGamesValidationResult] = {}
        self._lock = threading.Lock()

    def validate_account(self, account: AccountProfile) -> OwnedGamesValidationResult:
        app_ids = tuple(sorted({int(game.app_id) for game in account.games if game.enabled}))
        if not app_ids:
            return OwnedGamesValidationResult(
                state="skipped",
                message="No game slots are configured for library validation.",
                checked_at=_iso_timestamp(),
            )

        session_path = str(account.session_bundle_path or "").strip()
        if not session_path:
            return OwnedGamesValidationResult(
                state="unavailable",
                message="Saved session bundle is missing, so owned-game validation is unavailable.",
                checked_at=_iso_timestamp(),
            )

        resolved_path = Path(session_path)
        if not resolved_path.exists():
            return OwnedGamesValidationResult(
                state="unavailable",
                message="Saved session bundle file is missing, so owned-game validation is unavailable.",
                checked_at=_iso_timestamp(),
            )

        signature = self._build_signature(resolved_path, app_ids)
        with self._lock:
            cached = self._cache.get(signature)
        if cached is not None:
            return cached

        bundle = SessionStore.load_bundle_path(session_path)
        if not bundle:
            result = OwnedGamesValidationResult(
                state="unavailable",
                message="Saved session bundle could not be loaded for library validation.",
                checked_at=_iso_timestamp(),
                validated_app_ids=list(app_ids),
            )
            self._store_cache(signature, result)
            return result

        steam_id = str(bundle.get("steam_id", "") or account.steam_id or "").strip()
        if not steam_id:
            result = OwnedGamesValidationResult(
                state="unavailable",
                message="Saved session bundle does not include a SteamID for owned-game validation.",
                checked_at=_iso_timestamp(),
                validated_app_ids=list(app_ids),
            )
            self._store_cache(signature, result)
            return result

        session_id = str(bundle.get("session_id") or bundle.get("sessionid") or "").strip()
        has_web_credentials = bool(
            bundle.get("steam_login_secure")
            or bundle.get("steamLoginSecure")
            or bundle.get("access_token")
            or bundle.get("has_access_token")
            or bundle.get("has_steam_login_secure")
        )
        if not session_id or not has_web_credentials:
            result = OwnedGamesValidationResult(
                state="unavailable",
                message=(
                    "Saved session bundle does not include the community web credentials required for owned-game validation."
                ),
                checked_at=_iso_timestamp(),
                validated_app_ids=list(app_ids),
            )
            self._store_cache(signature, result)
            return result

        try:
            client = self._client_factory()
            client.set_community_credentials_from_bundle(bundle)
            api_status = client.get_web_api_key_status()
            if not api_status.get("has_access", False):
                result = OwnedGamesValidationResult(
                    state="unavailable",
                    message=(
                        api_status.get("reason")
                        or "Steam Web API access is unavailable for this account, so owned-game validation could not run."
                    ),
                    checked_at=_iso_timestamp(),
                    api_key_available=False,
                    validated_app_ids=list(app_ids),
                )
                self._store_cache(signature, result)
                return result

            api_key = str(api_status.get("api_key", "") or "").strip()
            if not api_key:
                result = OwnedGamesValidationResult(
                    state="unavailable",
                    message="No Steam Web API key is registered for this account, so owned-game validation could not run.",
                    checked_at=_iso_timestamp(),
                    api_key_available=False,
                    validated_app_ids=list(app_ids),
                )
                self._store_cache(signature, result)
                return result

            client.set_api_key(api_key)
            summary = client.get_owned_games_summary_for_user(
                steam_id,
                include_appinfo=True,
                include_played_free_games=True,
                appids_filter=list(app_ids),
            )
            games_map = summary.get("games_map", {}) or {}
            matched_titles = {
                int(app_id): str(game.get("name") or "")
                for app_id, game in games_map.items()
                if app_id is not None
            }
            missing_app_ids = [app_id for app_id in app_ids if app_id not in matched_titles]

            if missing_app_ids:
                result = OwnedGamesValidationResult(
                    state="invalid",
                    message=(
                        "Configured app IDs are not present in the owned-games API for this account: %s."
                        % ", ".join(str(app_id) for app_id in missing_app_ids)
                    ),
                    checked_at=_iso_timestamp(),
                    api_key_available=True,
                    validated_app_ids=list(app_ids),
                    missing_app_ids=list(missing_app_ids),
                    matched_titles=matched_titles,
                )
                self._store_cache(signature, result)
                return result

            result = OwnedGamesValidationResult(
                state="valid",
                message="All configured app IDs were found in the owned-games API for this account.",
                checked_at=_iso_timestamp(),
                api_key_available=True,
                validated_app_ids=list(app_ids),
                missing_app_ids=[],
                matched_titles=matched_titles,
            )
            self._store_cache(signature, result)
            return result
        except (
            SteamAuthenticationError,
            SteamValidationError,
            SteamResponseError,
            SteamNetworkError,
            SteamHTTPError,
        ) as exc:
            result = OwnedGamesValidationResult(
                state="unavailable",
                message=str(exc).strip() or "Owned-game validation failed.",
                checked_at=_iso_timestamp(),
                api_key_available=False,
                validated_app_ids=list(app_ids),
            )
            self._store_cache(signature, result)
            return result
        except Exception as exc:
            result = OwnedGamesValidationResult(
                state="unavailable",
                message="Owned-game validation failed unexpectedly: %s" % (str(exc).strip() or "unknown error"),
                checked_at=_iso_timestamp(),
                api_key_available=False,
                validated_app_ids=list(app_ids),
            )
            self._store_cache(signature, result)
            return result

    @staticmethod
    def _build_signature(resolved_path: Path, app_ids: Tuple[int, ...]) -> Tuple[str, str, Tuple[int, ...]]:
        modified = datetime.fromtimestamp(resolved_path.stat().st_mtime).isoformat(timespec="seconds")
        return (str(resolved_path), modified, app_ids)

    def _store_cache(
        self,
        signature: Tuple[str, str, Tuple[int, ...]],
        result: OwnedGamesValidationResult,
    ) -> None:
        with self._lock:
            self._cache = {
                key: value
                for key, value in self._cache.items()
                if key[0] != signature[0]
            }
            self._cache[signature] = result
