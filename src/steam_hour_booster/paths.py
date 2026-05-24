from __future__ import annotations

import os
from pathlib import Path


APP_DIR_NAME = "SteamHourBooster"


def app_data_dir() -> Path:
    local_app_data = os.getenv("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / APP_DIR_NAME
    return Path.home() / ".steam-hour-booster"


def config_path() -> Path:
    return app_data_dir() / "config.json"


def sessions_dir() -> Path:
    return app_data_dir() / "sessions"


def logs_dir() -> Path:
    return app_data_dir() / "logs"
