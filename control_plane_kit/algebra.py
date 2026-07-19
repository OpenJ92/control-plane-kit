"""Pure authoring algebra for deployment topology."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol as TypingProtocol, TypeAlias

from control_plane_kit.capabilities import CapabilityName
from control_plane_kit.lifecycle import EXTERNAL_RETAINED, OWNED_EPHEMERAL, ResourceLifecycle
from control_plane_kit.types import Protocol, RuntimeKind, SocketBinding
from control_plane_kit.verification import VerificationContract


@dataclass(frozen=True)
class RequirementSocket:
    """A provider requirement bound at startup or through runtime control."""

    name: str
    protocol: Protocol
    env_bindings: tuple[str, ...]
    required: bool = True
    binding: SocketBinding = SocketBinding.ENVIRONMENT

    def __post_init__(self) -> None:
        if self.binding is SocketBinding.ENVIRONMENT and not self.env_bindings:
            raise ValueError(f"requirement socket {self.name!r} needs at least one env binding")
        if self.binding is SocketBinding.RUNTIME_CONTROL and self.env_bindings:
            raise ValueError(
                f"runtime-controlled requirement socket {self.name!r} cannot declare env bindings"
            )


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


class PackageServerProduct(StrEnum):
    """Exact package-owned server products understood by the built-in codec."""

    HELLO = "hello"
    HTTP_PROXY = "http-proxy"
    HTTP_ACTIVE_ROUTER = "http-active-router"
    HTTP_CIRCUIT_BREAKER = "http-circuit-breaker"
    HTTP_MULTIPLEXER = "http-multiplexer"
    HTTP_RATE_LIMITER = "http-rate-limiter"
    HTTP_RETRY = "http-retry"
    HTTP_WEIGHTED_LOAD_BALANCER = "http-weighted-load-balancer"
    MANAGED_HTTP_ROUTER = "managed-http-router"
    REQUEST_OBSERVER = "request-observer"


@dataclass(frozen=True, kw_only=True)
class PackageServerSpec(BlockSpec):
    """Block specification retaining exact package-server product identity."""

    product: PackageServerProduct


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
    children: tuple[DeploymentExpr, ...] = ()
    metadata: dict[str, str] = field(default_factory=dict)
    lifecycle: ResourceLifecycle = OWNED_EPHEMERAL


@dataclass(frozen=True)
class ExternalRuntime(RuntimeContext):
    """Runtime context for observe-only externally managed services."""

    runtime_id: str = "external"
    kind: RuntimeKind = RuntimeKind.EXTERNAL
    children: tuple[DeploymentExpr, ...] = ()
    metadata: dict[str, str] = field(default_factory=dict)
    lifecycle: ResourceLifecycle = EXTERNAL_RETAINED


DeploymentExpr: TypeAlias = DeployBlock | RuntimeContext | SocketConnection


@dataclass(frozen=True)
class DeploymentRecipe:
    """A named deployment source tree."""

    name: str
    root: RuntimeContext
