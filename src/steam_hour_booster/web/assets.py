from __future__ import annotations

from pathlib import Path


WEB_DIR = Path(__file__).resolve().parent


def _read_asset(name: str) -> str:
    return (WEB_DIR / name).read_text(encoding="utf-8")


def load_shell_html() -> str:
    template = _read_asset("template.html")
    styles = _read_asset("styles.css")
    script = _read_asset("app.js")
    return (
        template.replace("__INLINE_STYLES__", styles)
        .replace("__INLINE_SCRIPT__", script)
    )
