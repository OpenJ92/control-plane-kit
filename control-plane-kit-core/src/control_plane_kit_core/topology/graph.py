"""Compiled graph values."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Mapping
from urllib.parse import urlsplit

from control_plane_kit_core.algebra import BlockSockets, BlockSpec
from control_plane_kit_core.configuration import ConfigurationArtifact
from control_plane_kit_core.environment import (
    PublicStaticEnvironmentBinding,
    SocketDerivedEnvironmentBinding,
    validate_socket_environment_value,
)
from control_plane_kit_core.secrets import (
    SecretDelivery,
    SecretEnvironmentDelivery,
    SecretFileDelivery,
    SecretReferenceEnvironmentDelivery,
    SecretReference,
    secret_delivery_sort_key,
)
from control_plane_kit_core.lifecycle import OWNED_EPHEMERAL, ResourceLifecycle
from control_plane_kit_core.types import (
    BlockFamily,
    EndpointScope,
    Protocol,
    RuntimeKind,
    SocketBinding,
)


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
        SecretReference(self.secret_ref)

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
            "protocol": self.protocol.descriptor(),
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
    public_environment: tuple[PublicStaticEnvironmentBinding, ...] = ()
    socket_environment: tuple[SocketDerivedEnvironmentBinding, ...] = ()
    metadata: Mapping[str, object] = field(default_factory=dict)
    lifecycle: ResourceLifecycle = OWNED_EPHEMERAL
    configuration_artifacts: tuple[ConfigurationArtifact, ...] = ()
    secret_deliveries: tuple[SecretDelivery, ...] = ()

    def __post_init__(self) -> None:
        if "environment" in self.metadata:
            raise ValueError("node metadata must not contain environment values")
        if not isinstance(self.public_environment, tuple) or not all(
            isinstance(value, PublicStaticEnvironmentBinding)
            for value in self.public_environment
        ):
            raise TypeError("node public environment must use typed bindings")
        if not isinstance(self.socket_environment, tuple) or not all(
            isinstance(value, SocketDerivedEnvironmentBinding)
            for value in self.socket_environment
        ):
            raise TypeError("node socket environment must use typed bindings")
        environment_names = tuple(
            value.name for value in self.public_environment + self.socket_environment
        )
        if not isinstance(self.configuration_artifacts, tuple) or not all(
            isinstance(value, ConfigurationArtifact)
            for value in self.configuration_artifacts
        ):
            raise TypeError(
                "node configuration artifacts must be ConfigurationArtifact values"
            )
        identities = tuple(value.artifact_id for value in self.configuration_artifacts)
        paths = tuple(value.target_path for value in self.configuration_artifacts)
        if len(set(identities)) != len(identities):
            raise ValueError("node configuration artifact identities must be unique")
        if len(set(paths)) != len(paths):
            raise ValueError("node configuration artifact target paths must be unique")
        if not isinstance(self.secret_deliveries, tuple) or not all(
            isinstance(
                value,
                (
                    SecretEnvironmentDelivery,
                    SecretReferenceEnvironmentDelivery,
                    SecretFileDelivery,
                ),
            )
            for value in self.secret_deliveries
        ):
            raise TypeError("node secret deliveries must be a tuple")
        if len(set(self.secret_deliveries)) != len(self.secret_deliveries):
            raise ValueError("node secret deliveries must be unique")
        secret_environment_names: list[str] = []
        for delivery in self.secret_deliveries:
            match delivery:
                case SecretEnvironmentDelivery(environment_name=name):
                    secret_environment_names.append(name)
                case SecretReferenceEnvironmentDelivery(environment_name=name):
                    secret_environment_names.append(name)
                case SecretFileDelivery(path_binding=path_binding) if path_binding is not None:
                    secret_environment_names.append(path_binding.environment_name)
        all_environment_names = environment_names + tuple(secret_environment_names)
        if len(set(all_environment_names)) != len(all_environment_names):
            raise ValueError("node environment binding names must be unique across sources")

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

    def with_socket_environment(
        self,
        values: tuple[SocketDerivedEnvironmentBinding, ...],
    ) -> Node:
        return replace(self, socket_environment=self.socket_environment + values)

    def non_secret_environment(self) -> dict[str, str]:
        """Interpret public and socket-derived bindings as process literals."""

        return {
            binding.name: binding.value
            for binding in self.public_environment + self.socket_environment
        }

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
                "verification": self.block_spec.verification.descriptor(),
                "metadata": dict(sorted(self.block_spec.metadata.items())),
            },
            "kind": self.kind,
            "runtime_id": self.runtime_id,
            "endpoints": {key: value.descriptor() for key, value in sorted(self.endpoints.items())},
            "environment_bindings": [
                value.descriptor()
                for value in sorted(
                    self.public_environment + self.socket_environment,
                    key=lambda binding: (binding.name, binding.descriptor()["kind"]),
                )
            ],
            "requirements": {
                socket.name: {
                    "protocol": socket.protocol.descriptor(),
                    "binding": socket.binding.value,
                    "env_bindings": list(socket.env_bindings),
                    "required": socket.required,
                }
                for socket in self.sockets.requirements
            },
            "providers": {
                socket.name: {"protocol": socket.protocol.descriptor()}
                for socket in self.sockets.providers
            },
            "metadata": dict(self.metadata),
            "lifecycle": self.lifecycle.descriptor(),
            "configuration_artifacts": [
                value.descriptor() for value in sorted(self.configuration_artifacts)
            ],
            "secret_deliveries": [
                value.descriptor()
                for value in sorted(
                    self.secret_deliveries,
                    key=secret_delivery_sort_key,
                )
            ],
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
    binding: SocketBinding
    env_assignments: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.env_assignments, Mapping) or not all(
            isinstance(name, str) and isinstance(value, str)
            for name, value in self.env_assignments.items()
        ):
            raise TypeError("edge environment assignments must map strings to strings")
        for value in self.env_assignments.values():
            validate_socket_environment_value(value)

    def descriptor(self) -> dict[str, object]:
        return {
            "edge_id": self.edge_id,
            "provider": {"role": self.provider_role, "socket": self.provider_socket},
            "consumer": {"role": self.consumer_role, "requirement": self.requirement_socket},
            "protocol": self.protocol.descriptor(),
            "binding": self.binding.value,
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

    def update_node(self, node: Node) -> DeploymentGraph:
        if node.node_id not in self.nodes:
            raise KeyError(f"cannot update missing node {node.node_id!r}")
        return replace(self, nodes={**self.nodes, node.node_id: node})

    def descriptor(self) -> dict[str, object]:
        from control_plane_kit_core.topology.codec import DEFAULT_GRAPH_CODEC

        return DEFAULT_GRAPH_CODEC.encode(self)
