from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from steam_hour_booster.models import AppConfig
from steam_hour_booster.paths import app_data_dir, config_path, logs_dir, sessions_dir


def ensure_runtime_directories() -> None:
    for directory in (app_data_dir(), sessions_dir(), logs_dir()):
        directory.mkdir(parents=True, exist_ok=True)


class ConfigStore:
    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = path or config_path()

    def load(self) -> AppConfig:
        ensure_runtime_directories()
        if not self.path.exists():
            return AppConfig()
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        return AppConfig.from_dict(payload)

    def save(self, config: AppConfig) -> Path:
        ensure_runtime_directories()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(config.to_dict(), indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return self.path
