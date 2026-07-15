"""Read service for one control-plane instance workspace."""

from __future__ import annotations

from control_plane_kit.projections import (
    GraphVersionReadModel,
    WorkspaceReadModel,
    project_operator_graph_descriptor,
)
from control_plane_kit.stores import GraphTopologyStore, WorkspaceStore
from control_plane_kit.stores.records import GraphVersionRecord


class InstanceReadService:
    """Composes store truth into bounded read models."""

    def __init__(
        self,
        *,
        workspace_store: WorkspaceStore,
        graph_store: GraphTopologyStore,
        include_addresses: bool = False,
    ) -> None:
        self._workspace_store = workspace_store
        self._graph_store = graph_store
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
