from steam_hour_booster.models import AccountProfile, IdleGame
from steam_hour_booster.runtime import RuntimeController, RuntimeState
from steam_hour_booster.session_store import SessionStore


def test_runtime_controller_marks_ready_accounts_from_saved_sessions(tmp_path) -> None:
    session_store = SessionStore(base_dir=tmp_path / "sessions")
    bundle_path = session_store.save_bundle("steam_7656119", {"steam_id": "7656119", "refresh_token": "refresh"})
    controller = RuntimeController(session_store=session_store)
    account = AccountProfile(
        profile_id="steam_7656119",
        display_name="Primary",
        steam_id="7656119",
        session_bundle_path=str(bundle_path),
        games=[IdleGame(app_id=730), IdleGame(app_id=570)],
    )

    snapshot = controller.refresh_accounts([account]).to_dict()
    status = snapshot["statuses"][0]

    assert snapshot["counts"]["ready_accounts"] == 1
    assert status["state"] == "ready"
    assert status["session_ready"] is True
    assert status["configured_slot_count"] == 2


def test_runtime_controller_starts_and_stops_preview_lane(tmp_path) -> None:
    session_store = SessionStore(base_dir=tmp_path / "sessions")
    bundle_path = session_store.save_bundle("steam_7656119", {"steam_id": "7656119", "refresh_token": "refresh"})
    controller = RuntimeController(session_store=session_store)
    account = AccountProfile(
        profile_id="steam_7656119",
        display_name="Primary",
        steam_id="7656119",
        session_bundle_path=str(bundle_path),
        games=[IdleGame(app_id=730), IdleGame(app_id=570)],
    )

    started = controller.start_profile("steam_7656119", [account])
    assert started.state == RuntimeState.BOOSTING
    assert started.active_app_ids == [730, 570]

    stopped = controller.stop_profile("steam_7656119", [account])

    assert stopped.state == RuntimeState.READY
    assert stopped.active_app_ids == []
    assert "Ready to start" in stopped.message


def test_runtime_controller_flags_missing_sessions_and_empty_slots(tmp_path) -> None:
    session_store = SessionStore(base_dir=tmp_path / "sessions")
    controller = RuntimeController(session_store=session_store)
    missing_session = AccountProfile(
        profile_id="missing",
        display_name="Missing Session",
        steam_id="7656119",
        session_bundle_path=str(tmp_path / "sessions" / "missing.json"),
        games=[IdleGame(app_id=730)],
    )
    empty_bundle = session_store.save_bundle("empty", {"steam_id": "7656120", "refresh_token": "refresh"})
    empty_slots = AccountProfile(
        profile_id="empty",
        display_name="Empty Slots",
        steam_id="7656120",
        session_bundle_path=str(empty_bundle),
    )

    snapshot = controller.refresh_accounts([missing_session, empty_slots]).to_dict()
    states = {item["profile_id"]: item["state"] for item in snapshot["statuses"]}

    assert states["missing"] == "error"
    assert states["empty"] == "idle"
    assert snapshot["counts"]["error_accounts"] >= 1
