"""Read service for one control-plane instance workspace."""

from __future__ import annotations

from control_plane_kit.projections import (
    ActivityEventReadModel,
    ActivityPlanTimelineReadModel,
    ActivityRunTimelineReadModel,
    ActivityTimelineReadModel,
    ControlSurfaceReadModel,
    GraphVersionReadModel,
    ObservationReadModel,
    ObservedStateReadModel,
    OperationActionReadModel,
    OperationSessionTimelineReadModel,
    WorkspaceReadModel,
    approval_descriptor,
    project_control_surface_descriptor,
    project_operator_graph_descriptor,
)
from control_plane_kit.stores import ActivityHistoryStore, GraphTopologyStore, ObservedStateStore, WorkspaceStore
from control_plane_kit.stores.records import ActivityPlanRecord, ActivityRunRecord
from control_plane_kit.stores.records import GraphVersionRecord


class InstanceReadService:
    """Composes store truth into bounded read models."""

    def __init__(
        self,
        *,
        workspace_store: WorkspaceStore,
        graph_store: GraphTopologyStore,
        activity_history_store: ActivityHistoryStore | None = None,
        observed_state_store: ObservedStateStore | None = None,
        include_addresses: bool = False,
    ) -> None:
        self._workspace_store = workspace_store
        self._graph_store = graph_store
        self._activity_history_store = activity_history_store
        self._observed_state_store = observed_state_store
        self._include_addresses = include_addresses

    def workspace(self, workspace_id: str) -> WorkspaceReadModel:
        """Return a read-only workspace summary."""

        workspace = self._workspace_store.get(workspace_id)
        current = self._graph_by_id(workspace.current_graph_id)
        desired = self._graph_by_id(workspace.desired_graph_id)
        return WorkspaceReadModel.from_record(
            workspace,
            current_graph=self._graph_read_model(current),
            desired_graph=self._graph_read_model(desired),
        )

    def current_graph(self, workspace_id: str) -> GraphVersionReadModel | None:
        """Return the current graph read model, if one is assigned."""

        workspace = self._workspace_store.get(workspace_id)
        return self._graph_read_model(self._graph_by_id(workspace.current_graph_id))

    def desired_graph(self, workspace_id: str) -> GraphVersionReadModel | None:
        """Return the desired graph read model, if one is assigned."""

        workspace = self._workspace_store.get(workspace_id)
        return self._graph_read_model(self._graph_by_id(workspace.desired_graph_id))

    def activity_timeline(self, workspace_id: str, *, limit: int = 50) -> ActivityTimelineReadModel:
        """Return a bounded activity timeline for one workspace."""

        _positive_limit(limit)
        self._workspace_store.get(workspace_id)
        if self._activity_history_store is None:
            return ActivityTimelineReadModel(workspace_id=workspace_id, limit=limit)
        sessions = self._activity_history_store.sessions_for_workspace(workspace_id, limit=limit)
        return ActivityTimelineReadModel(
            workspace_id=workspace_id,
            limit=limit,
            sessions=tuple(self._session_timeline(session.session_id) for session in sessions),
        )

    def observed_state(self, workspace_id: str, *, limit: int = 100) -> ObservedStateReadModel:
        """Return latest observed state summaries for one workspace."""

        _positive_limit(limit)
        self._workspace_store.get(workspace_id)
        if self._observed_state_store is None:
            return ObservedStateReadModel(workspace_id=workspace_id, limit=limit)
        return ObservedStateReadModel(
            workspace_id=workspace_id,
            limit=limit,
            observations=tuple(
                ObservationReadModel.from_record(record)
                for record in self._observed_state_store.latest_for_workspace(workspace_id, limit=limit)
            ),
        )

    def control_surface(self, workspace_id: str) -> ControlSurfaceReadModel | None:
        """Return declared capabilities, contracts, and control routes for the current graph."""

        workspace = self._workspace_store.get(workspace_id)
        graph = self._graph_by_id(workspace.current_graph_id)
        if graph is None:
            return None
        return project_control_surface_descriptor(
            workspace_id=workspace_id,
            graph_id=graph.graph_id,
            graph_descriptor=graph.graph_descriptor,
        )

    def _graph_by_id(self, graph_id: str | None) -> GraphVersionRecord | None:
        if graph_id is None:
            return None
        return self._graph_store.get(graph_id)

    def _graph_read_model(self, record: GraphVersionRecord | None) -> GraphVersionReadModel | None:
        if record is None:
            return None
        return GraphVersionReadModel.from_record(
            record,
            operator_graph=project_operator_graph_descriptor(
                record.graph_descriptor,
                include_addresses=self._include_addresses,
            ),
        )

    def _session_timeline(self, session_id: str) -> OperationSessionTimelineReadModel:
        if self._activity_history_store is None:
            raise RuntimeError("activity history store is required")
        session = self._activity_history_store.get_session(session_id)
        return OperationSessionTimelineReadModel.from_record(
            session,
            actions=tuple(
                OperationActionReadModel.from_record(action)
                for action in self._activity_history_store.actions_for_session(session_id)
            ),
            approvals=tuple(
                approval_descriptor(approval)
                for approval in self._activity_history_store.approvals_for_session(session_id)
            ),
            plans=tuple(self._plan_timeline(plan) for plan in self._activity_history_store.plans_for_session(session_id)),
        )

    def _plan_timeline(self, plan: ActivityPlanRecord) -> ActivityPlanTimelineReadModel:
        if self._activity_history_store is None:
            raise RuntimeError("activity history store is required")
        return ActivityPlanTimelineReadModel.from_record(
            plan,
            runs=tuple(self._run_timeline(run) for run in self._activity_history_store.runs_for_plan(plan.plan_id)),
        )

    def _run_timeline(self, run: ActivityRunRecord) -> ActivityRunTimelineReadModel:
        if self._activity_history_store is None:
            raise RuntimeError("activity history store is required")
        return ActivityRunTimelineReadModel.from_record(
            run,
            events=tuple(
                ActivityEventReadModel.from_record(event)
                for event in self._activity_history_store.events_for_run(run.run_id, limit=100)
            ),
        )


def _positive_limit(limit: int) -> None:
    if limit < 1:
        raise ValueError("limit must be a positive integer")
