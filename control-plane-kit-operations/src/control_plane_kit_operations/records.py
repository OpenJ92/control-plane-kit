"""Durable operations record shapes."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC, DeploymentGraph
from control_plane_kit_core.types import WorkspaceLifecycle


class OperationsRecordError(ValueError):
    """Raised when a durable operations record is malformed."""


@dataclass(frozen=True)
class WorkspaceRecord:
    """Workspace truth and graph pointers owned by operations."""

    workspace_id: str
    name: str
    lifecycle: WorkspaceLifecycle = WorkspaceLifecycle.CREATED
    current_graph_id: str | None = None
    desired_graph_id: str | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_text(self.workspace_id, "workspace_id")
        _validate_text(self.name, "name")
        if not isinstance(self.lifecycle, WorkspaceLifecycle):
            raise OperationsRecordError("workspace lifecycle must be WorkspaceLifecycle")
        _validate_optional_text(self.current_graph_id, "current_graph_id")
        _validate_optional_text(self.desired_graph_id, "desired_graph_id")
        if not isinstance(self.metadata, Mapping):
            raise OperationsRecordError("workspace metadata must be mapping")


@dataclass(frozen=True)
class GraphVersionRecord:
    """One immutable graph descriptor version owned by a workspace."""

    graph_id: str
    workspace_id: str
    version: int
    graph_descriptor: Mapping[str, object]
    created_by: str
    created_at: str
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_text(self.graph_id, "graph_id")
        _validate_text(self.workspace_id, "workspace_id")
        if type(self.version) is not int or self.version < 1:
            raise OperationsRecordError("graph version must be a positive integer")
        if not isinstance(self.graph_descriptor, Mapping):
            raise OperationsRecordError("graph_descriptor must be mapping")
        _validate_text(self.created_by, "created_by")
        _validate_text(self.created_at, "created_at")
        if not isinstance(self.metadata, Mapping):
            raise OperationsRecordError("graph metadata must be mapping")

    @classmethod
    def from_graph(
        cls,
        *,
        graph_id: str,
        workspace_id: str,
        version: int,
        graph: DeploymentGraph,
        created_by: str,
        created_at: str,
        metadata: Mapping[str, object] | None = None,
    ) -> "GraphVersionRecord":
        if not isinstance(graph, DeploymentGraph):
            raise OperationsRecordError("graph version requires DeploymentGraph")
        return cls(
            graph_id=graph_id,
            workspace_id=workspace_id,
            version=version,
            graph_descriptor=DEFAULT_GRAPH_CODEC.encode(graph),
            created_by=created_by,
            created_at=created_at,
            metadata={} if metadata is None else metadata,
        )


def _validate_text(value: str, field: str) -> None:
    if not isinstance(value, str) or not value or len(value) > 512:
        raise OperationsRecordError(f"{field} must be nonempty bounded text")
    if any(ord(character) < 32 for character in value):
        raise OperationsRecordError(f"{field} must not contain control characters")


def _validate_optional_text(value: str | None, field: str) -> None:
    if value is None:
        return
    _validate_text(value, field)
