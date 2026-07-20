"""Runtime implementations for deploy blocks."""

from __future__ import annotations

from dataclasses import dataclass, field
from ipaddress import IPv4Address, IPv6Address

from control_plane_kit.algebra import BlockSockets, RuntimeContext
from control_plane_kit.configuration import ConfigurationArtifact
from control_plane_kit.environment import PublicStaticEnvironmentBinding
from control_plane_kit.lifecycle import ResourceLifecycle
from control_plane_kit.secrets import (
    SecretDelivery,
    SecretEnvironmentDelivery,
    SecretFileDelivery,
    SecretFilePathBinding,
    secret_delivery_sort_key,
)
from control_plane_kit.topology.graph import Endpoint, EndpointAddress, LiteralAddress
from control_plane_kit.types import EndpointScope, Protocol, RuntimeKind


@dataclass(frozen=True)
class MaterializedNode:
    """Implementation result consumed by the compiler."""

    kind: str
    endpoints: dict[str, Endpoint]
    metadata: dict[str, object] = field(default_factory=dict)
    public_environment: tuple[PublicStaticEnvironmentBinding, ...] = ()
    lifecycle: ResourceLifecycle = field(default_factory=ResourceLifecycle.owned_ephemeral)
    configuration_artifacts: tuple[ConfigurationArtifact, ...] = ()
    secret_deliveries: tuple[SecretDelivery, ...] = ()


@dataclass(frozen=True)
class HostPublication:
    """Explicit host exposure requested for one provider socket."""

    bind_address: IPv4Address | IPv6Address = IPv4Address("127.0.0.1")
    host_port: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.bind_address, (IPv4Address, IPv6Address)):
            raise TypeError("host publication bind address must be an IP address")
        if self.host_port is not None and (
            type(self.host_port) is not int
            or self.host_port < 1
            or self.host_port > 65_535
        ):
            raise ValueError("host publication port must be between 1 and 65535")

    @classmethod
    def loopback_v4(cls, host_port: int | None = None) -> "HostPublication":
        return cls(IPv4Address("127.0.0.1"), host_port)

    @classmethod
    def loopback_v6(cls, host_port: int | None = None) -> "HostPublication":
        return cls(IPv6Address("::1"), host_port)


@dataclass(frozen=True)
class DockerImageImplementation:
    """Run a server as a Docker image under an enclosing Docker runtime."""

    image: str
    command: tuple[str, ...] = ()
    ports: dict[str, int] = field(default_factory=dict)
    environment: tuple[PublicStaticEnvironmentBinding, ...] = ()
    data_mounts: dict[str, str] = field(default_factory=dict)
    host_publications: dict[str, HostPublication] = field(default_factory=dict)
    configuration_artifacts: tuple[ConfigurationArtifact, ...] = ()
    secret_deliveries: tuple[SecretDelivery, ...] = ()
    lifecycle: ResourceLifecycle = field(default_factory=ResourceLifecycle.owned_ephemeral)
    kind: str = "docker-image"

    def __post_init__(self) -> None:
        if not isinstance(self.environment, tuple) or not all(
            isinstance(value, PublicStaticEnvironmentBinding)
            for value in self.environment
        ):
            raise TypeError(
                "Docker image public environment must be a tuple of "
                "PublicStaticEnvironmentBinding values"
            )
        names = tuple(value.name for value in self.environment)
        if len(set(names)) != len(names):
            raise ValueError("public environment binding names must be unique")

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
        _validate_host_publications(block_id, sockets, self.host_publications)
        _validate_configuration_artifacts(self.configuration_artifacts)
        _validate_secret_deliveries(self.environment, self.secret_deliveries)
        return MaterializedNode(
            kind=self.kind,
            endpoints=endpoints,
            metadata={
                "image": self.image,
                "command": list(self.command),
                "data_mounts": [
                    {"resource_id": resource_id, "target_path": target_path}
                    for resource_id, target_path in sorted(self.data_mounts.items())
                ],
                "host_publications": _host_publication_descriptors(
                    self.host_publications
                ),
            },
            public_environment=tuple(sorted(self.environment)),
            lifecycle=self.lifecycle,
            configuration_artifacts=tuple(sorted(self.configuration_artifacts)),
            secret_deliveries=tuple(
                sorted(self.secret_deliveries, key=secret_delivery_sort_key)
            ),
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
    data_target_path: str = "/var/lib/postgresql/data"
    host_publications: dict[str, HostPublication] = field(default_factory=dict)
    kind: str = "docker-postgres"

    def materialize(self, block_id: str, sockets: BlockSockets, runtime: RuntimeContext) -> MaterializedNode:
        _require_runtime(runtime, RuntimeKind.DOCKER, self.kind)
        sockets.provider(self.provider_socket)
        _validate_host_publications(block_id, sockets, self.host_publications)
        host = f"{runtime.runtime_id}-{block_id}"
        url = f"postgresql+psycopg://{self.username}@{host}:{self.port}/{self.database}"
        return MaterializedNode(
            kind=self.kind,
            endpoints={self.provider_socket: Endpoint(LiteralAddress(url), Protocol.POSTGRES)},
            metadata={
                "image": self.image,
                "database": self.database,
                "data_mounts": [
                    {
                        "resource_id": self.data_resource_id,
                        "target_path": self.data_target_path,
                    }
                ],
                "host_publications": _host_publication_descriptors(
                    self.host_publications
                ),
            },
            public_environment=tuple(
                sorted(
                    (
                        PublicStaticEnvironmentBinding("POSTGRES_DB", self.database),
                        PublicStaticEnvironmentBinding("POSTGRES_USER", self.username),
                        PublicStaticEnvironmentBinding("POSTGRES_HOST_AUTH_METHOD", "trust"),
                    )
                )
            ),
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
        case Protocol.UDP:
            return f"udp://{host}:{port}"
        case Protocol.DNS_TCP:
            return f"dns+tcp://{host}:{port}"
        case Protocol.DNS_UDP:
            return f"dns+udp://{host}:{port}"
        case Protocol.REDIS:
            return f"redis://{host}:{port}"
        case Protocol.SMTP:
            return f"smtp://{host}:{port}"
        case Protocol.OTLP_HTTP:
            return f"http://{host}:{port}"
        case Protocol.OTLP_GRPC:
            return f"grpc://{host}:{port}"
        case Protocol.NATS:
            return f"nats://{host}:{port}"
        case Protocol.AMQP:
            return f"amqp://{host}:{port}"
        case Protocol.KAFKA:
            return f"kafka://{host}:{port}"
        case Protocol.S3:
            return f"s3://{host}:{port}"


def _validate_host_publications(
    block_id: str,
    sockets: BlockSockets,
    publications: dict[str, HostPublication],
) -> None:
    providers = {provider.name: provider.protocol for provider in sockets.providers}
    unknown = set(publications).difference(providers)
    if unknown:
        names = ", ".join(sorted(unknown))
        raise ValueError(
            f"Docker block {block_id!r} publishes unknown provider sockets: {names}"
        )
    fixed = [
        (
            publication.bind_address,
            publication.host_port,
            providers[socket_name].transport,
        )
        for socket_name, publication in publications.items()
        if publication.host_port is not None
    ]
    if len(set(fixed)) != len(fixed):
        raise ValueError("Docker block host publications contain a fixed-port collision")


def _host_publication_descriptors(
    publications: dict[str, HostPublication],
) -> list[dict[str, object]]:
    return [
        {
            "socket_name": socket_name,
            "bind_address": str(publication.bind_address),
            "host_port": publication.host_port,
        }
        for socket_name, publication in sorted(publications.items())
    ]


def _validate_configuration_artifacts(
    artifacts: tuple[ConfigurationArtifact, ...],
) -> None:
    if not isinstance(artifacts, tuple) or not all(
        isinstance(value, ConfigurationArtifact) for value in artifacts
    ):
        raise TypeError("configuration artifacts must be ConfigurationArtifact values")
    identities = tuple(value.artifact_id for value in artifacts)
    paths = tuple(value.target_path for value in artifacts)
    if len(set(identities)) != len(identities):
        raise ValueError("configuration artifact identities must be unique per node")
    if len(set(paths)) != len(paths):
        raise ValueError("configuration artifact target paths must be unique per node")


def _validate_secret_deliveries(
    environment: tuple[PublicStaticEnvironmentBinding, ...],
    deliveries: tuple[SecretDelivery, ...],
) -> None:
    if not isinstance(deliveries, tuple):
        raise TypeError("secret deliveries must be a tuple")
    environment_names: list[str] = []
    file_paths: list[str] = []
    for delivery in deliveries:
        match delivery:
            case SecretEnvironmentDelivery(environment_name=name):
                environment_names.append(name)
            case SecretFileDelivery(target_path=path, path_binding=path_binding):
                file_paths.append(path)
                if isinstance(path_binding, SecretFilePathBinding):
                    environment_names.append(path_binding.environment_name)
            case _:
                raise TypeError("secret delivery must use the closed SecretDelivery language")
    if set(environment_names).intersection(value.name for value in environment):
        raise ValueError("literal and secret environment bindings must not overlap")
    if len(set(environment_names)) != len(environment_names):
        raise ValueError("secret environment names must be unique")
    if len(set(file_paths)) != len(file_paths):
        raise ValueError("secret file target paths must be unique")
