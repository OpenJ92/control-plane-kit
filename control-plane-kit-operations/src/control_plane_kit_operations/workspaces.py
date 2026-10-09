"""Workspace command service for operations-owned graph truth."""

from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any, Callable, Mapping

import rfc8785

from control_plane_kit_core.topology import DeploymentGraph
from control_plane_kit_operations.records import GraphVersionRecord, WorkspaceRecord
from control_plane_kit_operations.workflows import IdempotencyKey


class WorkspaceCommandError(RuntimeError):
    """Raised when workspace command data or state is invalid."""


@dataclass(frozen=True, repr=False)
class _WorkspaceInitialization:
    workspace_id: str
    profile: str
    initial_graph_id: str
    initial_projection_id: str
    graph_descriptor_sha256: str
    projection_digest: str
    configuration_slot_count: int
    created_by: str
    creation_idempotency_key: str


@dataclass(frozen=True, repr=False)
class _PreparedWorkspaceCreation:
    stores: object
    lifecycle_guard: object
    command: object
    graph: GraphVersionRecord

    def require(self, connection, workspace_id):
        if (self.stores.connection is not connection
                or type(self.command) is not CreateWorkspace
                or type(self.graph) is not GraphVersionRecord
                or self.command.workspace_id != workspace_id
                or self.graph.workspace_id != workspace_id):
            raise WorkspaceCommandError("workspace initialization requires original owner")
        self.stores.graphs._require_receiver_lifecycle(self.lifecycle_guard, workspace_id)
        expected = GraphVersionRecord.from_graph(graph_id=self.graph.graph_id,
            workspace_id=workspace_id, version=1, graph=DeploymentGraph("empty"),
            created_by=self.command.actor_id, created_at=self.graph.created_at,
            metadata={"bootstrap": "empty-current-graph",
                "idempotency_key": self.command.idempotency_key.value})
        if self.graph != expected:
            raise WorkspaceCommandError("workspace initialization graph is invalid")

    def receipt(self, projection):
        return _WorkspaceInitialization(self.command.workspace_id,
            "workspace-initialization.v1", self.graph.graph_id, projection.projection_id,
            sha256(rfc8785.dumps(self.graph.graph_descriptor)).hexdigest(),
            projection.projection_digest, 0, self.command.actor_id,
            self.command.idempotency_key.value)


def _require_prepared_creation(prepared, connection, workspace_id):
    if type(prepared) is not _PreparedWorkspaceCreation:
        raise WorkspaceCommandError("workspace initialization requires original owner")
    prepared.require(connection, workspace_id)


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
                "current_realized_projection_id": (
                    self.workspace.current_realized_projection_id
                ),
                "desired_realized_projection_id": (
                    self.workspace.desired_realized_projection_id
                ),
                "desired_graph_revision": self.workspace.desired_graph_revision,
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
            with unit_of_work.stores.workspaces._initialization_evidence(command.workspace_id):
                return self._create(command, unit_of_work)

    def _create(self, command: CreateWorkspace, unit_of_work: Any) -> CreateWorkspaceResult:
        try:
            existing = unit_of_work.stores.workspaces.get(command.workspace_id)
        except KeyError:
            existing = None
        guard = None
        if existing is None:
            guard = unit_of_work.stores.graphs.lock_receiver_lifecycle(command.workspace_id)
            # A concurrent creator may have committed while L was held.
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
            unit_of_work.stores.workspaces._require_workspace_initialization(command.workspace_id)
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
            metadata=command.metadata,
        )
        prepared = _PreparedWorkspaceCreation(unit_of_work.stores, guard, command, current_graph)
        unit_of_work.stores.workspaces._create_for_initialization(workspace, prepared)
        unit_of_work.stores.graphs._save_workspace_initialization(current_graph, prepared)
        workspace = unit_of_work.stores.workspaces._set_initial_current_graph(prepared)
        projection = unit_of_work.stores.realized_graphs.get(workspace.current_realized_projection_id)
        unit_of_work.stores.workspaces._insert_workspace_initialization(
            prepared.receipt(projection), prepared)
        unit_of_work.commit()
        return CreateWorkspaceResult(workspace, current_graph)


def _required_text(value: object, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise WorkspaceCommandError(f"{field} must not be empty")
