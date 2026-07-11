"""Runtime protocol for activity interpreters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from control_plane_kit.core.activities import Activity, ActivityPlan


@dataclass(frozen=True)
class RuntimeResult:
    """Result of interpreting one activity."""

    activity: Activity
    status: str
    message: str


class Runtime(Protocol):
    """A side-effect interpreter for an activity plan."""

    def execute(self, plan: ActivityPlan) -> tuple[RuntimeResult, ...]:
        """Execute or simulate a plan."""

        ...
