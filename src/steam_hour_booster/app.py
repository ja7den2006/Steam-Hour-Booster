from __future__ import annotations

from pathlib import Path
from typing import Optional

import webview

from steam_hour_booster.config_store import ConfigStore, ensure_runtime_directories
from steam_hour_booster.paths import app_data_dir
from steam_hour_booster.web.assets import load_shell_html
from steam_hour_booster.web.bridge import DesktopApi


def resolve_window_icon_path() -> Optional[Path]:
    candidate = Path(__file__).resolve().parents[2] / "tmp" / "steam_icon.ico"
    if candidate.exists():
        return candidate
    return None


def create_api(config_store: Optional[ConfigStore] = None) -> DesktopApi:
    resolved_store = config_store or ConfigStore()
    config = resolved_store.load()
    return DesktopApi(config_store=resolved_store, config=config)


def create_window(api: DesktopApi):
    config = api.config
    x = int(config.window_x) if config.window_x is not None else None
    y = int(config.window_y) if config.window_y is not None else None
    window = webview.create_window(
        "Steam Hour Booster",
        html=load_shell_html(),
        js_api=api,
        width=int(config.window_width),
        height=int(config.window_height),
        x=x,
        y=y,
        min_size=(980, 640),
        frameless=True,
        easy_drag=False,
        shadow=True,
        background_color="#090C10",
        text_select=False,
    )
    api.attach_window(window)
    return window


def main() -> int:
    ensure_runtime_directories()
    storage_path = Path(app_data_dir()) / "webview"
    storage_path.mkdir(parents=True, exist_ok=True)
    icon_path = resolve_window_icon_path()

    api = create_api()
    create_window(api)
    webview.start(
        debug=False,
        private_mode=True,
        storage_path=str(storage_path),
        icon=str(icon_path) if icon_path else None,
    )
    return 0
