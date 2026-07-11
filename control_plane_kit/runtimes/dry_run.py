"""No-side-effect runtime interpreter."""

from __future__ import annotations

from control_plane_kit.core.activities import ActivityPlan
from control_plane_kit.runtimes.base import RuntimeResult


class DryRunRuntime:
    """Interpret activities as readable messages without doing work."""

    def execute(self, plan: ActivityPlan) -> tuple[RuntimeResult, ...]:
        """Return one ``planned`` result per activity."""

        return tuple(
            RuntimeResult(activity, "planned", activity.to_text())
            for activity in plan.activities
        )
