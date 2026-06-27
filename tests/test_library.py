import os

from steam_hour_booster.library import OwnedGamesValidator
from steam_hour_booster.models import AccountProfile, IdleGame
from steam_hour_booster.session_store import SessionStore


class RecordingSteamClient:
    def __init__(self, games_map=None):
        self.games_map = games_map or {}
        self.bundle = None
        self.api_key = ""
        self.api_status_calls = 0
        self.owned_games_calls = 0

    def set_community_credentials_from_bundle(self, bundle):
        self.bundle = dict(bundle)

    def get_web_api_key_status(self):
        self.api_status_calls += 1
        return {
            "has_access": True,
            "api_key": "test-api-key",
        }

    def set_api_key(self, api_key):
        self.api_key = api_key

    def get_owned_games_summary_for_user(self, steam_id, **kwargs):
        assert steam_id
        assert kwargs["include_appinfo"] is True
        assert kwargs["include_played_free_games"] is True
        self.owned_games_calls += 1
        return {
            "games_map": dict(self.games_map),
        }


def test_owned_games_validator_requires_web_credentials_before_network_validation(tmp_path) -> None:
    session_store = SessionStore(base_dir=tmp_path / "sessions")
    bundle_path = session_store.save_bundle(
        "steam_7656119",
        {
            "steam_id": "7656119",
            "refresh_token": "refresh",
        },
    )

    def unexpected_client():
        raise AssertionError("validator should not build a Steam client without web credentials")

    validator = OwnedGamesValidator(client_factory=unexpected_client)
    account = AccountProfile(
        profile_id="steam_7656119",
        display_name="Primary",
        steam_id="7656119",
        session_bundle_path=str(bundle_path),
        games=[IdleGame(app_id=730)],
    )

    result = validator.validate_account(account)

    assert result.state == "unavailable"
    assert "community web credentials" in result.message.lower()


def test_owned_games_validator_caches_results_until_bundle_timestamp_changes(tmp_path) -> None:
    session_store = SessionStore(base_dir=tmp_path / "sessions")
    bundle_path = session_store.save_bundle(
        "steam_7656119",
        {
            "steam_id": "7656119",
            "session_id": "session-id",
            "access_token": "access-token",
            "refresh_token": "refresh-token",
            "has_access_token": True,
            "has_refresh_token": True,
        },
    )
    client = RecordingSteamClient(
        games_map={
            730: {"name": "Counter-Strike 2"},
            570: {"name": "Dota 2"},
        }
    )
    validator = OwnedGamesValidator(client_factory=lambda: client)
    account = AccountProfile(
        profile_id="steam_7656119",
        display_name="Primary",
        steam_id="7656119",
        session_bundle_path=str(bundle_path),
        games=[IdleGame(app_id=730), IdleGame(app_id=570)],
    )

    first = validator.validate_account(account)
    second = validator.validate_account(account)
    stat = bundle_path.stat()
    os.utime(bundle_path, (stat.st_atime, stat.st_mtime + 2))
    third = validator.validate_account(account)

    assert first.state == "valid"
    assert second.state == "valid"
    assert third.state == "valid"
    assert client.api_status_calls == 2
    assert client.owned_games_calls == 2


def test_owned_games_validator_reports_missing_app_ids(tmp_path) -> None:
    session_store = SessionStore(base_dir=tmp_path / "sessions")
    bundle_path = session_store.save_bundle(
        "steam_7656119",
        {
            "steam_id": "7656119",
            "session_id": "session-id",
            "access_token": "access-token",
            "refresh_token": "refresh-token",
            "has_access_token": True,
            "has_refresh_token": True,
        },
    )
    client = RecordingSteamClient(
        games_map={
            730: {"name": "Counter-Strike 2"},
        }
    )
    validator = OwnedGamesValidator(client_factory=lambda: client)
    account = AccountProfile(
        profile_id="steam_7656119",
        display_name="Primary",
        steam_id="7656119",
        session_bundle_path=str(bundle_path),
        games=[IdleGame(app_id=730), IdleGame(app_id=570)],
    )

    result = validator.validate_account(account)

    assert result.state == "invalid"
    assert result.missing_app_ids == [570]
    assert result.matched_titles == {730: "Counter-Strike 2"}
