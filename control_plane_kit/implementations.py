"""Runtime implementations for deploy blocks."""

from __future__ import annotations

from dataclasses import dataclass, field

from control_plane_kit.algebra import BlockSockets, RuntimeContext
from control_plane_kit.lifecycle import ResourceLifecycle
from control_plane_kit.topology.graph import Endpoint, EndpointAddress, LiteralAddress
from control_plane_kit.types import EndpointScope, Protocol, RuntimeKind


@dataclass(frozen=True)
class MaterializedNode:
    """Implementation result consumed by the compiler."""

    kind: str
    endpoints: dict[str, Endpoint]
    metadata: dict[str, object] = field(default_factory=dict)
    lifecycle: ResourceLifecycle = field(default_factory=ResourceLifecycle.owned_ephemeral)


@dataclass(frozen=True)
class DockerImageImplementation:
    """Run a server as a Docker image under an enclosing Docker runtime."""

    image: str
    command: tuple[str, ...] = ()
    ports: dict[str, int] = field(default_factory=dict)
    environment: dict[str, str] = field(default_factory=dict)
    lifecycle: ResourceLifecycle = field(default_factory=ResourceLifecycle.owned_ephemeral)
    kind: str = "docker-image"

    def materialize(self, block_id: str, sockets: BlockSockets, runtime: RuntimeContext) -> MaterializedNode:
        _require_runtime(runtime, RuntimeKind.DOCKER, self.kind)
        endpoints: dict[str, Endpoint] = {}
        for provider in sockets.providers:
            port = self.ports.get(provider.name)
            if port is None:
                raise ValueError(f"Docker image block {block_id!r} needs port for provider {provider.name!r}")
            endpoints[provider.name] = Endpoint(
                address=LiteralAddress(
                    _url(provider.protocol, host=f"{runtime.runtime_id}-{block_id}", port=port)
                ),
                protocol=provider.protocol,
            )
        return MaterializedNode(
            kind=self.kind,
            endpoints=endpoints,
            metadata={
                "image": self.image,
                "command": list(self.command),
                "environment": dict(self.environment),
            },
            lifecycle=self.lifecycle,
        )


@dataclass(frozen=True)
class LocalSourceImplementation:
    """Build/run source code from a local checkout under a runtime interpreter."""

    repo_path: str
    run_command: tuple[str, ...]
    ports: dict[str, int] = field(default_factory=dict)
    build_command: tuple[str, ...] = ()
    kind: str = "local-source"
    lifecycle: ResourceLifecycle = field(default_factory=ResourceLifecycle.owned_ephemeral)

    def materialize(self, block_id: str, sockets: BlockSockets, runtime: RuntimeContext) -> MaterializedNode:
        endpoints: dict[str, Endpoint] = {}
        for provider in sockets.providers:
            port = self.ports.get(provider.name)
            if port is None:
                raise ValueError(f"local source block {block_id!r} needs port for provider {provider.name!r}")
            host = "127.0.0.1" if runtime.kind is RuntimeKind.DRY_RUN else f"{runtime.runtime_id}-{block_id}"
            endpoints[provider.name] = Endpoint(
                LiteralAddress(_url(provider.protocol, host, port)), provider.protocol
            )
        return MaterializedNode(
            kind=self.kind,
            endpoints=endpoints,
            metadata={
                "repo_path": self.repo_path,
                "build_command": list(self.build_command),
                "run_command": list(self.run_command),
            },
            lifecycle=self.lifecycle,
        )


@dataclass(frozen=True)
class ExternalHttpImplementation:
    """Observe an already-running HTTP service."""

    url: str
    provider_socket: str = "internal"
    kind: str = "external-http"

    def materialize(self, block_id: str, sockets: BlockSockets, runtime: RuntimeContext) -> MaterializedNode:
        sockets.provider(self.provider_socket)
        return MaterializedNode(
            kind=self.kind,
            endpoints={
                self.provider_socket: Endpoint(
                    LiteralAddress(self.url), Protocol.HTTP, EndpointScope.PUBLIC
                )
            },
            lifecycle=ResourceLifecycle.external(),
        )


@dataclass(frozen=True)
class ExternalTcpImplementation:
    """Observe an already-running TCP service."""

    address: str
    provider_socket: str = "internal"
    kind: str = "external-tcp"

    def materialize(self, block_id: str, sockets: BlockSockets, runtime: RuntimeContext) -> MaterializedNode:
        sockets.provider(self.provider_socket)
        return MaterializedNode(
            kind=self.kind,
            endpoints={
                self.provider_socket: Endpoint(
                    LiteralAddress(self.address), Protocol.TCP, EndpointScope.PUBLIC
                )
            },
            lifecycle=ResourceLifecycle.external(),
        )


@dataclass(frozen=True)
class ExternalPostgresImplementation:
    """Observe an already-running Postgres provider."""

    address: EndpointAddress
    provider_socket: str = "internal"
    kind: str = "external-postgres"

    def materialize(self, block_id: str, sockets: BlockSockets, runtime: RuntimeContext) -> MaterializedNode:
        sockets.provider(self.provider_socket)
        return MaterializedNode(
            kind=self.kind,
            endpoints={
                self.provider_socket: Endpoint(
                    self.address, Protocol.POSTGRES, EndpointScope.PRIVATE
                )
            },
            lifecycle=ResourceLifecycle.external(),
        )


@dataclass(frozen=True)
class DockerPostgresImplementation:
    """Run Postgres under an enclosing Docker runtime."""

    database: str = "app"
    username: str = "postgres"
    provider_socket: str = "internal"
    port: int = 5432
    image: str = "postgres:16-alpine"
    data_resource_id: str = "postgres-data"
    kind: str = "docker-postgres"

    def materialize(self, block_id: str, sockets: BlockSockets, runtime: RuntimeContext) -> MaterializedNode:
        _require_runtime(runtime, RuntimeKind.DOCKER, self.kind)
        sockets.provider(self.provider_socket)
        host = f"{runtime.runtime_id}-{block_id}"
        url = f"postgresql+psycopg://{self.username}@{host}:{self.port}/{self.database}"
        return MaterializedNode(
            kind=self.kind,
            endpoints={self.provider_socket: Endpoint(LiteralAddress(url), Protocol.POSTGRES)},
            metadata={
                "image": self.image,
                "database": self.database,
                "environment": {
                    "POSTGRES_DB": self.database,
                    "POSTGRES_USER": self.username,
                    "POSTGRES_HOST_AUTH_METHOD": "trust",
                },
            },
            lifecycle=ResourceLifecycle.owned_with_retained_data(
                self.data_resource_id
            ),
        )


@dataclass(frozen=True)
class PlanOnlyImplementation:
    """Materialize a node only in the planned graph."""

    kind: str
    output_urls: dict[str, str] = field(default_factory=dict)

    def materialize(self, block_id: str, sockets: BlockSockets, runtime: RuntimeContext) -> MaterializedNode:
        endpoints: dict[str, Endpoint] = {}
        for provider in sockets.providers:
            endpoints[provider.name] = Endpoint(
                LiteralAddress(
                    self.output_urls.get(provider.name, f"plan://{block_id}/{provider.name}")
                ),
                provider.protocol,
            )
        return MaterializedNode(kind=self.kind, endpoints=endpoints, metadata={"planned": True})


def _require_runtime(runtime: RuntimeContext, kind: RuntimeKind, implementation: str) -> None:
    if runtime.kind is not kind:
        raise ValueError(f"implementation {implementation!r} requires runtime {kind.value!r}, got {runtime.kind.value!r}")


def _url(protocol: Protocol, host: str, port: int) -> str:
    match protocol:
        case Protocol.HTTP:
            return f"http://{host}:{port}"
        case Protocol.POSTGRES:
            return f"postgresql+psycopg://{host}:{port}"
        case Protocol.TCP:
            return f"tcp://{host}:{port}"
