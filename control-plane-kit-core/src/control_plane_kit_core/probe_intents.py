"""Pure probe intent and observation values.

This module describes what may be checked and what has been observed. It never
opens sockets, resolves DNS, calls HTTP, inspects Docker, or interprets
readiness.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TypeAlias
from urllib.parse import urlsplit

from control_plane_kit_core.types import Protocol


class EndpointContext(StrEnum):
    """Where a runtime endpoint address is meaningful."""

    RUNTIME_PRIVATE = "runtime-private"
    HOST_LOCAL = "host-local"
    PUBLIC = "public"


class ProbeKind(StrEnum):
    """Distinct observation layers; no layer implies another."""

    PROCESS = "process"
    TRANSPORT = "transport"
    APPLICATION_HEALTH = "application-health"
    READINESS = "readiness"


class ProbeOutcome(StrEnum):
    """Closed probe outcomes across all observation layers."""

    PROCESS_RUNNING = "process-running"
    PROCESS_STOPPED = "process-stopped"
    REACHABLE = "reachable"
    REFUSED = "refused"
    TIMED_OUT = "timed-out"
    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"
    MALFORMED = "malformed"
    READY = "ready"
    NOT_READY = "not-ready"
    UNKNOWN = "unknown"


class ProbeConstructionCode(StrEnum):
    """Closed failures while deriving an intent from supplied graph facts."""

    MISSING_ENDPOINT = "missing-endpoint"
    MISSING_HEALTH_CONTRACT = "missing-health-contract"
    INCOMPATIBLE_PROTOCOL = "incompatible-protocol"
    ENDPOINT_IDENTITY = "endpoint-identity"
    UNSAFE_HEALTH_PATH = "unsafe-health-path"


@dataclass(frozen=True, order=True)
class TimeoutPolicy:
    """Finite timeout and retry cadence for one probe intent."""

    total_seconds: float = 5.0
    interval_seconds: float = 0.5

    def __post_init__(self) -> None:
        for value, name in (
            (self.total_seconds, "total timeout"),
            (self.interval_seconds, "interval timeout"),
        ):
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or value <= 0
                or value > 300
            ):
                raise ValueError(f"{name} must be greater than zero and bounded")
        if self.interval_seconds > self.total_seconds:
            raise ValueError("probe interval cannot exceed total timeout")


@dataclass(frozen=True, order=True)
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


@dataclass(frozen=True, order=True)
class LiteralEndpointMaterial:
    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str) or not self.value:
            raise ValueError("literal endpoint material must be nonempty text")


@dataclass(frozen=True, order=True)
class SecretEndpointMaterial:
    reference_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.reference_id, str) or not self.reference_id.startswith(
            "secret://"
        ):
            raise ValueError("secret endpoint material must be a secret reference")


EndpointAddressMaterial: TypeAlias = LiteralEndpointMaterial | SecretEndpointMaterial


@dataclass(frozen=True, order=True)
class EndpointDeclaration:
    """One graph-declared endpoint socket shape."""

    socket_name: str
    protocol: Protocol

    def __post_init__(self) -> None:
        if not isinstance(self.socket_name, str) or not self.socket_name.strip():
            raise ValueError("endpoint socket identity must not be empty")
        if not isinstance(self.protocol, Protocol):
            raise TypeError("endpoint protocol must be Protocol")


@dataclass(frozen=True)
class ProbeSubject:
    """The graph facts needed to derive probe intents for one subject."""

    subject_id: str
    endpoints: tuple[EndpointDeclaration, ...] = ()
    health_path: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.subject_id, str) or not self.subject_id.strip():
            raise ValueError("probe subject identity must not be empty")
        if not all(isinstance(value, EndpointDeclaration) for value in self.endpoints):
            raise TypeError("probe subject endpoints must be EndpointDeclaration values")
        identities = tuple(value.socket_name for value in self.endpoints)
        if len(set(identities)) != len(identities):
            raise ValueError("probe subject endpoint identities must be unique")
        if self.health_path is not None:
            _validate_health_path(self.health_path)
        object.__setattr__(self, "endpoints", tuple(sorted(self.endpoints)))


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
            if not isinstance(value, str) or not value.strip():
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
        if not isinstance(self.endpoint, RuntimeEndpointObservation):
            raise TypeError("transport probe endpoint must be RuntimeEndpointObservation")
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
        if not isinstance(self.endpoint, RuntimeEndpointObservation):
            raise TypeError("health probe endpoint must be RuntimeEndpointObservation")
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
        if not isinstance(self.subject_id, str) or not self.subject_id.strip():
            raise ValueError("readiness probe subject identity must not be empty")
        if not isinstance(self.graph_id, str) or not self.graph_id.strip():
            raise ValueError("readiness probe graph identity must not be empty")
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


ProbeIntent: TypeAlias = (
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
        if not isinstance(self.subject_id, str) or not self.subject_id.strip():
            raise ValueError("probe observation subject identity must not be empty")
        if not isinstance(self.graph_id, str) or not self.graph_id.strip():
            raise ValueError("probe observation graph identity must not be empty")
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


@dataclass(frozen=True, order=True)
class ProbeConstructionFailure:
    code: ProbeConstructionCode
    subject_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.code, ProbeConstructionCode):
            raise TypeError("probe construction code must be closed")
        if not isinstance(self.subject_id, str) or not self.subject_id.strip():
            raise ValueError("probe construction subject must not be empty")

    def descriptor(self) -> dict[str, str]:
        return {"code": self.code.value, "subject_id": self.subject_id}


def process_probe(
    subject: ProbeSubject,
    graph_id: str,
    policy: ProbePolicy,
) -> ProcessProbeIntent:
    if not isinstance(subject, ProbeSubject):
        raise TypeError("process probe requires ProbeSubject")
    if not isinstance(graph_id, str) or not graph_id.strip():
        raise ValueError("process probe graph identity must not be empty")
    return ProcessProbeIntent(subject.subject_id, graph_id, policy)


def transport_probe(
    subject: ProbeSubject,
    endpoint: RuntimeEndpointObservation,
    policy: ProbePolicy,
) -> TransportProbeIntent | ProbeConstructionFailure:
    failure = _validate_endpoint_identity(subject, endpoint)
    if failure is not None:
        return failure
    return TransportProbeIntent(subject.subject_id, endpoint.graph_id, endpoint, policy)


def application_health_probe(
    subject: ProbeSubject,
    endpoint: RuntimeEndpointObservation,
    policy: ProbePolicy,
) -> ApplicationHealthProbeIntent | ProbeConstructionFailure:
    failure = _validate_endpoint_identity(subject, endpoint)
    if failure is not None:
        return failure
    if endpoint.protocol != Protocol.HTTP:
        return ProbeConstructionFailure(
            ProbeConstructionCode.INCOMPATIBLE_PROTOCOL,
            subject.subject_id,
        )
    if subject.health_path is None:
        return ProbeConstructionFailure(
            ProbeConstructionCode.MISSING_HEALTH_CONTRACT,
            subject.subject_id,
        )
    return ApplicationHealthProbeIntent(
        subject.subject_id,
        endpoint.graph_id,
        endpoint,
        subject.health_path,
        policy,
    )


def protocol_endpoint_schemes(protocol: Protocol) -> frozenset[str]:
    """Return closed safe URL schemes for one connection protocol."""

    if not isinstance(protocol, Protocol):
        raise TypeError("protocol endpoint schemes require Protocol")
    return protocol.endpoint_schemes()


def probe_outcome_is_valid(kind: ProbeKind, outcome: ProbeOutcome) -> bool:
    if not isinstance(kind, ProbeKind) or not isinstance(outcome, ProbeOutcome):
        return False
    return outcome in _OUTCOMES_BY_KIND[kind]


def _validate_endpoint_identity(
    subject: ProbeSubject,
    endpoint: RuntimeEndpointObservation,
) -> ProbeConstructionFailure | None:
    if not isinstance(subject, ProbeSubject):
        raise TypeError("probe construction requires ProbeSubject")
    if not isinstance(endpoint, RuntimeEndpointObservation):
        raise TypeError("probe construction requires RuntimeEndpointObservation")
    if endpoint.subject_id != subject.subject_id:
        return ProbeConstructionFailure(
            ProbeConstructionCode.ENDPOINT_IDENTITY,
            subject.subject_id,
        )
    declared = {value.socket_name: value for value in subject.endpoints}
    expected = declared.get(endpoint.socket_name)
    if expected is None:
        return ProbeConstructionFailure(
            ProbeConstructionCode.MISSING_ENDPOINT,
            subject.subject_id,
        )
    if expected.protocol != endpoint.protocol:
        return ProbeConstructionFailure(
            ProbeConstructionCode.INCOMPATIBLE_PROTOCOL,
            subject.subject_id,
        )
    return None


def _validate_literal_endpoint(value: str, protocol: Protocol) -> None:
    try:
        parsed = urlsplit(value)
    except ValueError as error:
        raise ValueError("runtime endpoint is not a safe authority") from error
    expected_schemes = protocol_endpoint_schemes(protocol)
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError("runtime endpoint is not a safe authority") from error
    if (
        parsed.scheme not in expected_schemes
        or parsed.hostname is None
        or port is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("runtime endpoint is not a safe authority")


def _validate_health_path(value: str) -> None:
    if not isinstance(value, str):
        raise TypeError("health path must be a string")
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
    if not isinstance(subject_id, str) or not subject_id.strip():
        raise ValueError("probe subject identity must not be empty")
    if not isinstance(graph_id, str) or not graph_id.strip():
        raise ValueError("probe graph identity must not be empty")
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


_OUTCOMES_BY_KIND: dict[ProbeKind, frozenset[ProbeOutcome]] = {
    ProbeKind.PROCESS: frozenset(
        (
            ProbeOutcome.PROCESS_RUNNING,
            ProbeOutcome.PROCESS_STOPPED,
            ProbeOutcome.UNKNOWN,
        )
    ),
    ProbeKind.TRANSPORT: frozenset(
        (
            ProbeOutcome.REACHABLE,
            ProbeOutcome.REFUSED,
            ProbeOutcome.TIMED_OUT,
            ProbeOutcome.UNKNOWN,
        )
    ),
    ProbeKind.APPLICATION_HEALTH: frozenset(
        (
            ProbeOutcome.HEALTHY,
            ProbeOutcome.UNHEALTHY,
            ProbeOutcome.REFUSED,
            ProbeOutcome.TIMED_OUT,
            ProbeOutcome.MALFORMED,
            ProbeOutcome.UNKNOWN,
        )
    ),
    ProbeKind.READINESS: frozenset(
        (
            ProbeOutcome.READY,
            ProbeOutcome.NOT_READY,
            ProbeOutcome.UNKNOWN,
        )
    ),
}
