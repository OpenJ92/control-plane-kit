"""Convert graph differences into activity plans."""

from __future__ import annotations

from control_plane_kit.core.activities import (
    Activity,
    ActivityPlan,
    AddEdge,
    RemoveEdge,
    StartNode,
    StopNode,
    SwitchEdge,
    VerifyNode,
)
from control_plane_kit.core.diff import diff_graphs
from control_plane_kit.core.graph import DeploymentGraph


def plan_migration(before: DeploymentGraph, after: DeploymentGraph) -> ActivityPlan:
    """Return a conservative activity plan for ``before -> after``.

    The planner intentionally starts new nodes before switching edges and stops
    removed nodes last.  That gives blue/green and retained-rollback workflows a
    sensible default order.
    """

    diff = diff_graphs(before, after)
    activities: list[Activity] = []

    for node in diff.added_nodes:
        activities.append(StartNode(node.node_id))
        if "health" in node.capabilities:
            activities.append(VerifyNode(node.node_id))

    for edge in diff.added_edges:
        activities.append(AddEdge(edge.edge_id, edge.source, edge.target))

    for old_edge, new_edge in diff.changed_edges:
        if old_edge.source == new_edge.source and old_edge.mutable:
            activities.append(
                SwitchEdge(
                    new_edge.edge_id,
                    new_edge.source,
                    old_edge.target,
                    new_edge.target,
                )
            )
        else:
            activities.append(RemoveEdge(old_edge.edge_id, old_edge.source, old_edge.target))
            activities.append(AddEdge(new_edge.edge_id, new_edge.source, new_edge.target))

    for edge in diff.removed_edges:
        activities.append(RemoveEdge(edge.edge_id, edge.source, edge.target))

    for node in diff.removed_nodes:
        activities.append(StopNode(node.node_id))

    return ActivityPlan(tuple(activities))
