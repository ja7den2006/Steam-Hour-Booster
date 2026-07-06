from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

from steam_hour_booster.paths import sessions_dir


class SessionStore:
    def __init__(self, base_dir: Path = None) -> None:
        self.base_dir = Path(base_dir) if base_dir is not None else sessions_dir()

    def build_profile_id(self, steam_id: str) -> str:
        return "steam_%s" % str(steam_id).strip()

    def bundle_path(self, profile_id: str) -> Path:
        return self.base_dir / ("%s.json" % profile_id)

    def client_credentials_dir(self, profile_id: str) -> Path:
        return self.base_dir / "cm_credentials" / str(profile_id).strip()

    def client_auth_cache_path(self, profile_id: str) -> Path:
        return self.client_credentials_dir(profile_id) / "client_auth.json"

    def save_bundle(self, profile_id: str, bundle: Dict[str, object]) -> Path:
        self.base_dir.mkdir(parents=True, exist_ok=True)
        path = self.bundle_path(profile_id)
        path.write_text(
            json.dumps(bundle, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return path

    def save_client_auth_cache(self, profile_id: str, payload: Dict[str, object]) -> Path:
        cache_path = self.client_auth_cache_path(profile_id)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return cache_path

    def delete_bundle(self, profile_id: str) -> None:
        path = self.bundle_path(profile_id)
        if path.exists():
            path.unlink()
        self.delete_client_credentials(profile_id)

    @staticmethod
    def load_bundle_path(path: str) -> Optional[Dict[str, object]]:
        resolved = Path(path)
        if not resolved.exists():
            return None
        return json.loads(resolved.read_text(encoding="utf-8"))

    @classmethod
    def summarize_bundle_path(cls, path: str) -> Dict[str, object]:
        resolved = Path(path)
        if not resolved.exists():
            return {
                "exists": False,
                "path": str(resolved),
            }

        payload = cls.load_bundle_path(str(resolved)) or {}
        stat = resolved.stat()
        modified_at = datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds")
        return {
            "exists": True,
            "path": str(resolved),
            "modified_at": modified_at,
            "steam_id": str(payload.get("steam_id", "")),
            "has_refresh_token": bool(payload.get("refresh_token")),
            "has_access_token": bool(payload.get("access_token")),
            "has_session_id": bool(payload.get("session_id")),
        }

    def load_client_auth_cache(self, profile_id: str) -> Dict[str, object]:
        cache_path = self.client_auth_cache_path(profile_id)
        if not cache_path.exists():
            return {}
        try:
            return json.loads(cache_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    @staticmethod
    def delete_bundle_path(path: str) -> None:
        resolved = Path(path)
        if resolved.exists():
            resolved.unlink()

    def delete_client_credentials(self, profile_id: str) -> None:
        credential_dir = self.client_credentials_dir(profile_id)
        if credential_dir.exists():
            shutil.rmtree(credential_dir, ignore_errors=True)
