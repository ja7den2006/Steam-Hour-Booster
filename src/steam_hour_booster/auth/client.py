from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional

from steam_hour_booster.session_store import SessionStore


def _iso_timestamp() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _console_log(message: str) -> None:
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] [finish-booster-login] {message}", flush=True)


@dataclass
class ClientAuthResult:
    account_name: str
    steam_id: str
    refresh_token: str
    cache_path: str
    token_expires_at: int = 0
    source: str = "steam-user"


class SteamClientAuthError(RuntimeError):
    pass


class SteamClientGuardRequiredError(SteamClientAuthError):
    def __init__(self, *, code_kind: str, message: str, associated_message: str = "") -> None:
        super().__init__(message)
        self.code_kind = code_kind
        self.associated_message = associated_message


class SteamClientAuthGateway:
    def __init__(
        self,
        *,
        session_store: Optional[SessionStore] = None,
        node_executable: str = "node",
        bridge_script: Optional[Path] = None,
        timeout_seconds: float = 90.0,
        bridge_runner: Optional[Callable[[Dict[str, object]], Dict[str, object]]] = None,
    ) -> None:
        self._session_store = session_store or SessionStore()
        self._node_executable = node_executable
        self._bridge_script = bridge_script or self._default_bridge_script()
        self._timeout_seconds = max(15.0, float(timeout_seconds))
        self._bridge_runner = bridge_runner

    def authorize_credentials(
        self,
        *,
        profile_id: str,
        account_name: str,
        steam_id: str,
        password: str,
        steam_guard_code: str = "",
        steam_guard_code_kind: str = "",
    ) -> ClientAuthResult:
        credential_dir = self._session_store.client_credentials_dir(profile_id)
        credential_dir.mkdir(parents=True, exist_ok=True)
        _console_log(
            "Starting modern Steam client authorization for account=%s steam_id=%s profile_id=%s credential_dir=%s guard_kind=%s guard_supplied=%s"
            % (
                account_name,
                steam_id,
                profile_id,
                credential_dir,
                str(steam_guard_code_kind or "").strip().lower() or "none",
                "yes" if str(steam_guard_code or "").strip() else "no",
            )
        )

        payload = {
            "profileId": profile_id,
            "accountName": account_name,
            "steamId": steam_id,
            "password": password,
            "steamGuardCode": steam_guard_code,
            "steamGuardCodeKind": steam_guard_code_kind,
            "dataDirectory": str(credential_dir),
            "machineName": "Steam Hour Booster",
            "timeoutMs": int(self._timeout_seconds * 1000),
        }
        response = self._run_bridge(payload)
        status = str(response.get("status", "") or "").strip().lower()

        if status == "steam_guard_required":
            code_kind = str(response.get("code_kind", "") or "generic").strip().lower() or "generic"
            _console_log("Steam requested a %s Guard code through the modern auth bridge." % code_kind)
            raise SteamClientGuardRequiredError(
                code_kind=code_kind,
                associated_message=str(response.get("associated_message", "") or ""),
                message=str(response.get("message", "") or "A Steam Guard code is required for Steam client authorization."),
            )

        if status != "success":
            message = str(response.get("message", "") or "Steam client authorization failed.")
            eresult_name = str(response.get("eresult_name", "") or "").strip()
            if eresult_name and eresult_name not in message:
                message = "%s (%s)" % (message, eresult_name)
            _console_log("Modern Steam client authorization failed: %s" % message)
            raise SteamClientAuthError(message)

        refresh_token = str(response.get("refresh_token", "") or "").strip()
        if not refresh_token:
            raise SteamClientAuthError("Steam client authorization completed, but no client refresh token was returned.")

        resolved_steam_id = str(response.get("steam_id", "") or steam_id or "").strip()
        resolved_account_name = str(response.get("account_name", "") or account_name or "").strip()
        token_expires_at = self._coerce_int(response.get("token_expires_at"))
        cache_path = self._session_store.save_client_auth_cache(
            profile_id,
            {
                "account_name": resolved_account_name,
                "steam_id": resolved_steam_id,
                "client_refresh_token": refresh_token,
                "token_expires_at": token_expires_at,
                "source": str(response.get("source", "") or "steam-user"),
                "updated_at": _iso_timestamp(),
            },
        )
        _console_log(
            "Saved Steam client refresh token metadata to %s and completed authorization."
            % cache_path
        )

        return ClientAuthResult(
            account_name=resolved_account_name,
            steam_id=resolved_steam_id,
            refresh_token=refresh_token,
            cache_path=str(cache_path),
            token_expires_at=token_expires_at,
            source=str(response.get("source", "") or "steam-user"),
        )

    def _run_bridge(self, payload: Dict[str, object]) -> Dict[str, object]:
        if self._bridge_runner is not None:
            return dict(self._bridge_runner(dict(payload)))

        node_path = self._resolve_node_executable()
        if not node_path:
            raise SteamClientAuthError("Node.js is required for Steam client authorization, but it was not found.")
        if not self._bridge_script.exists():
            raise SteamClientAuthError("Steam client bridge script is missing: %s" % self._bridge_script)

        _console_log("Launching Steam client bridge with node=%s script=%s" % (node_path, self._bridge_script))
        try:
            completed = subprocess.run(
                [node_path, str(self._bridge_script), "authorize"],
                input=json.dumps(payload),
                stdout=subprocess.PIPE,
                stderr=None,
                text=True,
                timeout=self._timeout_seconds + 5.0,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise SteamClientAuthError("Steam client bridge timed out before returning a result.") from exc
        except OSError as exc:
            raise SteamClientAuthError("Unable to launch Steam client bridge: %s" % exc) from exc

        raw_stdout = str(completed.stdout or "").strip()
        if completed.returncode != 0:
            raise SteamClientAuthError(
                "Steam client bridge exited with code %s. Output: %s"
                % (completed.returncode, raw_stdout or "<empty>")
            )
        if not raw_stdout:
            raise SteamClientAuthError("Steam client bridge returned no JSON response.")

        last_line = raw_stdout.splitlines()[-1]
        try:
            parsed = json.loads(last_line)
        except json.JSONDecodeError as exc:
            raise SteamClientAuthError("Steam client bridge returned invalid JSON: %s" % last_line) from exc
        if not isinstance(parsed, dict):
            raise SteamClientAuthError("Steam client bridge returned an invalid response payload.")
        return parsed

    @staticmethod
    def _default_bridge_script() -> Path:
        return Path(__file__).resolve().parents[1] / "node" / "steam_client_bridge.cjs"

    def _resolve_node_executable(self) -> str:
        for candidate in self._candidate_node_paths(self._node_executable):
            if candidate.exists():
                return str(candidate)

        resolved = shutil.which(self._node_executable)
        if resolved:
            return resolved

        return self._node_executable if Path(self._node_executable).name != self._node_executable else ""

    @staticmethod
    def _candidate_node_paths(node_executable: str) -> List[Path]:
        executable_name = "node.exe" if os.name == "nt" else "node"
        normalized = str(node_executable or "").strip() or executable_name
        candidates: List[Path] = []

        env_path = str(os.getenv("STEAM_HOUR_BOOSTER_NODE", "") or "").strip()
        if env_path:
            candidates.append(Path(env_path))

        requested = Path(normalized)
        if requested.name != normalized or requested.is_absolute():
            candidates.append(requested)

        runtime_bases: List[Path] = []
        frozen_base = str(getattr(sys, "_MEIPASS", "") or "").strip()
        if frozen_base:
            runtime_bases.append(Path(frozen_base).resolve())
        if getattr(sys, "frozen", False):
            runtime_bases.append(Path(sys.executable).resolve().parent)
        runtime_bases.append(Path.cwd().resolve())

        for base in runtime_bases:
            candidates.extend(
                [
                    base / "node_runtime" / executable_name,
                    base / "_internal" / "node_runtime" / executable_name,
                ]
            )

        project_root = Path(__file__).resolve().parents[2]
        candidates.append(project_root / "node_runtime" / executable_name)

        unique_candidates: List[Path] = []
        seen = set()
        for candidate in candidates:
            key = str(candidate)
            if key not in seen:
                seen.add(key)
                unique_candidates.append(candidate)
        return unique_candidates

    @staticmethod
    def _coerce_int(value: object) -> int:
        try:
            return int(value)
        except Exception:
            return 0
