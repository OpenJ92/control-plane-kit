"""Compiled graph values."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Mapping
from urllib.parse import urlsplit

from control_plane_kit.algebra import BlockSockets, BlockSpec
from control_plane_kit.lifecycle import OWNED_EPHEMERAL, ResourceLifecycle
from control_plane_kit.types import BlockFamily, EndpointScope, Protocol, RuntimeKind


@dataclass(frozen=True)
class LiteralAddress:
    """Non-secret provider address safe to retain in durable topology."""

    value: str

    def __post_init__(self) -> None:
        if not self.value.strip():
            raise ValueError("literal address must not be empty")
        parsed = urlsplit(self.value)
        if parsed.password is not None:
            raise ValueError("literal address must not contain credentials; use a secret reference")

    def descriptor(self) -> dict[str, str]:
        return {"kind": "literal", "value": self.value}


@dataclass(frozen=True)
class SecretReferenceAddress:
    """Stable reference to an address resolved only by a runtime interpreter."""

    secret_ref: str

    def __post_init__(self) -> None:
        if not self.secret_ref.strip():
            raise ValueError("secret reference must not be empty")

    def descriptor(self) -> dict[str, str]:
        return {"kind": "secret-reference", "secret_ref": self.secret_ref}


EndpointAddress = LiteralAddress | SecretReferenceAddress


@dataclass(frozen=True)
class Endpoint:
    """Typed address provided by a compiled provider socket."""

    address: EndpointAddress
    protocol: Protocol
    scope: EndpointScope = EndpointScope.PRIVATE

    @property
    def url(self) -> str:
        """Return a literal address or unresolved secret-reference token."""

        match self.address:
            case LiteralAddress(value=value):
                return value
            case SecretReferenceAddress(secret_ref=secret_ref):
                return secret_ref

    def descriptor(self) -> dict[str, object]:
        return {
            "address": self.address.descriptor(),
            "protocol": self.protocol.value,
            "scope": self.scope.value,
        }


@dataclass(frozen=True)
class Node:
    """Compiled deployable node."""

    node_id: str
    block_family: BlockFamily
    block_spec: BlockSpec
    kind: str
    runtime_id: str
    sockets: BlockSockets
    endpoints: Mapping[str, Endpoint] = field(default_factory=dict)
    environment: Mapping[str, str] = field(default_factory=dict)
    metadata: Mapping[str, object] = field(default_factory=dict)
    lifecycle: ResourceLifecycle = OWNED_EPHEMERAL

    def requirement_socket(self, name: str):
        return self.sockets.requirement(name)

    def provider_socket(self, name: str):
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
            "block_family": self.block_family.value,
            "block_spec": {
                "variant": "block",
                "role_id": self.block_spec.role_id,
                "display_name": self.block_spec.display_name,
                "health_path": self.block_spec.health_path,
                "capabilities": [value.value for value in self.block_spec.capabilities],
                "metadata": dict(sorted(self.block_spec.metadata.items())),
            },
            "kind": self.kind,
            "runtime_id": self.runtime_id,
            "endpoints": {key: value.descriptor() for key, value in sorted(self.endpoints.items())},
            "environment": dict(sorted(self.environment.items())),
            "requirements": {
                socket.name: {
                    "protocol": socket.protocol.value,
                    "binding": socket.binding.value,
                    "env_bindings": list(socket.env_bindings),
                    "required": socket.required,
                }
                for socket in self.sockets.requirements
            },
            "providers": {
                socket.name: {"protocol": socket.protocol.value}
                for socket in self.sockets.providers
            },
            "metadata": dict(self.metadata),
            "lifecycle": self.lifecycle.descriptor(),
        }


@dataclass(frozen=True)
class Edge:
    """Compiled provider-to-requirement socket connection."""

    edge_id: str
    provider_role: str
    provider_socket: str
    consumer_role: str
    requirement_socket: str
    protocol: Protocol
    env_assignments: Mapping[str, str] = field(default_factory=dict)

    def descriptor(self) -> dict[str, object]:
        return {
            "edge_id": self.edge_id,
            "provider": {"role": self.provider_role, "socket": self.provider_socket},
            "consumer": {"role": self.consumer_role, "requirement": self.requirement_socket},
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
    lifecycle: ResourceLifecycle = OWNED_EPHEMERAL

    def descriptor(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "children": list(self.children),
            "metadata": dict(self.metadata),
            "lifecycle": self.lifecycle.descriptor(),
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
        from control_plane_kit.topology.codec import DEFAULT_GRAPH_CODEC

        return DEFAULT_GRAPH_CODEC.encode(self)
