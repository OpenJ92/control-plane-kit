"""Graph diffing and conservative activity planning."""

from __future__ import annotations

from dataclasses import dataclass

from control_plane_kit.graph import DeploymentGraph


@dataclass(frozen=True)
class GraphDiff:
    """Structural difference between two graphs."""

    added_nodes: tuple[str, ...] = ()
    removed_nodes: tuple[str, ...] = ()
    changed_nodes: tuple[str, ...] = ()
    added_edges: tuple[str, ...] = ()
    removed_edges: tuple[str, ...] = ()
    changed_edges: tuple[str, ...] = ()

    def summary(self) -> str:
        parts = []
        for label, values in (
            ("added nodes", self.added_nodes),
            ("removed nodes", self.removed_nodes),
            ("changed nodes", self.changed_nodes),
            ("added edges", self.added_edges),
            ("removed edges", self.removed_edges),
            ("changed edges", self.changed_edges),
        ):
            if values:
                parts.append(f"{label}: {', '.join(values)}")
        return "\n".join(parts) if parts else "no changes"


@dataclass(frozen=True)
class Activity:
    """One planned action."""

    action: str
    subject: str
    detail: str = ""

    def text(self) -> str:
        suffix = f" - {self.detail}" if self.detail else ""
        return f"{self.action}({self.subject}){suffix}"


@dataclass(frozen=True)
class ActivityPlan:
    """Conservative linear activity plan."""

    activities: tuple[Activity, ...]

    def to_text(self) -> str:
        return "\n".join(
            f"{index}. {activity.text()}"
            for index, activity in enumerate(self.activities, start=1)
        )


def diff_graphs(current: DeploymentGraph, desired: DeploymentGraph) -> GraphDiff:
    """Return a structural graph diff."""

    current_nodes = set(current.nodes)
    desired_nodes = set(desired.nodes)
    current_edges = set(current.edges)
    desired_edges = set(desired.edges)
    changed_nodes = tuple(
        sorted(
            node_id
            for node_id in current_nodes & desired_nodes
            if current.nodes[node_id] != desired.nodes[node_id]
        )
    )
    changed_edges = tuple(
        sorted(
            edge_id
            for edge_id in current_edges & desired_edges
            if current.edges[edge_id] != desired.edges[edge_id]
        )
    )
    return GraphDiff(
        added_nodes=tuple(sorted(desired_nodes - current_nodes)),
        removed_nodes=tuple(sorted(current_nodes - desired_nodes)),
        changed_nodes=changed_nodes,
        added_edges=tuple(sorted(desired_edges - current_edges)),
        removed_edges=tuple(sorted(current_edges - desired_edges)),
        changed_edges=changed_edges,
    )


def plan_migration(current: DeploymentGraph, desired: DeploymentGraph) -> ActivityPlan:
    """Plan a conservative linear migration from current to desired."""

    diff = diff_graphs(current, desired)
    activities: list[Activity] = []
    for node_id in diff.added_nodes:
        activities.append(Activity("StartNode", node_id))
        activities.append(Activity("HealthCheck", node_id, "if node advertises health"))
    for edge_id in diff.added_edges:
        activities.append(Activity("AddSocketConnection", edge_id))
    for edge_id in diff.changed_edges:
        activities.append(Activity("SwitchSocketConnection", edge_id))
    for edge_id in diff.removed_edges:
        activities.append(Activity("RemoveSocketConnection", edge_id))
    for node_id in diff.changed_nodes:
        activities.append(Activity("ReconcileNode", node_id))
    for node_id in diff.removed_nodes:
        activities.append(Activity("StopNode", node_id, "after verification"))
    return ActivityPlan(tuple(activities))
