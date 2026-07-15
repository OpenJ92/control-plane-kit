"""Operator-facing graph projection.

This module is intentionally pure: it transforms compiled graph truth into a
bounded read payload.  It does not read stores, call runtimes, or mutate graph
state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from control_plane_kit.graph import DeploymentGraph, Edge, Endpoint, Node, RuntimeRecord


@dataclass(frozen=True)
class OperatorEndpointProjection:
    """A safe endpoint summary for a provider socket."""

    name: str
    protocol: str
    scope: str
    address_available: bool
    url: str | None = None

    def descriptor(self) -> dict[str, object]:
        descriptor: dict[str, object] = {
            "name": self.name,
            "protocol": self.protocol,
            "scope": self.scope,
            "address_available": self.address_available,
        }
        if self.url is not None:
            descriptor["url"] = self.url
        return descriptor


@dataclass(frozen=True)
class OperatorSocketProjection:
    """Provider or requirement socket shown to an operator."""

    name: str
    direction: str
    protocol: str
    required: bool | None = None
    env_bindings: tuple[str, ...] = ()

    def descriptor(self) -> dict[str, object]:
        descriptor: dict[str, object] = {
            "name": self.name,
            "direction": self.direction,
            "protocol": self.protocol,
        }
        if self.required is not None:
            descriptor["required"] = self.required
        if self.env_bindings:
            descriptor["env_bindings"] = list(self.env_bindings)
        return descriptor


@dataclass(frozen=True)
class OperatorNodeProjection:
    """A block/node summary for graph editor and read clients."""

    node_id: str
    kind: str
    runtime_id: str
    display_name: str
    block_family: str | None = None
    providers: tuple[OperatorSocketProjection, ...] = ()
    requirements: tuple[OperatorSocketProjection, ...] = ()
    endpoints: tuple[OperatorEndpointProjection, ...] = ()
    capabilities: tuple[Mapping[str, object], ...] = ()
    metadata: Mapping[str, object] = field(default_factory=dict)

    def descriptor(self) -> dict[str, object]:
        descriptor: dict[str, object] = {
            "node_id": self.node_id,
            "kind": self.kind,
            "runtime_id": self.runtime_id,
            "display_name": self.display_name,
            "providers": [socket.descriptor() for socket in self.providers],
            "requirements": [socket.descriptor() for socket in self.requirements],
            "endpoints": [endpoint.descriptor() for endpoint in self.endpoints],
            "capabilities": [dict(capability) for capability in self.capabilities],
            "metadata": dict(self.metadata),
        }
        if self.block_family is not None:
            descriptor["block_family"] = self.block_family
        return descriptor


@dataclass(frozen=True)
class OperatorEdgeProjection:
    """A provider-to-requirement socket connection for read clients."""

    edge_id: str
    provider_node_id: str
    provider_socket: str
    consumer_node_id: str
    requirement_socket: str
    protocol: str
    env_bindings: tuple[str, ...] = ()
    env_assignments: Mapping[str, str] = field(default_factory=dict)

    def descriptor(self) -> dict[str, object]:
        descriptor: dict[str, object] = {
            "edge_id": self.edge_id,
            "provider": {"node_id": self.provider_node_id, "socket": self.provider_socket},
            "consumer": {"node_id": self.consumer_node_id, "socket": self.requirement_socket},
            "protocol": self.protocol,
            "env_bindings": list(self.env_bindings),
        }
        if self.env_assignments:
            descriptor["env_assignments"] = dict(sorted(self.env_assignments.items()))
        return descriptor


@dataclass(frozen=True)
class OperatorRuntimeProjection:
    """A runtime context summary."""

    runtime_id: str
    kind: str
    children: tuple[str, ...] = ()
    metadata: Mapping[str, str] = field(default_factory=dict)

    def descriptor(self) -> dict[str, object]:
        return {
            "runtime_id": self.runtime_id,
            "kind": self.kind,
            "children": list(self.children),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class OperatorGraphProjection:
    """A bounded read model for a deployment graph."""

    name: str
    runtimes: tuple[OperatorRuntimeProjection, ...] = ()
    nodes: tuple[OperatorNodeProjection, ...] = ()
    edges: tuple[OperatorEdgeProjection, ...] = ()

    def descriptor(self) -> dict[str, object]:
        return {
            "name": self.name,
            "runtimes": [runtime.descriptor() for runtime in self.runtimes],
            "nodes": [node.descriptor() for node in self.nodes],
            "edges": [edge.descriptor() for edge in self.edges],
        }


def project_operator_graph(
    graph: DeploymentGraph,
    *,
    include_addresses: bool = False,
) -> OperatorGraphProjection:
    """Project compiled topology into an operator-facing read model."""

    return OperatorGraphProjection(
        name=graph.name,
        runtimes=tuple(_runtime_projection(runtime) for _, runtime in sorted(graph.runtimes.items())),
        nodes=tuple(
            _node_projection(node, include_addresses=include_addresses)
            for _, node in sorted(graph.nodes.items())
        ),
        edges=tuple(
            _edge_projection(edge, graph, include_addresses=include_addresses)
            for _, edge in sorted(graph.edges.items())
        ),
    )


def _runtime_projection(runtime: RuntimeRecord) -> OperatorRuntimeProjection:
    return OperatorRuntimeProjection(
        runtime_id=runtime.runtime_id,
        kind=runtime.kind.value,
        children=tuple(runtime.children),
        metadata=runtime.metadata,
    )


def _node_projection(
    node: Node,
    *,
    include_addresses: bool,
) -> OperatorNodeProjection:
    safe_metadata = {
        key: value
        for key, value in node.metadata.items()
        if key not in {"capabilities", "block_family", "display_name"}
    }
    return OperatorNodeProjection(
        node_id=node.node_id,
        kind=node.kind,
        runtime_id=node.runtime_id,
        display_name=str(node.metadata.get("display_name") or node.node_id),
        block_family=_optional_string(node.metadata.get("block_family")),
        providers=tuple(
            OperatorSocketProjection(
                name=socket.name,
                direction="provider",
                protocol=socket.protocol.value,
            )
            for socket in node.sockets.providers
        ),
        requirements=tuple(
            OperatorSocketProjection(
                name=socket.name,
                direction="requirement",
                protocol=socket.protocol.value,
                required=socket.required,
                env_bindings=tuple(socket.env_bindings),
            )
            for socket in node.sockets.requirements
        ),
        endpoints=tuple(
            _endpoint_projection(name, endpoint, include_addresses=include_addresses)
            for name, endpoint in sorted(node.endpoints.items())
        ),
        capabilities=tuple(_mapping_tuple(node.metadata.get("capabilities"))),
        metadata=safe_metadata,
    )


def _edge_projection(
    edge: Edge,
    graph: DeploymentGraph,
    *,
    include_addresses: bool,
) -> OperatorEdgeProjection:
    consumer = graph.node(edge.consumer_role)
    requirement = consumer.requirement_socket(edge.requirement_socket)
    return OperatorEdgeProjection(
        edge_id=edge.edge_id,
        provider_node_id=edge.provider_role,
        provider_socket=edge.provider_socket,
        consumer_node_id=edge.consumer_role,
        requirement_socket=edge.requirement_socket,
        protocol=edge.protocol.value,
        env_bindings=tuple(requirement.env_bindings),
        env_assignments=edge.env_assignments if include_addresses else {},
    )


def _endpoint_projection(
    name: str,
    endpoint: Endpoint,
    *,
    include_addresses: bool,
) -> OperatorEndpointProjection:
    return OperatorEndpointProjection(
        name=name,
        protocol=endpoint.protocol.value,
        scope=endpoint.scope.value,
        address_available=bool(endpoint.url),
        url=endpoint.url if include_addresses else None,
    )


def _mapping_tuple(value: object) -> tuple[Mapping[str, object], ...]:
    if not isinstance(value, tuple | list):
        return ()
    return tuple(item for item in value if isinstance(item, Mapping))


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) else None
