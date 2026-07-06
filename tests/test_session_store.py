import json

from steam_hour_booster.session_store import SessionStore


def test_session_store_writes_bundle_file(tmp_path) -> None:
    store = SessionStore(base_dir=tmp_path / "sessions")
    path = store.save_bundle("steam_7656119", {"steam_id": "7656119", "refresh_token": "refresh"})

    assert path.exists()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["steam_id"] == "7656119"


def test_session_store_deletes_bundle_file(tmp_path) -> None:
    store = SessionStore(base_dir=tmp_path / "sessions")
    path = store.save_bundle("steam_7656119", {"steam_id": "7656119"})

    store.delete_bundle("steam_7656119")

    assert path.exists() is False


def test_session_store_summarizes_bundle_file(tmp_path) -> None:
    store = SessionStore(base_dir=tmp_path / "sessions")
    path = store.save_bundle(
        "steam_7656119",
        {
            "steam_id": "7656119",
            "refresh_token": "refresh",
            "client_refresh_token": "client-refresh",
            "access_token": "access",
            "session_id": "session",
        },
    )

    summary = store.summarize_bundle_path(str(path))

    assert summary["exists"] is True
    assert summary["steam_id"] == "7656119"
    assert summary["has_refresh_token"] is True
    assert summary["has_client_refresh_token"] is True
    assert summary["has_access_token"] is True
    assert summary["has_session_id"] is True
