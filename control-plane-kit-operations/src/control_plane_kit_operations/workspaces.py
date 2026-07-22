"""Workspace command service for operations-owned graph truth."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from control_plane_kit_core.topology import DeploymentGraph
from control_plane_kit_operations.records import GraphVersionRecord, WorkspaceRecord
from control_plane_kit_operations.workflows import IdempotencyKey


class WorkspaceCommandError(RuntimeError):
    """Raised when workspace command data or state is invalid."""


@dataclass(frozen=True)
class CreateWorkspace:
    """Create one workspace with an initial empty current graph."""

    workspace_id: str
    name: str
    actor_id: str
    idempotency_key: IdempotencyKey
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _required_text(self.workspace_id, "workspace_id")
        _required_text(self.name, "name")
        _required_text(self.actor_id, "actor_id")
        if not isinstance(self.idempotency_key, IdempotencyKey):
            raise WorkspaceCommandError("idempotency_key must be IdempotencyKey")
        if not isinstance(self.metadata, Mapping):
            raise WorkspaceCommandError("metadata must be a mapping")


@dataclass(frozen=True)
class CreateWorkspaceResult:
    """Committed workspace and initial graph evidence."""

    workspace: WorkspaceRecord
    current_graph: GraphVersionRecord
    replayed: bool = False

    def descriptor(self) -> dict[str, object]:
        return {
            "workspace": {
                "workspace_id": self.workspace.workspace_id,
                "name": self.workspace.name,
                "lifecycle": self.workspace.lifecycle.value,
                "current_graph_id": self.workspace.current_graph_id,
                "desired_graph_id": self.workspace.desired_graph_id,
            },
            "current_graph": {
                "graph_id": self.current_graph.graph_id,
                "version": self.current_graph.version,
                "graph_name": self.current_graph.graph_descriptor.get("name"),
            },
            "replayed": self.replayed,
        }


class WorkspaceCommandService:
    """Application service owning workspace creation transaction boundaries."""

    def __init__(
        self,
        unit_of_work_factory: Callable[[], Any],
        *,
        clock: Callable[[], str],
        id_factory: Callable[[], str],
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._clock = clock
        self._id_factory = id_factory

    def create(self, command: CreateWorkspace) -> CreateWorkspaceResult:
        if not isinstance(command, CreateWorkspace):
            raise WorkspaceCommandError("create requires CreateWorkspace")
        with self._unit_of_work_factory() as unit_of_work:
            try:
                existing = unit_of_work.stores.workspaces.get(command.workspace_id)
            except KeyError:
                existing = None
            if existing is not None:
                if existing.name != command.name:
                    raise WorkspaceCommandError(
                        "workspace id already exists with different name"
                    )
                if existing.current_graph_id is None:
                    raise WorkspaceCommandError(
                        "workspace exists without initial current graph"
                    )
                graph = unit_of_work.stores.graphs.get(existing.current_graph_id)
                unit_of_work.commit()
                return CreateWorkspaceResult(existing, graph, replayed=True)

            current_graph = GraphVersionRecord.from_graph(
                graph_id=self._id_factory(),
                workspace_id=command.workspace_id,
                version=1,
                graph=DeploymentGraph("empty"),
                created_by=command.actor_id,
                created_at=self._clock(),
                metadata={
                    "bootstrap": "empty-current-graph",
                    "idempotency_key": command.idempotency_key.value,
                },
            )
            workspace = WorkspaceRecord(
                workspace_id=command.workspace_id,
                name=command.name,
                current_graph_id=current_graph.graph_id,
                metadata=command.metadata,
            )
            unit_of_work.stores.workspaces.create(workspace)
            unit_of_work.stores.graphs.save(current_graph)
            unit_of_work.commit()
            return CreateWorkspaceResult(workspace, current_graph)


def _required_text(value: object, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise WorkspaceCommandError(f"{field} must not be empty")
