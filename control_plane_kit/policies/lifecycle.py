"""Lifecycle retention laws for control-plane instances and workspaces."""

from __future__ import annotations

from dataclasses import dataclass

from control_plane_kit.core.types import WorkspaceLifecycle


@dataclass(frozen=True)
class LifecycleRetention:
    """What remains available in one lifecycle state."""

    lifecycle: WorkspaceLifecycle
    keeps_workspace_record: bool
    keeps_graph_history: bool
    keeps_activity_history: bool
    keeps_observed_state: bool
    keeps_runtime_resources: bool
    destructive: bool = False


_RETENTION: dict[WorkspaceLifecycle, LifecycleRetention] = {
    WorkspaceLifecycle.CREATED: LifecycleRetention(
        lifecycle=WorkspaceLifecycle.CREATED,
        keeps_workspace_record=True,
        keeps_graph_history=True,
        keeps_activity_history=True,
        keeps_observed_state=True,
        keeps_runtime_resources=False,
    ),
    WorkspaceLifecycle.RUNNING: LifecycleRetention(
        lifecycle=WorkspaceLifecycle.RUNNING,
        keeps_workspace_record=True,
        keeps_graph_history=True,
        keeps_activity_history=True,
        keeps_observed_state=True,
        keeps_runtime_resources=True,
    ),
    WorkspaceLifecycle.PAUSED: LifecycleRetention(
        lifecycle=WorkspaceLifecycle.PAUSED,
        keeps_workspace_record=True,
        keeps_graph_history=True,
        keeps_activity_history=True,
        keeps_observed_state=True,
        keeps_runtime_resources=True,
    ),
    WorkspaceLifecycle.STOPPED: LifecycleRetention(
        lifecycle=WorkspaceLifecycle.STOPPED,
        keeps_workspace_record=True,
        keeps_graph_history=True,
        keeps_activity_history=True,
        keeps_observed_state=True,
        keeps_runtime_resources=False,
    ),
    WorkspaceLifecycle.ARCHIVED: LifecycleRetention(
        lifecycle=WorkspaceLifecycle.ARCHIVED,
        keeps_workspace_record=True,
        keeps_graph_history=True,
        keeps_activity_history=True,
        keeps_observed_state=False,
        keeps_runtime_resources=False,
    ),
    WorkspaceLifecycle.DECONSTRUCTED: LifecycleRetention(
        lifecycle=WorkspaceLifecycle.DECONSTRUCTED,
        keeps_workspace_record=True,
        keeps_graph_history=True,
        keeps_activity_history=True,
        keeps_observed_state=False,
        keeps_runtime_resources=False,
    ),
    WorkspaceLifecycle.DELETED: LifecycleRetention(
        lifecycle=WorkspaceLifecycle.DELETED,
        keeps_workspace_record=False,
        keeps_graph_history=False,
        keeps_activity_history=False,
        keeps_observed_state=False,
        keeps_runtime_resources=False,
        destructive=True,
    ),
    WorkspaceLifecycle.FAILED: LifecycleRetention(
        lifecycle=WorkspaceLifecycle.FAILED,
        keeps_workspace_record=True,
        keeps_graph_history=True,
        keeps_activity_history=True,
        keeps_observed_state=True,
        keeps_runtime_resources=True,
    ),
}


def retention_for(lifecycle: WorkspaceLifecycle) -> LifecycleRetention:
    """Return the retention law for a lifecycle state."""

    return _RETENTION[lifecycle]
