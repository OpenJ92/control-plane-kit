"""Graph diffing and conservative activity planning."""

from __future__ import annotations

from dataclasses import dataclass

from control_plane_kit.graph_changes import (
    AddedChange,
    AmbiguousChange,
    FieldSubject,
    GraphDiff,
    ModifiedChange,
    RemovedChange,
    UnsupportedChange,
)
from control_plane_kit.validation import EdgeSubject, NodeSubject


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


def plan_migration(diff: GraphDiff) -> ActivityPlan:
    """Adapt typed diff data to the prototype plan until Roadmap 0007.6 replaces it."""

    activities: list[Activity] = []
    added_nodes = sorted(
        change.subject.node_id
        for change in diff.changes
        if isinstance(change, AddedChange) and isinstance(change.subject, NodeSubject)
    )
    removed_nodes = sorted(
        change.subject.node_id
        for change in diff.changes
        if isinstance(change, RemovedChange) and isinstance(change.subject, NodeSubject)
    )
    added_edges = sorted(
        change.subject.edge_id
        for change in diff.changes
        if isinstance(change, AddedChange) and isinstance(change.subject, EdgeSubject)
    )
    removed_edges = sorted(
        change.subject.edge_id
        for change in diff.changes
        if isinstance(change, RemovedChange) and isinstance(change.subject, EdgeSubject)
    )
    changed_edges = sorted(
        change.subject.edge_id
        for change in diff.changes
        if isinstance(change, ModifiedChange) and isinstance(change.subject, EdgeSubject)
    )
    changed_nodes = sorted(
        {
            node_id
            for change in diff.changes
            if isinstance(change, (ModifiedChange, UnsupportedChange, AmbiguousChange))
            for node_id in _subject_node_ids(change.subject)
        }
    )
    for node_id in added_nodes:
        activities.append(Activity("StartNode", node_id))
        activities.append(Activity("HealthCheck", node_id, "if node advertises health"))
    for edge_id in added_edges:
        activities.append(Activity("AddSocketConnection", edge_id))
    for edge_id in changed_edges:
        activities.append(Activity("SwitchSocketConnection", edge_id))
    for edge_id in removed_edges:
        activities.append(Activity("RemoveSocketConnection", edge_id))
    for node_id in changed_nodes:
        activities.append(Activity("ReconcileNode", node_id))
    for node_id in removed_nodes:
        activities.append(Activity("StopNode", node_id, "after verification"))
    return ActivityPlan(tuple(activities))


def _subject_node_ids(subject: object) -> tuple[str, ...]:
    if isinstance(subject, NodeSubject):
        return (subject.node_id,)
    if isinstance(subject, FieldSubject) and isinstance(subject.owner, NodeSubject):
        return (subject.owner.node_id,)
    return ()
