from pathlib import Path

from steam_hour_booster import app


def test_resolve_window_icon_path_prefers_repo_tmp_icon(monkeypatch, tmp_path) -> None:
    repo_root = tmp_path / "repo"
    package_dir = repo_root / "src" / "steam_hour_booster"
    package_dir.mkdir(parents=True)
    icon_path = repo_root / "tmp" / "steam_icon.ico"
    icon_path.parent.mkdir(parents=True)
    icon_path.write_bytes(b"ico")

    monkeypatch.setattr(app, "__file__", str(package_dir / "app.py"))

    assert app.resolve_window_icon_path() == icon_path


def test_resolve_window_icon_path_returns_none_when_missing(monkeypatch, tmp_path) -> None:
    repo_root = tmp_path / "repo"
    package_dir = repo_root / "src" / "steam_hour_booster"
    package_dir.mkdir(parents=True)

    monkeypatch.setattr(app, "__file__", str(package_dir / "app.py"))

    assert app.resolve_window_icon_path() is None
