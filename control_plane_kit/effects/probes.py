"""Pure probe and runtime-endpoint language for truthful observations."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from urllib.parse import urlsplit

from control_plane_kit.effects.material import (
    EndpointAddressMaterial,
    LiteralEndpointMaterial,
    NodeMaterial,
    SecretEndpointMaterial,
)
from control_plane_kit.effects.values import TimeoutPolicy
from control_plane_kit.execution import (
    EndpointContext,
    ProbeKind,
    ProbeOutcome,
    probe_outcome_is_valid,
)
from control_plane_kit.types import Protocol


class ProbeConstructionCode(StrEnum):
    MISSING_ENDPOINT = "missing-endpoint"
    MISSING_HEALTH_CONTRACT = "missing-health-contract"
    INCOMPATIBLE_PROTOCOL = "incompatible-protocol"
    ENDPOINT_IDENTITY = "endpoint-identity"
    UNSAFE_ENDPOINT = "unsafe-endpoint"
    UNSAFE_HEALTH_PATH = "unsafe-health-path"


@dataclass(frozen=True)
class HttpResponseExpectation:
    """Bounded response contract; bodies are never part of success truth."""

    status_codes: tuple[int, ...] = (200,)

    def __post_init__(self) -> None:
        if not self.status_codes or any(
            type(value) is not int or value < 100 or value > 599
            for value in self.status_codes
        ):
            raise ValueError("HTTP expectation requires status codes between 100 and 599")
        object.__setattr__(self, "status_codes", tuple(sorted(set(self.status_codes))))

    def descriptor(self) -> dict[str, object]:
        return {"status_codes": list(self.status_codes)}


@dataclass(frozen=True)
class ProbePolicy:
    """Finite retry and evidence bounds for one probe."""

    timeout: TimeoutPolicy = field(default_factory=TimeoutPolicy)
    maximum_attempts: int = 1
    maximum_response_bytes: int = 16_384
    http: HttpResponseExpectation = field(default_factory=HttpResponseExpectation)

    def __post_init__(self) -> None:
        if not isinstance(self.timeout, TimeoutPolicy):
            raise TypeError("probe timeout must be TimeoutPolicy")
        if (
            type(self.maximum_attempts) is not int
            or self.maximum_attempts < 1
            or self.maximum_attempts > 100
        ):
            raise ValueError("probe maximum attempts must be between 1 and 100")
        if (
            type(self.maximum_response_bytes) is not int
            or self.maximum_response_bytes < 1
            or self.maximum_response_bytes > 65_536
        ):
            raise ValueError("probe response bound must be between 1 and 65536 bytes")
        if not isinstance(self.http, HttpResponseExpectation):
            raise TypeError("probe HTTP expectation must be HttpResponseExpectation")

    def descriptor(self) -> dict[str, object]:
        return {
            "timeout_seconds": self.timeout.total_seconds,
            "interval_seconds": self.timeout.interval_seconds,
            "maximum_attempts": self.maximum_attempts,
            "maximum_response_bytes": self.maximum_response_bytes,
            "http": self.http.descriptor(),
        }


@dataclass(frozen=True)
class RuntimeEndpointObservation:
    """A graph-correlated endpoint fact supplied by a runtime interpreter."""

    subject_id: str
    socket_name: str
    graph_id: str
    protocol: Protocol
    context: EndpointContext
    address: EndpointAddressMaterial

    def __post_init__(self) -> None:
        for value, name in (
            (self.subject_id, "endpoint subject"),
            (self.socket_name, "endpoint socket"),
            (self.graph_id, "endpoint graph"),
        ):
            if not value.strip():
                raise ValueError(f"{name} identity must not be empty")
        if not isinstance(self.protocol, Protocol):
            raise TypeError("runtime endpoint protocol must be Protocol")
        if not isinstance(self.context, EndpointContext):
            raise TypeError("runtime endpoint context must be EndpointContext")
        if not isinstance(
            self.address,
            (LiteralEndpointMaterial, SecretEndpointMaterial),
        ):
            raise TypeError("runtime endpoint address must be typed material")
        if isinstance(self.address, LiteralEndpointMaterial):
            _validate_literal_endpoint(self.address.value, self.protocol)

    def descriptor(self) -> dict[str, object]:
        address = (
            {"kind": "literal", "value": self.address.value}
            if isinstance(self.address, LiteralEndpointMaterial)
            else {"kind": "secret-reference", "reference_id": self.address.reference_id}
        )
        return {
            "subject_id": self.subject_id,
            "socket_name": self.socket_name,
            "graph_id": self.graph_id,
            "protocol": self.protocol.descriptor(),
            "context": self.context.value,
            "address": address,
        }


@dataclass(frozen=True)
class ProcessProbeIntent:
    subject_id: str
    graph_id: str
    policy: ProbePolicy
    kind: ProbeKind = ProbeKind.PROCESS

    def __post_init__(self) -> None:
        _validate_probe_identity(self.subject_id, self.graph_id, self.policy)

    def descriptor(self) -> dict[str, object]:
        return _intent_descriptor(self.kind, self.subject_id, self.graph_id, self.policy)


@dataclass(frozen=True)
class TransportProbeIntent:
    subject_id: str
    graph_id: str
    endpoint: RuntimeEndpointObservation
    policy: ProbePolicy
    kind: ProbeKind = ProbeKind.TRANSPORT

    def __post_init__(self) -> None:
        _validate_probe_identity(self.subject_id, self.graph_id, self.policy)
        if self.endpoint.subject_id != self.subject_id or self.endpoint.graph_id != self.graph_id:
            raise ValueError("transport probe endpoint identity must match its subject and graph")

    def descriptor(self) -> dict[str, object]:
        return {
            **_intent_descriptor(self.kind, self.subject_id, self.graph_id, self.policy),
            "endpoint": self.endpoint.descriptor(),
        }


@dataclass(frozen=True)
class ApplicationHealthProbeIntent:
    subject_id: str
    graph_id: str
    endpoint: RuntimeEndpointObservation
    health_path: str
    policy: ProbePolicy
    kind: ProbeKind = ProbeKind.APPLICATION_HEALTH

    def __post_init__(self) -> None:
        _validate_probe_identity(self.subject_id, self.graph_id, self.policy)
        if self.endpoint.subject_id != self.subject_id or self.endpoint.graph_id != self.graph_id:
            raise ValueError("health probe endpoint identity must match its subject and graph")
        if self.endpoint.protocol != Protocol.HTTP:
            raise ValueError("application health probes require HTTP endpoints")
        _validate_health_path(self.health_path)

    def descriptor(self) -> dict[str, object]:
        return {
            **_intent_descriptor(self.kind, self.subject_id, self.graph_id, self.policy),
            "endpoint": self.endpoint.descriptor(),
            "health_path": self.health_path,
        }


@dataclass(frozen=True)
class ReadinessProbeIntent:
    subject_id: str
    graph_id: str
    required: tuple[ProbeKind, ...] = (
        ProbeKind.PROCESS,
        ProbeKind.TRANSPORT,
        ProbeKind.APPLICATION_HEALTH,
    )
    kind: ProbeKind = ProbeKind.READINESS

    def __post_init__(self) -> None:
        if not self.subject_id.strip() or not self.graph_id.strip():
            raise ValueError("readiness probe identity must not be empty")
        if not self.required or ProbeKind.READINESS in self.required:
            raise ValueError("readiness must depend on concrete probe kinds")
        if not all(isinstance(value, ProbeKind) for value in self.required):
            raise TypeError("readiness requirements must be ProbeKind values")
        object.__setattr__(self, "required", tuple(dict.fromkeys(self.required)))

    def descriptor(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "subject_id": self.subject_id,
            "graph_id": self.graph_id,
            "required": [value.value for value in self.required],
        }


ProbeIntent = (
    ProcessProbeIntent
    | TransportProbeIntent
    | ApplicationHealthProbeIntent
    | ReadinessProbeIntent
)


@dataclass(frozen=True)
class ProbeObservation:
    """One coherent observed fact; no layer implies another layer."""

    subject_id: str
    graph_id: str
    kind: ProbeKind
    outcome: ProbeOutcome
    attempts: int = 1
    endpoint_context: EndpointContext | None = None

    def __post_init__(self) -> None:
        if not self.subject_id.strip() or not self.graph_id.strip():
            raise ValueError("probe observation identity must not be empty")
        if not isinstance(self.kind, ProbeKind):
            raise TypeError("probe observation kind must be ProbeKind")
        if not isinstance(self.outcome, ProbeOutcome):
            raise TypeError("probe observation outcome must be ProbeOutcome")
        if type(self.attempts) is not int or self.attempts < 1 or self.attempts > 100:
            raise ValueError("probe observation attempts must be between 1 and 100")
        if self.endpoint_context is not None and not isinstance(
            self.endpoint_context,
            EndpointContext,
        ):
            raise TypeError("probe endpoint context must be EndpointContext")
        if not probe_outcome_is_valid(self.kind, self.outcome):
            raise ValueError(
                f"{self.outcome.value} is not a valid {self.kind.value} observation"
            )
        if self.kind is ProbeKind.PROCESS and self.endpoint_context is not None:
            raise ValueError("process observation cannot claim endpoint context")
        if self.kind in (
            ProbeKind.TRANSPORT,
            ProbeKind.APPLICATION_HEALTH,
        ) and self.endpoint_context is None:
            raise ValueError("endpoint probe observation requires endpoint context")

    def descriptor(self) -> dict[str, object]:
        return {
            "subject_id": self.subject_id,
            "graph_id": self.graph_id,
            "kind": self.kind.value,
            "outcome": self.outcome.value,
            "attempts": self.attempts,
            "endpoint_context": (
                None if self.endpoint_context is None else self.endpoint_context.value
            ),
        }


@dataclass(frozen=True)
class ProbeConstructionFailure:
    code: ProbeConstructionCode
    subject_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.code, ProbeConstructionCode):
            raise TypeError("probe construction code must be closed")
        if not self.subject_id.strip():
            raise ValueError("probe construction subject must not be empty")

    def descriptor(self) -> dict[str, str]:
        return {"code": self.code.value, "subject_id": self.subject_id}


def process_probe(
    node: NodeMaterial,
    graph_id: str,
    policy: ProbePolicy,
) -> ProcessProbeIntent:
    if not graph_id.strip():
        raise ValueError("process probe graph identity must not be empty")
    return ProcessProbeIntent(node.node_id, graph_id, policy)


def transport_probe(
    node: NodeMaterial,
    endpoint: RuntimeEndpointObservation,
    policy: ProbePolicy,
) -> TransportProbeIntent | ProbeConstructionFailure:
    failure = _validate_endpoint_identity(node, endpoint)
    if failure is not None:
        return failure
    return TransportProbeIntent(node.node_id, endpoint.graph_id, endpoint, policy)


def application_health_probe(
    node: NodeMaterial,
    endpoint: RuntimeEndpointObservation,
    policy: ProbePolicy,
) -> ApplicationHealthProbeIntent | ProbeConstructionFailure:
    failure = _validate_endpoint_identity(node, endpoint)
    if failure is not None:
        return failure
    if endpoint.protocol != Protocol.HTTP:
        return ProbeConstructionFailure(
            ProbeConstructionCode.INCOMPATIBLE_PROTOCOL,
            node.node_id,
        )
    if node.health_path is None:
        return ProbeConstructionFailure(
            ProbeConstructionCode.MISSING_HEALTH_CONTRACT,
            node.node_id,
        )
    try:
        _validate_health_path(node.health_path)
    except ValueError:
        return ProbeConstructionFailure(
            ProbeConstructionCode.UNSAFE_HEALTH_PATH,
            node.node_id,
        )
    return ApplicationHealthProbeIntent(
        node.node_id,
        endpoint.graph_id,
        endpoint,
        node.health_path,
        policy,
    )


def _validate_endpoint_identity(
    node: NodeMaterial,
    endpoint: RuntimeEndpointObservation,
) -> ProbeConstructionFailure | None:
    if endpoint.subject_id != node.node_id:
        return ProbeConstructionFailure(
            ProbeConstructionCode.ENDPOINT_IDENTITY,
            node.node_id,
        )
    declared = {value.socket_name: value for value in node.endpoints}
    expected = declared.get(endpoint.socket_name)
    if expected is None:
        return ProbeConstructionFailure(
            ProbeConstructionCode.MISSING_ENDPOINT,
            node.node_id,
        )
    if expected.protocol != endpoint.protocol:
        return ProbeConstructionFailure(
            ProbeConstructionCode.INCOMPATIBLE_PROTOCOL,
            node.node_id,
        )
    return None


def _validate_literal_endpoint(value: str, protocol: Protocol) -> None:
    parsed = urlsplit(value)
    expected_schemes = protocol_endpoint_schemes(protocol)
    if (
        parsed.scheme not in expected_schemes
        or parsed.hostname is None
        or parsed.port is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("runtime endpoint is not a safe authority")


def protocol_endpoint_schemes(protocol: Protocol) -> frozenset[str]:
    """Return closed safe URL schemes for one connection protocol."""

    return {
        Protocol.HTTP: frozenset(("http", "https")),
        Protocol.POSTGRES: frozenset(
            ("postgres", "postgresql", "postgresql+psycopg")
        ),
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
    }[protocol]


def _validate_health_path(value: str) -> None:
    parsed = urlsplit(value)
    if (
        not value.startswith("/")
        or value.startswith("//")
        or parsed.scheme
        or parsed.netloc
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("health path must be a relative absolute path without query")


def _validate_probe_identity(
    subject_id: str,
    graph_id: str,
    policy: ProbePolicy,
) -> None:
    if not subject_id.strip() or not graph_id.strip():
        raise ValueError("probe identity must not be empty")
    if not isinstance(policy, ProbePolicy):
        raise TypeError("probe policy must be ProbePolicy")


def _intent_descriptor(
    kind: ProbeKind,
    subject_id: str,
    graph_id: str,
    policy: ProbePolicy,
) -> dict[str, object]:
    return {
        "kind": kind.value,
        "subject_id": subject_id,
        "graph_id": graph_id,
        "policy": policy.descriptor(),
    }
