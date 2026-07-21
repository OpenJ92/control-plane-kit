"""Closed primitive types for topology values."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar, Mapping


class Transport(StrEnum):
    """Network transports understood by runtime interpreters."""

    TCP = "tcp"
    UDP = "udp"


class ApplicationProtocol(StrEnum):
    """Closed semantic protocols understood by socket compatibility checks."""

    RAW = "raw"
    HTTP = "http"
    POSTGRES = "postgres"
    DNS = "dns"
    REDIS = "redis"
    SMTP = "smtp"
    OTLP_HTTP = "otlp-http"
    OTLP_GRPC = "otlp-grpc"
    NATS = "nats"
    AMQP = "amqp"
    KAFKA = "kafka"
    S3 = "s3"


_ALLOWED_TRANSPORTS: dict[ApplicationProtocol, frozenset[Transport]] = {
    ApplicationProtocol.RAW: frozenset((Transport.TCP, Transport.UDP)),
    ApplicationProtocol.HTTP: frozenset((Transport.TCP,)),
    ApplicationProtocol.POSTGRES: frozenset((Transport.TCP,)),
    ApplicationProtocol.DNS: frozenset((Transport.TCP, Transport.UDP)),
    ApplicationProtocol.REDIS: frozenset((Transport.TCP,)),
    ApplicationProtocol.SMTP: frozenset((Transport.TCP,)),
    ApplicationProtocol.OTLP_HTTP: frozenset((Transport.TCP,)),
    ApplicationProtocol.OTLP_GRPC: frozenset((Transport.TCP,)),
    ApplicationProtocol.NATS: frozenset((Transport.TCP,)),
    ApplicationProtocol.AMQP: frozenset((Transport.TCP,)),
    ApplicationProtocol.KAFKA: frozenset((Transport.TCP,)),
    ApplicationProtocol.S3: frozenset((Transport.TCP,)),
}


@dataclass(frozen=True, slots=True)
class Protocol:
    """A valid connection protocol: transport x application semantics."""

    transport: Transport
    application: ApplicationProtocol

    TCP: ClassVar["Protocol"]
    UDP: ClassVar["Protocol"]
    HTTP: ClassVar["Protocol"]
    POSTGRES: ClassVar["Protocol"]
    DNS_TCP: ClassVar["Protocol"]
    DNS_UDP: ClassVar["Protocol"]
    REDIS: ClassVar["Protocol"]
    SMTP: ClassVar["Protocol"]
    OTLP_HTTP: ClassVar["Protocol"]
    OTLP_GRPC: ClassVar["Protocol"]
    NATS: ClassVar["Protocol"]
    AMQP: ClassVar["Protocol"]
    KAFKA: ClassVar["Protocol"]
    S3: ClassVar["Protocol"]

    def __post_init__(self) -> None:
        if not isinstance(self.transport, Transport):
            raise TypeError("protocol transport must be Transport")
        if not isinstance(self.application, ApplicationProtocol):
            raise TypeError("protocol application must be ApplicationProtocol")
        if self.transport not in _ALLOWED_TRANSPORTS[self.application]:
            raise ValueError(
                f"{self.application.value} does not support {self.transport.value}"
            )

    @property
    def value(self) -> str:
        """Return a stable compact name for display and pre-#452 descriptors."""

        if self.application is ApplicationProtocol.RAW:
            return self.transport.value
        if len(_ALLOWED_TRANSPORTS[self.application]) == 1:
            return self.application.value
        return f"{self.application.value}+{self.transport.value}"

    @classmethod
    def parse(cls, value: str) -> "Protocol":
        """Interpret one closed compact name without accepting unknown values."""

        try:
            return _PROTOCOL_BY_VALUE[value]
        except KeyError as error:
            raise ValueError(f"unknown connection protocol {value!r}") from error

    @classmethod
    def allowed_transports(
        cls,
        application: ApplicationProtocol,
    ) -> frozenset[Transport]:
        """Return the closed transport set for one application protocol."""

        if not isinstance(application, ApplicationProtocol):
            raise TypeError("application must be ApplicationProtocol")
        return _ALLOWED_TRANSPORTS[application]

    def compatible_with(self, other: "Protocol") -> bool:
        """Return whether two sockets have identical connection semantics."""

        return self == other

    def endpoint_schemes(self) -> frozenset[str]:
        """Return the closed URL schemes that preserve this protocol product."""

        return _ENDPOINT_SCHEMES[self]

    def descriptor(self) -> dict[str, str]:
        """Return the exact durable product descriptor."""

        return {
            "transport": self.transport.value,
            "application": self.application.value,
        }

    @classmethod
    def from_descriptor(cls, value: Mapping[str, object]) -> "Protocol":
        """Decode one exact durable product descriptor."""

        if set(value) != {"transport", "application"}:
            raise ValueError(
                "protocol descriptor requires exactly transport and application"
            )
        transport_value = value["transport"]
        application_value = value["application"]
        if not isinstance(transport_value, str):
            raise ValueError("protocol transport must be a string")
        if not isinstance(application_value, str):
            raise ValueError("protocol application must be a string")
        try:
            transport = Transport(transport_value)
            application = ApplicationProtocol(application_value)
        except ValueError as error:
            raise ValueError(f"unknown protocol descriptor value: {error}") from error
        candidate = cls(transport, application)
        try:
            return _PROTOCOL_BY_VALUE[candidate.value]
        except KeyError as error:
            raise ValueError("protocol descriptor has no canonical value") from error

    def __str__(self) -> str:
        return self.value


Protocol.TCP = Protocol(Transport.TCP, ApplicationProtocol.RAW)
Protocol.UDP = Protocol(Transport.UDP, ApplicationProtocol.RAW)
Protocol.HTTP = Protocol(Transport.TCP, ApplicationProtocol.HTTP)
Protocol.POSTGRES = Protocol(Transport.TCP, ApplicationProtocol.POSTGRES)
Protocol.DNS_TCP = Protocol(Transport.TCP, ApplicationProtocol.DNS)
Protocol.DNS_UDP = Protocol(Transport.UDP, ApplicationProtocol.DNS)
Protocol.REDIS = Protocol(Transport.TCP, ApplicationProtocol.REDIS)
Protocol.SMTP = Protocol(Transport.TCP, ApplicationProtocol.SMTP)
Protocol.OTLP_HTTP = Protocol(Transport.TCP, ApplicationProtocol.OTLP_HTTP)
Protocol.OTLP_GRPC = Protocol(Transport.TCP, ApplicationProtocol.OTLP_GRPC)
Protocol.NATS = Protocol(Transport.TCP, ApplicationProtocol.NATS)
Protocol.AMQP = Protocol(Transport.TCP, ApplicationProtocol.AMQP)
Protocol.KAFKA = Protocol(Transport.TCP, ApplicationProtocol.KAFKA)
Protocol.S3 = Protocol(Transport.TCP, ApplicationProtocol.S3)


_PROTOCOL_BY_VALUE = {
    protocol.value: protocol
    for protocol in (
        Protocol.TCP,
        Protocol.UDP,
        Protocol.HTTP,
        Protocol.POSTGRES,
        Protocol.DNS_TCP,
        Protocol.DNS_UDP,
        Protocol.REDIS,
        Protocol.SMTP,
        Protocol.OTLP_HTTP,
        Protocol.OTLP_GRPC,
        Protocol.NATS,
        Protocol.AMQP,
        Protocol.KAFKA,
        Protocol.S3,
    )
}

_ENDPOINT_SCHEMES: dict[Protocol, frozenset[str]] = {
    Protocol.HTTP: frozenset(("http", "https")),
    Protocol.POSTGRES: frozenset(("postgres", "postgresql", "postgresql+psycopg")),
    Protocol.TCP: frozenset(("tcp",)),
    Protocol.UDP: frozenset(("udp",)),
    Protocol.DNS_TCP: frozenset(("dns+tcp",)),
    Protocol.DNS_UDP: frozenset(("dns+udp",)),
    Protocol.REDIS: frozenset(("redis", "rediss")),
    Protocol.SMTP: frozenset(("smtp", "smtps")),
    Protocol.OTLP_HTTP: frozenset(("http", "https")),
    Protocol.OTLP_GRPC: frozenset(("grpc", "grpcs")),
    Protocol.NATS: frozenset(("nats",)),
    Protocol.AMQP: frozenset(("amqp", "amqps")),
    Protocol.KAFKA: frozenset(("kafka",)),
    Protocol.S3: frozenset(("s3", "http", "https")),
}


class SocketBinding(StrEnum):
    """How a requirement connection becomes available to its consumer."""

    ENVIRONMENT = "environment"
    RUNTIME_CONTROL = "runtime-control"


class EndpointScope(StrEnum):
    """Descriptive endpoint visibility."""

    LOCAL = "local"
    PRIVATE = "private"
    PUBLIC = "public"


class RuntimeKind(StrEnum):
    """Runtime contexts supplied by the topology tree."""

    DOCKER = "docker"
    EXTERNAL = "external"
    DRY_RUN = "dry-run"
    AWS = "aws"
    KUBERNETES = "kubernetes"


class WorkspaceLifecycle(StrEnum):
    """Lifecycle states shared by workspace and instance records."""

    CREATED = "created"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"
    ARCHIVED = "archived"
    DECONSTRUCTED = "deconstructed"
    DELETED = "deleted"
    FAILED = "failed"


class BlockFamily(StrEnum):
    """Closed authoring roles retained by compiled graph nodes."""

    APPLICATION = "application"
    DATA = "data"
    PROXY = "proxy"
