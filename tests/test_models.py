from steam_hour_booster.models import AccountProfile, AppConfig, IdleGame


def test_app_config_round_trip() -> None:
    config = AppConfig(
        theme="ember",
        window_width=1440,
        window_height=900,
        last_page="runtime",
        accounts=[
            AccountProfile(
                profile_id="primary",
                display_name="Primary Account",
                account_name="jayden_main",
                steam_id="76561198000000000",
                login_mode="qr",
                boost_enabled=False,
                appear_online=False,
                conflict_policy="kick",
                custom_status="Boosting library",
                games=[
                    IdleGame(app_id=730, title="Counter-Strike 2"),
                    IdleGame(app_id=570, title="Dota 2", enabled=False),
                ],
            )
        ],
    )

    payload = config.to_dict()
    restored = AppConfig.from_dict(payload)

    assert restored.theme == "ember"
    assert restored.last_page == "runtime"
    assert len(restored.accounts) == 1
    assert restored.accounts[0].boost_enabled is False
    assert restored.accounts[0].appear_online is False
    assert restored.accounts[0].conflict_policy == "kick"
    assert restored.accounts[0].games[0].app_id == 730
    assert restored.accounts[0].games[1].enabled is False
