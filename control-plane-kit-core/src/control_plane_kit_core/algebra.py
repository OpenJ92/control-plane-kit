"""Pure authoring algebra for deployment topology."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol as TypingProtocol, TypeAlias

from control_plane_kit_core.capabilities import CapabilityName
from control_plane_kit_core.delegation_authority import DelegationAuthorityBinding
from control_plane_kit_core.lifecycle import EXTERNAL_RETAINED, OWNED_EPHEMERAL, ResourceLifecycle
from control_plane_kit_core.public_ingress import NamedPublicIngress
from control_plane_kit_core.runtime_authority import RuntimeAuthorityReference
from control_plane_kit_core.secrets import (
    SecretDelivery,
    SecretEnvironmentDelivery,
    SecretFileDelivery,
    SecretReferenceEnvironmentDelivery,
    secret_delivery_sort_key,
)
from control_plane_kit_core.types import Protocol, RuntimeKind, SocketBinding
from control_plane_kit_core.verification import VerificationContract


@dataclass(frozen=True)
class RequirementSocket:
    """A provider requirement bound at startup or through runtime control."""

    name: str
    protocol: Protocol
    env_bindings: tuple[str, ...]
    required: bool = True
    binding: SocketBinding = SocketBinding.ENVIRONMENT
    secret_deliveries: tuple[SecretDelivery, ...] = ()

    def __post_init__(self) -> None:
        if self.binding is SocketBinding.ENVIRONMENT and not self.env_bindings:
            raise ValueError(f"requirement socket {self.name!r} needs at least one env binding")
        if self.binding is SocketBinding.RUNTIME_CONTROL and self.env_bindings:
            raise ValueError(
                f"runtime-controlled requirement socket {self.name!r} cannot declare env bindings"
            )
        deliveries = tuple(
            sorted(self.secret_deliveries, key=secret_delivery_sort_key)
        )
        if not all(
            isinstance(
                value,
                (
                    SecretEnvironmentDelivery,
                    SecretReferenceEnvironmentDelivery,
                    SecretFileDelivery,
                ),
            )
            for value in deliveries
        ):
            raise TypeError(
                f"requirement socket {self.name!r} secret deliveries must be typed"
            )
        if len(set(deliveries)) != len(deliveries):
            raise ValueError(
                f"requirement socket {self.name!r} secret deliveries must be unique"
            )
        secret_environment_names: list[str] = []
        secret_file_paths: list[str] = []
        for delivery in deliveries:
            match delivery:
                case SecretEnvironmentDelivery(environment_name=name):
                    secret_environment_names.append(name)
                case SecretReferenceEnvironmentDelivery(environment_name=name):
                    secret_environment_names.append(name)
                case SecretFileDelivery(
                    target_path=target_path,
                    path_binding=path_binding,
                ):
                    secret_file_paths.append(target_path)
                    if path_binding is not None:
                        secret_environment_names.append(
                            path_binding.environment_name
                        )
        if len(set(secret_environment_names)) != len(secret_environment_names):
            raise ValueError(
                f"requirement socket {self.name!r} secret environment targets "
                "must be unique"
            )
        if set(self.env_bindings) & set(secret_environment_names):
            raise ValueError(
                f"requirement socket {self.name!r} public and secret environment "
                "targets must be distinct"
            )
        if len(set(secret_file_paths)) != len(secret_file_paths):
            raise ValueError(
                f"requirement socket {self.name!r} secret file targets must be unique"
            )
        object.__setattr__(self, "secret_deliveries", deliveries)


@dataclass(frozen=True)
class ProviderSocket:
    """An endpoint provided by a node."""

    name: str
    protocol: Protocol


@dataclass(frozen=True)
class BlockSockets:
    """The full communication surface of a block."""

    requirements: tuple[RequirementSocket, ...] = ()
    providers: tuple[ProviderSocket, ...] = ()

    def requirement(self, name: str) -> RequirementSocket:
        for socket in self.requirements:
            if socket.name == name:
                return socket
        raise KeyError(f"no requirement socket {name!r}; available: {self.requirement_names()}")

    def provider(self, name: str) -> ProviderSocket:
        for socket in self.providers:
            if socket.name == name:
                return socket
        raise KeyError(f"no provider socket {name!r}; available: {self.provider_names()}")

    def requirement_names(self) -> tuple[str, ...]:
        return tuple(socket.name for socket in self.requirements)

    def provider_names(self) -> tuple[str, ...]:
        return tuple(socket.name for socket in self.providers)


@dataclass(frozen=True)
class BlockSpec:
    """Shared identity and display metadata for any deployable block."""

    role_id: str
    display_name: str | None = None
    health_path: str | None = None
    capabilities: tuple[CapabilityName, ...] = ()
    verification: VerificationContract = field(default_factory=VerificationContract)
    metadata: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.verification, VerificationContract):
            raise TypeError("block verification must be VerificationContract")


class RuntimeImplementation(TypingProtocol):
    """How a block is materialized under an enclosing runtime context."""

    kind: str

    def materialize(self, block_id: str, sockets: BlockSockets, runtime: RuntimeContext) -> object:
        """Return implementation-specific materialization data."""


@dataclass(frozen=True)
class ApplicationBlock:
    """User or package supplied application/server code."""

    spec: BlockSpec
    implementation: RuntimeImplementation
    sockets: BlockSockets

    @property
    def block_id(self) -> str:
        return self.spec.role_id


@dataclass(frozen=True)
class DataBlock:
    """Database, queue, cache, or other data-bearing infrastructure."""

    spec: BlockSpec
    implementation: RuntimeImplementation
    sockets: BlockSockets

    @property
    def block_id(self) -> str:
        return self.spec.role_id


@dataclass(frozen=True)
class ProxyBlock:
    """Reusable proxy/router/control block."""

    spec: BlockSpec
    implementation: RuntimeImplementation
    sockets: BlockSockets

    @property
    def block_id(self) -> str:
        return self.spec.role_id


DeployBlock: TypeAlias = ApplicationBlock | DataBlock | ProxyBlock


@dataclass(frozen=True)
class SocketConnection:
    """Provider socket connected to a consumer requirement socket."""

    provider_role: str
    provider_socket: str
    consumer_role: str
    requirement_socket: str
    protocol: Protocol | None = None
    edge_id: str | None = None


@dataclass(frozen=True)
class RuntimeContext:
    """A runtime interpreter context containing deployable children."""

    runtime_id: str
    kind: RuntimeKind
    authority_ref: RuntimeAuthorityReference | None = None
    children: tuple[DeploymentExpr, ...] = ()
    metadata: dict[str, str] = field(default_factory=dict)
    lifecycle: ResourceLifecycle = OWNED_EPHEMERAL


@dataclass(frozen=True)
class DockerRuntime(RuntimeContext):
    """Docker runtime context.

    Children using Docker implementations are interpreted as containers in this
    shared runtime, not as their own Docker runtimes.
    """

    runtime_id: str = "docker"
    kind: RuntimeKind = RuntimeKind.DOCKER
    network_name: str = "control-plane-kit-network"
    authority_ref: RuntimeAuthorityReference | None = None
    children: tuple[DeploymentExpr, ...] = ()
    metadata: dict[str, str] = field(default_factory=dict)
    lifecycle: ResourceLifecycle = OWNED_EPHEMERAL


@dataclass(frozen=True)
class ExternalRuntime(RuntimeContext):
    """Runtime context for observe-only externally managed services."""

    runtime_id: str = "external"
    kind: RuntimeKind = RuntimeKind.EXTERNAL
    authority_ref: RuntimeAuthorityReference | None = None
    children: tuple[DeploymentExpr, ...] = ()
    metadata: dict[str, str] = field(default_factory=dict)
    lifecycle: ResourceLifecycle = EXTERNAL_RETAINED


DeploymentExpr: TypeAlias = DeployBlock | RuntimeContext | SocketConnection


@dataclass(frozen=True)
class DeploymentTopology:
    """A named declarative deployment source tree."""

    name: str
    root: RuntimeContext
    public_ingresses: tuple[NamedPublicIngress, ...] = ()
    delegation_authorities: tuple[DelegationAuthorityBinding, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.delegation_authorities, tuple) or not all(
            isinstance(value, DelegationAuthorityBinding)
            for value in self.delegation_authorities
        ):
            raise TypeError("delegation authorities must be typed bindings")
        bindings = tuple(
            sorted(
                self.delegation_authorities,
                key=lambda value: (
                    value.delegate_node_id,
                    value.purpose.value,
                    value.issuer,
                ),
            )
        )
        identities = tuple(value.identity for value in bindings)
        if len(set(identities)) != len(identities):
            raise ValueError("delegation authority binding identities must be unique")
        object.__setattr__(self, "delegation_authorities", bindings)


# Backward-compatible rollout alias. New code should use DeploymentTopology.
DeploymentRecipe = DeploymentTopology
