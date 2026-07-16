"""Typed data language for desired-topology replacement commands."""

from __future__ import annotations

from dataclasses import dataclass

from control_plane_kit.graph import DeploymentGraph
from control_plane_kit.stores import (
    GraphVersionRecord,
    OperationActionKind,
    OperationActionRecord,
)
from control_plane_kit.workflows.commands import (
    IdempotencyKey,
    InvalidOperationCommand,
)


@dataclass(frozen=True)
class SetDesiredGraph:
    """Replace one workspace's desired topology from an expected pointer."""

    session_id: str
    workspace_id: str
    actor_id: str
    graph: DeploymentGraph
    expected_desired_graph_id: str | None
    idempotency_key: IdempotencyKey

    def __post_init__(self) -> None:
        _required("session_id", self.session_id)
        _required("workspace_id", self.workspace_id)
        _required("actor_id", self.actor_id)
        if not isinstance(self.graph, DeploymentGraph):
            raise InvalidOperationCommand("graph must be a DeploymentGraph")
        _required("graph.name", self.graph.name)
        if self.expected_desired_graph_id is not None:
            _required("expected_desired_graph_id", self.expected_desired_graph_id)

    def descriptor(self) -> dict[str, object]:
        """Describe intent safely; the shared codec owns durable graph encoding."""

        return {
            "command": "set_desired_graph",
            "session_id": self.session_id,
            "workspace_id": self.workspace_id,
            "actor_id": self.actor_id,
            "expected_desired_graph_id": self.expected_desired_graph_id,
            "idempotency_key": self.idempotency_key.value,
            "graph": {
                "name": self.graph.name,
                "runtime_ids": sorted(self.graph.runtimes),
                "node_ids": sorted(self.graph.nodes),
                "edge_ids": sorted(self.graph.edges),
            },
        }


@dataclass(frozen=True)
class DesiredGraphEditResult:
    """Durable evidence returned after one desired-graph command."""

    workspace_id: str
    previous_desired_graph_id: str | None
    graph_version: GraphVersionRecord
    action: OperationActionRecord
    replayed: bool = False

    def __post_init__(self) -> None:
        _required("workspace_id", self.workspace_id)
        if self.graph_version.workspace_id != self.workspace_id:
            raise InvalidOperationCommand(
                "graph version workspace must match result workspace"
            )
        if self.action.action_type is not OperationActionKind.SET_DESIRED_GRAPH:
            raise InvalidOperationCommand(
                "desired graph result requires SET_DESIRED_GRAPH action evidence"
            )

    def descriptor(self) -> dict[str, object]:
        return {
            "workspace_id": self.workspace_id,
            "previous_desired_graph_id": self.previous_desired_graph_id,
            "desired_graph_id": self.graph_version.graph_id,
            "desired_graph_version": self.graph_version.version,
            "action_id": self.action.action_id,
            "action_ordinal": self.action.ordinal,
            "replayed": self.replayed,
        }


DesiredGraphEdit = SetDesiredGraph


def _required(name: str, value: object) -> None:
    if not isinstance(value, str) or not value.strip():
        raise InvalidOperationCommand(f"{name} must not be empty")
