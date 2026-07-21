from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Mapping


@dataclass(frozen=True)
class Node:
    node_id: str
    kind: str
    metadata: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Edge:
    edge_id: str
    provider_role: str
    provider_socket: str
    consumer_role: str
    requirement_socket: str


@dataclass(frozen=True)
class RuntimeRecord:
    runtime_id: str
    kind: str
    children: tuple[str, ...] = ()
    metadata: Mapping[str, str] = field(default_factory=dict)


class GraphConstructionCode(StrEnum):
    DUPLICATE_IDENTITY = "duplicate-identity"


class GraphIdentityKind(StrEnum):
    NODE = "node"
    EDGE = "edge"
    RUNTIME = "runtime"


class GraphConstructionError(ValueError):
    """Closed failure emitted when pure graph construction breaks identity laws."""

    def __init__(
        self,
        code: GraphConstructionCode,
        identity_kind: GraphIdentityKind,
        identity: str,
    ) -> None:
        self.code = code
        self.identity_kind = identity_kind
        self.identity = identity
        super().__init__(
            f"cannot add duplicate {identity_kind.value} identity {identity!r}"
        )


@dataclass(frozen=True)
class DeploymentGraph:
    """Pure deployment topology."""

    name: str
    nodes: Mapping[str, Node] = field(default_factory=dict)
    edges: Mapping[str, Edge] = field(default_factory=dict)
    runtimes: Mapping[str, RuntimeRecord] = field(default_factory=dict)

    def node(self, node_id: str) -> Node:
        try:
            return self.nodes[node_id]
        except KeyError as exc:
            available = ", ".join(sorted(self.nodes)) or "<none>"
            raise KeyError(f"missing node {node_id!r}; available: {available}") from exc

    def add_node(self, node: Node) -> DeploymentGraph:
        if node.node_id in self.nodes:
            raise GraphConstructionError(
                GraphConstructionCode.DUPLICATE_IDENTITY,
                GraphIdentityKind.NODE,
                node.node_id,
            )
        return replace(self, nodes={**self.nodes, node.node_id: node})

    def add_edge(self, edge: Edge) -> DeploymentGraph:
        if edge.edge_id in self.edges:
            raise GraphConstructionError(
                GraphConstructionCode.DUPLICATE_IDENTITY,
                GraphIdentityKind.EDGE,
                edge.edge_id,
            )
        return replace(self, edges={**self.edges, edge.edge_id: edge})

    def add_runtime(self, runtime: RuntimeRecord) -> DeploymentGraph:
        if runtime.runtime_id in self.runtimes:
            raise GraphConstructionError(
                GraphConstructionCode.DUPLICATE_IDENTITY,
                GraphIdentityKind.RUNTIME,
                runtime.runtime_id,
            )
        return replace(self, runtimes={**self.runtimes, runtime.runtime_id: runtime})

