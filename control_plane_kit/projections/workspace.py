"""Workspace read models for a control-plane instance."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from control_plane_kit.projections.operator_graph import OperatorGraphProjection
from control_plane_kit.stores.records import GraphVersionRecord, WorkspaceRecord


@dataclass(frozen=True)
class GraphVersionReadModel:
    """Bounded read model for one graph version."""

    graph_id: str
    workspace_id: str
    version: int
    name: str
    created_by: str
    created_at: str
    metadata: Mapping[str, str] = field(default_factory=dict)
    operator_graph: OperatorGraphProjection | None = None

    @classmethod
    def from_record(
        cls,
        record: GraphVersionRecord,
        *,
        operator_graph: OperatorGraphProjection | None = None,
    ) -> "GraphVersionReadModel":
        """Build a graph read model from graph-store truth."""

        return cls(
            graph_id=record.graph_id,
            workspace_id=record.workspace_id,
            version=record.version,
            name=str(record.graph_descriptor.get("name") or ""),
            created_by=record.created_by,
            created_at=record.created_at,
            metadata=record.metadata,
            operator_graph=operator_graph,
        )

    def descriptor(self) -> dict[str, object]:
        descriptor: dict[str, object] = {
            "graph_id": self.graph_id,
            "workspace_id": self.workspace_id,
            "version": self.version,
            "name": self.name,
            "created_by": self.created_by,
            "created_at": self.created_at,
            "metadata": dict(self.metadata),
        }
        if self.operator_graph is not None:
            descriptor["operator_graph"] = self.operator_graph.descriptor()
        return descriptor


@dataclass(frozen=True)
class WorkspaceReadModel:
    """Read-only summary of one control-plane workspace."""

    workspace_id: str
    name: str
    lifecycle: str
    metadata: Mapping[str, str] = field(default_factory=dict)
    current_graph_id: str | None = None
    desired_graph_id: str | None = None
    current_graph: GraphVersionReadModel | None = None
    desired_graph: GraphVersionReadModel | None = None

    @classmethod
    def from_record(
        cls,
        record: WorkspaceRecord,
        *,
        current_graph: GraphVersionReadModel | None = None,
        desired_graph: GraphVersionReadModel | None = None,
    ) -> "WorkspaceReadModel":
        """Build a workspace read model from workspace-store truth."""

        return cls(
            workspace_id=record.workspace_id,
            name=record.name,
            lifecycle=record.lifecycle.value,
            metadata=record.metadata,
            current_graph_id=record.current_graph_id,
            desired_graph_id=record.desired_graph_id,
            current_graph=current_graph,
            desired_graph=desired_graph,
        )

    def descriptor(self) -> dict[str, object]:
        descriptor: dict[str, object] = {
            "workspace_id": self.workspace_id,
            "name": self.name,
            "lifecycle": self.lifecycle,
            "metadata": dict(self.metadata),
            "current_graph_id": self.current_graph_id,
            "desired_graph_id": self.desired_graph_id,
        }
        if self.current_graph is not None:
            descriptor["current_graph"] = self.current_graph.descriptor()
        if self.desired_graph is not None:
            descriptor["desired_graph"] = self.desired_graph.descriptor()
        return descriptor
