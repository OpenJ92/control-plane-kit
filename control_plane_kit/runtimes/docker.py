"""Docker runtime sketch.

This module is intentionally not a production executor yet.  It records the
shape of the interpreter boundary without pretending that every node kind can be
started safely from generic metadata.
"""

from __future__ import annotations

from control_plane_kit.core.activities import ActivityPlan, StartNode, StopNode, SwitchEdge
from control_plane_kit.runtimes.base import RuntimeResult


class DockerRuntime:
    """A conservative Docker interpreter skeleton.

    A real implementation should require explicit image, command, network, and
    secret-provider metadata before starting anything.  That keeps the graph
    language honest and avoids inventing unsafe defaults.
    """

    def execute(self, plan: ActivityPlan) -> tuple[RuntimeResult, ...]:
        """Return unsupported results for activities needing real Docker state."""

        results: list[RuntimeResult] = []
        for activity in plan.activities:
            if isinstance(activity, (StartNode, StopNode, SwitchEdge)):
                results.append(
                    RuntimeResult(
                        activity,
                        "unsupported",
                        "DockerRuntime requires concrete node implementation metadata",
                    )
                )
            else:
                results.append(RuntimeResult(activity, "skipped", activity.to_text()))
        return tuple(results)
