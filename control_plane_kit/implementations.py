"""Runtime implementations for deploy blocks."""

from __future__ import annotations

from dataclasses import dataclass, field

from control_plane_kit.algebra import RoleSockets, RuntimeContext
from control_plane_kit.graph import Endpoint
from control_plane_kit.types import EndpointScope, Protocol, RuntimeKind


@dataclass(frozen=True)
class MaterializedNode:
    """Implementation result consumed by the compiler."""

    kind: str
    endpoints: dict[str, Endpoint]
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class DockerImageImplementation:
    """Run a server as a Docker image under an enclosing Docker runtime."""

    image: str
    command: tuple[str, ...] = ()
    ports: dict[str, int] = field(default_factory=dict)
    kind: str = "docker-image"

    def materialize(self, block_id: str, sockets: RoleSockets, runtime: RuntimeContext) -> MaterializedNode:
        _require_runtime(runtime, RuntimeKind.DOCKER, self.kind)
        endpoints: dict[str, Endpoint] = {}
        for output in sockets.outputs:
            port = self.ports.get(output.name)
            if port is None:
                raise ValueError(f"Docker image block {block_id!r} needs port for output {output.name!r}")
            endpoints[output.name] = Endpoint(
                url=_url(output.protocol, host=f"{runtime.runtime_id}-{block_id}", port=port),
                protocol=output.protocol,
            )
        return MaterializedNode(
            kind=self.kind,
            endpoints=endpoints,
            metadata={"image": self.image, "command": list(self.command)},
        )


@dataclass(frozen=True)
class LocalSourceImplementation:
    """Build/run source code from a local checkout under a runtime interpreter."""

    repo_path: str
    run_command: tuple[str, ...]
    ports: dict[str, int] = field(default_factory=dict)
    build_command: tuple[str, ...] = ()
    kind: str = "local-source"

    def materialize(self, block_id: str, sockets: RoleSockets, runtime: RuntimeContext) -> MaterializedNode:
        endpoints: dict[str, Endpoint] = {}
        for output in sockets.outputs:
            port = self.ports.get(output.name)
            if port is None:
                raise ValueError(f"local source block {block_id!r} needs port for output {output.name!r}")
            host = "127.0.0.1" if runtime.kind is RuntimeKind.DRY_RUN else f"{runtime.runtime_id}-{block_id}"
            endpoints[output.name] = Endpoint(_url(output.protocol, host, port), output.protocol)
        return MaterializedNode(
            kind=self.kind,
            endpoints=endpoints,
            metadata={
                "repo_path": self.repo_path,
                "build_command": list(self.build_command),
                "run_command": list(self.run_command),
            },
        )


@dataclass(frozen=True)
class ExternalHttpImplementation:
    """Observe an already-running HTTP service."""

    url: str
    output_socket: str = "internal"
    kind: str = "external-http"

    def materialize(self, block_id: str, sockets: RoleSockets, runtime: RuntimeContext) -> MaterializedNode:
        sockets.output(self.output_socket)
        return MaterializedNode(
            kind=self.kind,
            endpoints={self.output_socket: Endpoint(self.url, Protocol.HTTP, EndpointScope.PUBLIC)},
            metadata={"owned": False},
        )


@dataclass(frozen=True)
class ExternalTcpImplementation:
    """Observe an already-running TCP service."""

    address: str
    output_socket: str = "internal"
    kind: str = "external-tcp"

    def materialize(self, block_id: str, sockets: RoleSockets, runtime: RuntimeContext) -> MaterializedNode:
        sockets.output(self.output_socket)
        return MaterializedNode(
            kind=self.kind,
            endpoints={self.output_socket: Endpoint(self.address, Protocol.TCP, EndpointScope.PUBLIC)},
            metadata={"owned": False},
        )


@dataclass(frozen=True)
class ExternalPostgresImplementation:
    """Observe an already-running Postgres provider."""

    url: str
    output_socket: str = "internal"
    kind: str = "external-postgres"

    def materialize(self, block_id: str, sockets: RoleSockets, runtime: RuntimeContext) -> MaterializedNode:
        sockets.output(self.output_socket)
        return MaterializedNode(
            kind=self.kind,
            endpoints={self.output_socket: Endpoint(self.url, Protocol.POSTGRES, EndpointScope.PRIVATE)},
            metadata={"owned": False},
        )


@dataclass(frozen=True)
class DockerPostgresImplementation:
    """Run Postgres under an enclosing Docker runtime."""

    database: str = "app"
    username: str = "postgres"
    password: str = "postgres"
    output_socket: str = "internal"
    port: int = 5432
    image: str = "postgres:16-alpine"
    kind: str = "docker-postgres"

    def materialize(self, block_id: str, sockets: RoleSockets, runtime: RuntimeContext) -> MaterializedNode:
        _require_runtime(runtime, RuntimeKind.DOCKER, self.kind)
        sockets.output(self.output_socket)
        host = f"{runtime.runtime_id}-{block_id}"
        url = f"postgresql+psycopg://{self.username}:{self.password}@{host}:{self.port}/{self.database}"
        return MaterializedNode(
            kind=self.kind,
            endpoints={self.output_socket: Endpoint(url, Protocol.POSTGRES)},
            metadata={"image": self.image, "database": self.database},
        )


@dataclass(frozen=True)
class PlanOnlyImplementation:
    """Materialize a node only in the planned graph."""

    kind: str
    output_urls: dict[str, str] = field(default_factory=dict)

    def materialize(self, block_id: str, sockets: RoleSockets, runtime: RuntimeContext) -> MaterializedNode:
        endpoints: dict[str, Endpoint] = {}
        for output in sockets.outputs:
            endpoints[output.name] = Endpoint(
                self.output_urls.get(output.name, f"plan://{block_id}/{output.name}"),
                output.protocol,
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
