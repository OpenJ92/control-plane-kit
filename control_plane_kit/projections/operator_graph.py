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

    return project_operator_graph_descriptor(
        graph.descriptor(),
        include_addresses=include_addresses,
    )


def project_operator_graph_descriptor(
    graph_descriptor: Mapping[str, object],
    *,
    include_addresses: bool = False,
) -> OperatorGraphProjection:
    """Project a stored graph descriptor into an operator-facing read model."""

    runtimes = _mapping(graph_descriptor.get("runtimes"))
    nodes = _mapping(graph_descriptor.get("nodes"))
    edges = _mapping(graph_descriptor.get("edges"))
    return OperatorGraphProjection(
        name=str(graph_descriptor.get("name") or ""),
        runtimes=tuple(
            _runtime_descriptor_projection(runtime_id, _mapping(runtime))
            for runtime_id, runtime in sorted(runtimes.items())
        ),
        nodes=tuple(
            _node_descriptor_projection(node_id, _mapping(node), include_addresses=include_addresses)
            for node_id, node in sorted(nodes.items())
        ),
        edges=tuple(
            _edge_descriptor_projection(edge_id, _mapping(edge), nodes, include_addresses=include_addresses)
            for edge_id, edge in sorted(edges.items())
        ),
    )


def _runtime_projection(runtime: RuntimeRecord) -> OperatorRuntimeProjection:
    return OperatorRuntimeProjection(
        runtime_id=runtime.runtime_id,
        kind=runtime.kind.value,
        children=tuple(runtime.children),
        metadata=runtime.metadata,
    )


def _runtime_descriptor_projection(
    runtime_id: str,
    runtime: Mapping[str, object],
) -> OperatorRuntimeProjection:
    return OperatorRuntimeProjection(
        runtime_id=runtime_id,
        kind=str(runtime.get("kind") or ""),
        children=_string_tuple(runtime.get("children")),
        metadata=_string_mapping(runtime.get("metadata")),
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


def _node_descriptor_projection(
    node_id: str,
    node: Mapping[str, object],
    *,
    include_addresses: bool,
) -> OperatorNodeProjection:
    metadata = _mapping(node.get("metadata"))
    safe_metadata = {
        key: value
        for key, value in metadata.items()
        if key not in {"capabilities", "block_family", "display_name"}
    }
    return OperatorNodeProjection(
        node_id=node_id,
        kind=str(node.get("kind") or ""),
        runtime_id=str(node.get("runtime_id") or ""),
        display_name=str(metadata.get("display_name") or node_id),
        block_family=_optional_string(metadata.get("block_family")),
        providers=tuple(
            OperatorSocketProjection(
                name=socket_id,
                direction="provider",
                protocol=str(_mapping(socket).get("protocol") or ""),
            )
            for socket_id, socket in sorted(_mapping(node.get("providers")).items())
        ),
        requirements=tuple(
            _requirement_descriptor_projection(socket_id, _mapping(socket))
            for socket_id, socket in sorted(_mapping(node.get("requirements")).items())
        ),
        endpoints=tuple(
            _endpoint_descriptor_projection(endpoint_id, _mapping(endpoint), include_addresses=include_addresses)
            for endpoint_id, endpoint in sorted(_mapping(node.get("endpoints")).items())
        ),
        capabilities=tuple(_mapping_tuple(metadata.get("capabilities"))),
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


def _edge_descriptor_projection(
    edge_id: str,
    edge: Mapping[str, object],
    nodes: Mapping[str, object],
    *,
    include_addresses: bool,
) -> OperatorEdgeProjection:
    provider = _mapping(edge.get("provider"))
    consumer = _mapping(edge.get("consumer"))
    consumer_node_id = str(consumer.get("role") or consumer.get("node_id") or "")
    requirement_socket = str(consumer.get("requirement") or consumer.get("socket") or "")
    return OperatorEdgeProjection(
        edge_id=edge_id,
        provider_node_id=str(provider.get("role") or provider.get("node_id") or ""),
        provider_socket=str(provider.get("socket") or ""),
        consumer_node_id=consumer_node_id,
        requirement_socket=requirement_socket,
        protocol=str(edge.get("protocol") or ""),
        env_bindings=_descriptor_env_bindings(nodes, consumer_node_id, requirement_socket),
        env_assignments=_string_mapping(edge.get("env_assignments")) if include_addresses else {},
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


def _endpoint_descriptor_projection(
    name: str,
    endpoint: Mapping[str, object],
    *,
    include_addresses: bool,
) -> OperatorEndpointProjection:
    url = _optional_string(endpoint.get("url"))
    return OperatorEndpointProjection(
        name=name,
        protocol=str(endpoint.get("protocol") or ""),
        scope=str(endpoint.get("scope") or ""),
        address_available=bool(url),
        url=url if include_addresses else None,
    )


def _requirement_descriptor_projection(
    name: str,
    socket: Mapping[str, object],
) -> OperatorSocketProjection:
    return OperatorSocketProjection(
        name=name,
        direction="requirement",
        protocol=str(socket.get("protocol") or ""),
        required=bool(socket.get("required", True)),
        env_bindings=_string_tuple(socket.get("env_bindings")),
    )


def _descriptor_env_bindings(
    nodes: Mapping[str, object],
    consumer_node_id: str,
    requirement_socket: str,
) -> tuple[str, ...]:
    consumer = _mapping(nodes.get(consumer_node_id))
    requirement = _mapping(_mapping(consumer.get("requirements")).get(requirement_socket))
    return _string_tuple(requirement.get("env_bindings"))


def _mapping_tuple(value: object) -> tuple[Mapping[str, object], ...]:
    if not isinstance(value, tuple | list):
        return ()
    return tuple(item for item in value if isinstance(item, Mapping))


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _string_mapping(value: object) -> Mapping[str, str]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): str(item) for key, item in value.items()}


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, tuple | list):
        return ()
    return tuple(str(item) for item in value)
