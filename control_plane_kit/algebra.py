"""Pure authoring algebra for deployment topology."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol as TypingProtocol, TypeAlias

from control_plane_kit.capabilities import CapabilityName
from control_plane_kit.types import Protocol, RuntimeKind


@dataclass(frozen=True)
class RequirementSocket:
    """An env-backed requirement on a consumer node."""

    name: str
    protocol: Protocol
    env_bindings: tuple[str, ...]
    required: bool = True

    def __post_init__(self) -> None:
        if not self.env_bindings:
            raise ValueError(f"requirement socket {self.name!r} needs at least one env binding")


@dataclass(frozen=True)
class ProviderSocket:
    """An endpoint provided by a node."""

    name: str
    protocol: Protocol


@dataclass(frozen=True)
class RoleSockets:
    """The full communication surface of a block."""

    inputs: tuple[RequirementSocket, ...] = ()
    outputs: tuple[ProviderSocket, ...] = ()

    def input(self, name: str) -> RequirementSocket:
        for socket in self.inputs:
            if socket.name == name:
                return socket
        raise KeyError(f"no input socket {name!r}; available: {self.input_names()}")

    def output(self, name: str) -> ProviderSocket:
        for socket in self.outputs:
            if socket.name == name:
                return socket
        raise KeyError(f"no output socket {name!r}; available: {self.output_names()}")

    def input_names(self) -> tuple[str, ...]:
        return tuple(socket.name for socket in self.inputs)

    def output_names(self) -> tuple[str, ...]:
        return tuple(socket.name for socket in self.outputs)


@dataclass(frozen=True)
class BlockSpec:
    """Shared identity and display metadata for any deployable block."""

    role_id: str
    display_name: str | None = None
    health_path: str | None = None
    capabilities: tuple[CapabilityName, ...] = ()
    metadata: dict[str, str] = field(default_factory=dict)


class RuntimeImplementation(TypingProtocol):
    """How a block is materialized under an enclosing runtime context."""

    kind: str

    def materialize(self, block_id: str, sockets: RoleSockets, runtime: RuntimeContext) -> object:
        """Return implementation-specific materialization data."""


@dataclass(frozen=True)
class ApplicationBlock:
    """User or package supplied application/server code."""

    spec: BlockSpec
    implementation: RuntimeImplementation
    sockets: RoleSockets

    @property
    def block_id(self) -> str:
        return self.spec.role_id


@dataclass(frozen=True)
class DataBlock:
    """Database, queue, cache, or other data-bearing infrastructure."""

    spec: BlockSpec
    implementation: RuntimeImplementation
    sockets: RoleSockets

    @property
    def block_id(self) -> str:
        return self.spec.role_id


@dataclass(frozen=True)
class ProxyBlock:
    """Reusable proxy/router/control block."""

    spec: BlockSpec
    implementation: RuntimeImplementation
    sockets: RoleSockets

    @property
    def block_id(self) -> str:
        return self.spec.role_id


DeployBlock: TypeAlias = ApplicationBlock | DataBlock | ProxyBlock


@dataclass(frozen=True)
class SocketConnection:
    """Provider socket connected to a consumer requirement socket."""

    provider_role: str
    output_socket: str
    consumer_role: str
    input_socket: str
    protocol: Protocol | None = None
    edge_id: str | None = None


@dataclass(frozen=True)
class RuntimeContext:
    """A runtime interpreter context containing deployable children."""

    runtime_id: str
    kind: RuntimeKind
    children: tuple[DeploymentExpr, ...] = ()
    metadata: dict[str, str] = field(default_factory=dict)


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


@dataclass(frozen=True)
class ExternalRuntime(RuntimeContext):
    """Runtime context for observe-only externally managed services."""

    runtime_id: str = "external"
    kind: RuntimeKind = RuntimeKind.EXTERNAL
    children: tuple[DeploymentExpr, ...] = ()
    metadata: dict[str, str] = field(default_factory=dict)


DeploymentExpr: TypeAlias = DeployBlock | RuntimeContext | SocketConnection


@dataclass(frozen=True)
class DeploymentRecipe:
    """A named deployment source tree."""

    name: str
    root: RuntimeContext
