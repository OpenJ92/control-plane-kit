"""Capability, contract, and control-route read models."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from control_plane_kit.control_routes import route_set_named


@dataclass(frozen=True)
class ControlRouteReadModel:
    """One route in a block control protocol surface."""

    name: str
    method: str
    path: str
    scope: str
    description: str

    @classmethod
    def from_descriptor(cls, descriptor: Mapping[str, object]) -> "ControlRouteReadModel":
        return cls(
            name=str(descriptor.get("name") or ""),
            method=str(descriptor.get("method") or ""),
            path=str(descriptor.get("path") or ""),
            scope=str(descriptor.get("scope") or ""),
            description=str(descriptor.get("description") or ""),
        )

    def descriptor(self) -> dict[str, object]:
        return {
            "name": self.name,
            "method": self.method,
            "path": self.path,
            "scope": self.scope,
            "description": self.description,
        }


@dataclass(frozen=True)
class ControlRouteSetReadModel:
    """Routes implied by a capability route-set declaration."""

    name: str
    routes: tuple[ControlRouteReadModel, ...] = ()

    @classmethod
    def from_route_set_name(cls, route_set_name: str) -> "ControlRouteSetReadModel":
        route_set = route_set_named(route_set_name)
        descriptor = route_set.as_descriptor()
        return cls(
            name=str(descriptor["name"]),
            routes=tuple(
                ControlRouteReadModel.from_descriptor(route)
                for route in _mapping_tuple(descriptor.get("routes"))
            ),
        )

    def descriptor(self) -> dict[str, object]:
        return {
            "name": self.name,
            "routes": [route.descriptor() for route in self.routes],
        }


@dataclass(frozen=True)
class CapabilityReadModel:
    """Operator-facing capability with its declared route surface."""

    name: str
    label: str
    description: str
    route_set: str | None = None
    route_path: str | None = None
    control_routes: ControlRouteSetReadModel | None = None

    @classmethod
    def from_descriptor(cls, descriptor: Mapping[str, object]) -> "CapabilityReadModel":
        route_set = _optional_string(descriptor.get("route_set"))
        return cls(
            name=str(descriptor.get("name") or ""),
            label=str(descriptor.get("label") or ""),
            description=str(descriptor.get("description") or ""),
            route_set=route_set,
            route_path=_optional_string(descriptor.get("route_path")),
            control_routes=ControlRouteSetReadModel.from_route_set_name(route_set) if route_set else None,
        )

    def descriptor(self) -> dict[str, object]:
        descriptor: dict[str, object] = {
            "name": self.name,
            "label": self.label,
            "description": self.description,
        }
        if self.route_set is not None:
            descriptor["route_set"] = self.route_set
        if self.route_path is not None:
            descriptor["route_path"] = self.route_path
        if self.control_routes is not None:
            descriptor["control_routes"] = self.control_routes.descriptor()
        return descriptor


@dataclass(frozen=True)
class ProviderContractReadModel:
    """A provider socket exposed by a node."""

    name: str
    protocol: str
    endpoint_available: bool

    def descriptor(self) -> dict[str, object]:
        return {
            "name": self.name,
            "protocol": self.protocol,
            "endpoint_available": self.endpoint_available,
        }


@dataclass(frozen=True)
class RequirementContractReadModel:
    """An env-backed requirement socket on a consumer node."""

    name: str
    protocol: str
    required: bool
    env_bindings: tuple[str, ...]
    fulfilled: bool
    provider_node_id: str | None = None
    provider_socket: str | None = None

    def descriptor(self) -> dict[str, object]:
        descriptor: dict[str, object] = {
            "name": self.name,
            "protocol": self.protocol,
            "required": self.required,
            "env_bindings": list(self.env_bindings),
            "fulfilled": self.fulfilled,
        }
        if self.provider_node_id is not None and self.provider_socket is not None:
            descriptor["provider"] = {
                "node_id": self.provider_node_id,
                "socket": self.provider_socket,
            }
        return descriptor


@dataclass(frozen=True)
class NodeControlSurfaceReadModel:
    """Declared control and connection surface for one graph node."""

    node_id: str
    display_name: str
    block_family: str | None = None
    capabilities: tuple[CapabilityReadModel, ...] = ()
    providers: tuple[ProviderContractReadModel, ...] = ()
    requirements: tuple[RequirementContractReadModel, ...] = ()

    def descriptor(self) -> dict[str, object]:
        descriptor: dict[str, object] = {
            "node_id": self.node_id,
            "display_name": self.display_name,
            "capabilities": [capability.descriptor() for capability in self.capabilities],
            "providers": [provider.descriptor() for provider in self.providers],
            "requirements": [requirement.descriptor() for requirement in self.requirements],
        }
        if self.block_family is not None:
            descriptor["block_family"] = self.block_family
        return descriptor


@dataclass(frozen=True)
class ControlSurfaceReadModel:
    """Declared control surface for a graph version."""

    workspace_id: str
    graph_id: str
    graph_name: str
    nodes: tuple[NodeControlSurfaceReadModel, ...] = ()

    def descriptor(self) -> dict[str, object]:
        return {
            "workspace_id": self.workspace_id,
            "graph_id": self.graph_id,
            "graph_name": self.graph_name,
            "nodes": [node.descriptor() for node in self.nodes],
        }


def project_control_surface_descriptor(
    *,
    workspace_id: str,
    graph_id: str,
    graph_descriptor: Mapping[str, object],
) -> ControlSurfaceReadModel:
    """Project graph declarations into an operator control-surface read model."""

    nodes = _mapping(graph_descriptor.get("nodes"))
    edges = _mapping(graph_descriptor.get("edges"))
    connections = _requirement_connections(edges)
    return ControlSurfaceReadModel(
        workspace_id=workspace_id,
        graph_id=graph_id,
        graph_name=str(graph_descriptor.get("name") or ""),
        nodes=tuple(
            _node_control_surface(node_id, _mapping(node), connections)
            for node_id, node in sorted(nodes.items())
        ),
    )


def _node_control_surface(
    node_id: str,
    node: Mapping[str, object],
    connections: Mapping[tuple[str, str], Mapping[str, str]],
) -> NodeControlSurfaceReadModel:
    metadata = _mapping(node.get("metadata"))
    return NodeControlSurfaceReadModel(
        node_id=node_id,
        display_name=str(metadata.get("display_name") or node_id),
        block_family=_optional_string(metadata.get("block_family")),
        capabilities=tuple(
            CapabilityReadModel.from_descriptor(capability)
            for capability in _mapping_tuple(metadata.get("capabilities"))
        ),
        providers=tuple(
            _provider_contract(socket_id, _mapping(socket), _mapping(node.get("endpoints")))
            for socket_id, socket in sorted(_mapping(node.get("providers")).items())
        ),
        requirements=tuple(
            _requirement_contract(node_id, socket_id, _mapping(socket), connections)
            for socket_id, socket in sorted(_mapping(node.get("requirements")).items())
        ),
    )


def _provider_contract(
    socket_id: str,
    socket: Mapping[str, object],
    endpoints: Mapping[str, object],
) -> ProviderContractReadModel:
    return ProviderContractReadModel(
        name=socket_id,
        protocol=str(socket.get("protocol") or ""),
        endpoint_available=socket_id in endpoints,
    )


def _requirement_contract(
    node_id: str,
    socket_id: str,
    socket: Mapping[str, object],
    connections: Mapping[tuple[str, str], Mapping[str, str]],
) -> RequirementContractReadModel:
    connection = connections.get((node_id, socket_id))
    return RequirementContractReadModel(
        name=socket_id,
        protocol=str(socket.get("protocol") or ""),
        required=bool(socket.get("required")),
        env_bindings=_string_tuple(socket.get("env_bindings")),
        fulfilled=connection is not None,
        provider_node_id=connection.get("node_id") if connection is not None else None,
        provider_socket=connection.get("socket") if connection is not None else None,
    )


def _requirement_connections(edges: Mapping[str, object]) -> dict[tuple[str, str], Mapping[str, str]]:
    connections: dict[tuple[str, str], Mapping[str, str]] = {}
    for edge in edges.values():
        edge_mapping = _mapping(edge)
        provider = _mapping(edge_mapping.get("provider"))
        consumer = _mapping(edge_mapping.get("consumer"))
        consumer_id = str(consumer.get("role") or consumer.get("node_id") or "")
        requirement_socket = str(consumer.get("requirement") or consumer.get("socket") or "")
        if not consumer_id or not requirement_socket:
            continue
        connections[(consumer_id, requirement_socket)] = {
            "node_id": str(provider.get("role") or provider.get("node_id") or ""),
            "socket": str(provider.get("socket") or ""),
        }
    return connections


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _mapping_tuple(value: object) -> tuple[Mapping[str, object], ...]:
    if not isinstance(value, list | tuple):
        return ()
    return tuple(item for item in value if isinstance(item, Mapping))


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list | tuple):
        return ()
    return tuple(str(item) for item in value)


def _optional_string(value: object) -> str | None:
    return str(value) if isinstance(value, str) and value else None
