"""Compiled graph values."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Mapping

from control_plane_kit.algebra import BlockSockets
from control_plane_kit.types import EndpointScope, Protocol, RuntimeKind


@dataclass(frozen=True)
class Endpoint:
    """Concrete address provided by a compiled node output socket."""

    url: str
    protocol: Protocol
    scope: EndpointScope = EndpointScope.PRIVATE

    def descriptor(self) -> dict[str, str]:
        return {"url": self.url, "protocol": self.protocol.value, "scope": self.scope.value}


@dataclass(frozen=True)
class Node:
    """Compiled deployable node."""

    node_id: str
    kind: str
    runtime_id: str
    sockets: BlockSockets
    endpoints: Mapping[str, Endpoint] = field(default_factory=dict)
    environment: Mapping[str, str] = field(default_factory=dict)
    metadata: Mapping[str, object] = field(default_factory=dict)

    def input_socket(self, name: str):
        return self.sockets.requirement(name)

    def output_socket(self, name: str):
        return self.sockets.provider(name)

    def endpoint(self, name: str) -> Endpoint:
        try:
            return self.endpoints[name]
        except KeyError as exc:
            available = ", ".join(sorted(self.endpoints)) or "<none>"
            raise KeyError(f"node {self.node_id!r} has no endpoint {name!r}; available: {available}") from exc

    def with_environment(self, values: Mapping[str, str]) -> Node:
        return replace(self, environment={**self.environment, **values})

    def descriptor(self) -> dict[str, object]:
        return {
            "node_id": self.node_id,
            "kind": self.kind,
            "runtime_id": self.runtime_id,
            "endpoints": {key: value.descriptor() for key, value in sorted(self.endpoints.items())},
            "environment": dict(sorted(self.environment.items())),
            "inputs": {
                socket.name: {
                    "protocol": socket.protocol.value,
                    "env_bindings": list(socket.env_bindings),
                    "required": socket.required,
                }
                for socket in self.sockets.requirements
            },
            "outputs": {
                socket.name: {"protocol": socket.protocol.value}
                for socket in self.sockets.providers
            },
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class Edge:
    """Compiled provider-output to consumer-input connection."""

    edge_id: str
    provider_role: str
    output_socket: str
    consumer_role: str
    input_socket: str
    protocol: Protocol
    env_assignments: Mapping[str, str] = field(default_factory=dict)

    def descriptor(self) -> dict[str, object]:
        return {
            "edge_id": self.edge_id,
            "provider": {"role": self.provider_role, "socket": self.output_socket},
            "consumer": {"role": self.consumer_role, "socket": self.input_socket},
            "protocol": self.protocol.value,
            "env_assignments": dict(sorted(self.env_assignments.items())),
        }


@dataclass(frozen=True)
class RuntimeRecord:
    """Compiled runtime context."""

    runtime_id: str
    kind: RuntimeKind
    children: tuple[str, ...] = ()
    metadata: Mapping[str, str] = field(default_factory=dict)

    def descriptor(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "children": list(self.children),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class DeploymentGraph:
    """Pure compiled topology."""

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
        return replace(self, nodes={**self.nodes, node.node_id: node})

    def add_edge(self, edge: Edge) -> DeploymentGraph:
        return replace(self, edges={**self.edges, edge.edge_id: edge})

    def add_runtime(self, runtime: RuntimeRecord) -> DeploymentGraph:
        return replace(self, runtimes={**self.runtimes, runtime.runtime_id: runtime})

    def update_node(self, node: Node) -> DeploymentGraph:
        if node.node_id not in self.nodes:
            raise KeyError(f"cannot update missing node {node.node_id!r}")
        return replace(self, nodes={**self.nodes, node.node_id: node})

    def descriptor(self) -> dict[str, object]:
        return {
            "name": self.name,
            "runtimes": {key: value.descriptor() for key, value in sorted(self.runtimes.items())},
            "nodes": {key: value.descriptor() for key, value in sorted(self.nodes.items())},
            "edges": {key: value.descriptor() for key, value in sorted(self.edges.items())},
        }
