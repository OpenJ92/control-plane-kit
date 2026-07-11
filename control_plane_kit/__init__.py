"""Public entry points for control-plane-kit.

The package is intentionally small at the top level.  Most applications only
need graph values, graph comparison, and a migration planner.  Runtime-specific
interpreters live under :mod:`control_plane_kit.runtimes`.
"""

from control_plane_kit.core.activities import (
    Activity,
    ActivityPlan,
    StartNode,
    StopNode,
    SwitchEdge,
)
from control_plane_kit.core.diff import GraphDiff, diff_graphs
from control_plane_kit.core.graph import DeploymentGraph, Edge, Endpoint, Node
from control_plane_kit.core.planner import plan_migration

__all__ = [
    "Activity",
    "ActivityPlan",
    "DeploymentGraph",
    "Edge",
    "Endpoint",
    "GraphDiff",
    "Node",
    "StartNode",
    "StopNode",
    "SwitchEdge",
    "diff_graphs",
    "plan_migration",
]
