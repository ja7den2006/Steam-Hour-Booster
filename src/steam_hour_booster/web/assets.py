from __future__ import annotations

import base64
from pathlib import Path


WEB_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = WEB_DIR.parents[2]
PACKAGED_STEAM_ICON_PATH = WEB_DIR / "static" / "steam_icon.png"
LOCAL_STEAM_ICON_PATH = PROJECT_ROOT / "tmp" / "steam_icon.png"
FALLBACK_STEAM_ICON_SVG = """
<svg viewBox="0 0 64 64" xmlns="http://www.w3.org/2000/svg">
  <circle cx="32" cy="32" r="30" fill="#111821"/>
  <circle cx="43" cy="21" r="10" fill="none" stroke="#ffffff" stroke-width="5"/>
  <circle cx="43" cy="21" r="4" fill="#ffffff"/>
  <path d="M17 41.5 28.5 46a8 8 0 1 0 4.7-10.7L22.5 31" fill="none" stroke="#ffffff" stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/>
  <circle cx="27" cy="44.5" r="3.4" fill="#ffffff"/>
</svg>
""".strip()


def _read_asset(name: str) -> str:
    return (WEB_DIR / name).read_text(encoding="utf-8")


def _data_url_from_bytes(payload: bytes, mime_type: str) -> str:
    encoded = base64.b64encode(payload).decode("ascii")
    return "data:%s;base64,%s" % (mime_type, encoded)


def _steam_icon_src() -> str:
    for icon_path in (PACKAGED_STEAM_ICON_PATH, LOCAL_STEAM_ICON_PATH):
        if icon_path.exists():
            return _data_url_from_bytes(icon_path.read_bytes(), "image/png")
    return _data_url_from_bytes(FALLBACK_STEAM_ICON_SVG.encode("utf-8"), "image/svg+xml")


def load_shell_html() -> str:
    template = _read_asset("template.html")
    styles = _read_asset("styles.css")
    script = _read_asset("app.js")
    steam_icon_src = _steam_icon_src()
    return (
        template.replace("__INLINE_STYLES__", styles)
        .replace("__STEAM_ICON_SRC__", steam_icon_src)
        .replace("__INLINE_SCRIPT__", script)
    )
