from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Protocol

from steam_hour_booster.models import AccountProfile


class RuntimeState(str, Enum):
    IDLE = "idle"
    STARTING = "starting"
    BOOSTING = "boosting"
    PAUSED = "paused"
    ERROR = "error"


@dataclass
class AccountRuntimeStatus:
    profile_id: str
    display_name: str
    state: RuntimeState = RuntimeState.IDLE
    active_app_ids: List[int] = field(default_factory=list)
    message: str = ""


@dataclass
class RuntimeSnapshot:
    accounts: Dict[str, AccountRuntimeStatus] = field(default_factory=dict)


class BoosterRuntime(Protocol):
    def start(self, accounts: List[AccountProfile]) -> None:
        ...

    def stop(self) -> None:
        ...

    def snapshot(self) -> RuntimeSnapshot:
        ...
