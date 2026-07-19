"""Closed package-owned semantic verification language."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from re import fullmatch
from typing import Mapping, TypeAlias
from urllib.parse import urlsplit

from control_plane_kit.types import Protocol


MAX_VERIFICATION_CHECKS = 100
MAX_VERIFICATION_TEXT = 256


class VerificationContractError(ValueError):
    """A verification value cannot enter durable topology."""


class DnsRecordType(StrEnum):
    A = "a"
    AAAA = "aaaa"


class PostgresVerificationOperation(StrEnum):
    SELECT_ONE = "select-one"


class RedisVerificationOperation(StrEnum):
    PING = "ping"


class VerificationCapability(StrEnum):
    HTTP = "http"
    DNS = "dns"
    POSTGRES = "postgres"
    REDIS = "redis"
    BROKER = "broker"
    OBJECT_STORAGE = "object-storage"
    SMTP = "smtp"


class VerificationOutcome(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    TIMED_OUT = "timed-out"
    MALFORMED = "malformed"
    REJECTED = "rejected"


@dataclass(frozen=True)
class VerificationPolicy:
    """Finite execution and evidence bounds shared by semantic checks."""

    timeout_seconds: float = 5.0
    maximum_attempts: int = 1
    maximum_evidence_bytes: int = 16_384

    def __post_init__(self) -> None:
        if (
            not isinstance(self.timeout_seconds, (int, float))
            or isinstance(self.timeout_seconds, bool)
            or self.timeout_seconds <= 0
            or self.timeout_seconds > 60
        ):
            raise VerificationContractError(
                "verification timeout must be greater than zero and at most 60 seconds"
            )
        if type(self.maximum_attempts) is not int or not 1 <= self.maximum_attempts <= 10:
            raise VerificationContractError(
                "verification attempts must be between 1 and 10"
            )
        if (
            type(self.maximum_evidence_bytes) is not int
            or not 1 <= self.maximum_evidence_bytes <= 65_536
        ):
            raise VerificationContractError(
                "verification evidence bound must be between 1 and 65536 bytes"
            )

    def descriptor(self) -> dict[str, object]:
        return {
            "timeout_seconds": float(self.timeout_seconds),
            "maximum_attempts": self.maximum_attempts,
            "maximum_evidence_bytes": self.maximum_evidence_bytes,
        }

    @classmethod
    def from_descriptor(cls, value: object) -> "VerificationPolicy":
        descriptor = _mapping(value, "verification policy")
        _require_keys(
            descriptor,
            {"timeout_seconds", "maximum_attempts", "maximum_evidence_bytes"},
            "verification policy",
        )
        try:
            return cls(
                timeout_seconds=_number(descriptor, "timeout_seconds"),
                maximum_attempts=_integer(descriptor, "maximum_attempts"),
                maximum_evidence_bytes=_integer(
                    descriptor, "maximum_evidence_bytes"
                ),
            )
        except (TypeError, ValueError) as error:
            raise VerificationContractError(
                "verification policy descriptor is malformed"
            ) from error


@dataclass(frozen=True, kw_only=True)
class HttpCheck:
    check_id: str
    provider_socket: str
    path: str
    expected_statuses: tuple[int, ...] = (200,)
    policy: VerificationPolicy = field(default_factory=VerificationPolicy)

    def __post_init__(self) -> None:
        _validate_identity(self.check_id, "check")
        _validate_identity(self.provider_socket, "provider socket")
        _validate_http_path(self.path)
        if not self.expected_statuses or any(
            type(value) is not int or not 100 <= value <= 599
            for value in self.expected_statuses
        ):
            raise VerificationContractError(
                "HTTP verification statuses must be between 100 and 599"
            )
        object.__setattr__(
            self, "expected_statuses", tuple(sorted(set(self.expected_statuses)))
        )
        _validate_policy(self.policy)

    def descriptor(self) -> dict[str, object]:
        return {
            **_base_descriptor("http", self),
            "path": self.path,
            "expected_statuses": list(self.expected_statuses),
        }


@dataclass(frozen=True, kw_only=True)
class DnsResolveCheck:
    check_id: str
    provider_socket: str
    query_name: str
    record_type: DnsRecordType = DnsRecordType.A
    policy: VerificationPolicy = field(default_factory=VerificationPolicy)

    def __post_init__(self) -> None:
        _validate_common(self)
        if (
            len(self.query_name) > 253
            or not fullmatch(
                r"(?=.{1,253}\.?\Z)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)*[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.?",
                self.query_name,
            )
        ):
            raise VerificationContractError("DNS verification query name is invalid")
        if not isinstance(self.record_type, DnsRecordType):
            raise TypeError("DNS verification record type must be DnsRecordType")

    def descriptor(self) -> dict[str, object]:
        return {
            **_base_descriptor("dns-resolve", self),
            "query_name": self.query_name,
            "record_type": self.record_type.value,
        }


@dataclass(frozen=True, kw_only=True)
class PostgresQueryCheck:
    check_id: str
    provider_socket: str
    operation: PostgresVerificationOperation = PostgresVerificationOperation.SELECT_ONE
    policy: VerificationPolicy = field(default_factory=VerificationPolicy)

    def __post_init__(self) -> None:
        _validate_common(self)
        if not isinstance(self.operation, PostgresVerificationOperation):
            raise TypeError(
                "Postgres verification operation must be PostgresVerificationOperation"
            )

    def descriptor(self) -> dict[str, object]:
        return {
            **_base_descriptor("postgres-query", self),
            "operation": self.operation.value,
        }


@dataclass(frozen=True, kw_only=True)
class RedisCheck:
    check_id: str
    provider_socket: str
    operation: RedisVerificationOperation = RedisVerificationOperation.PING
    policy: VerificationPolicy = field(default_factory=VerificationPolicy)

    def __post_init__(self) -> None:
        _validate_common(self)
        if not isinstance(self.operation, RedisVerificationOperation):
            raise TypeError("Redis verification operation must be RedisVerificationOperation")

    def descriptor(self) -> dict[str, object]:
        return {
            **_base_descriptor("redis", self),
            "operation": self.operation.value,
        }


@dataclass(frozen=True, kw_only=True)
class BrokerRoundTripCheck:
    check_id: str
    provider_socket: str
    channel: str
    policy: VerificationPolicy = field(default_factory=VerificationPolicy)

    def __post_init__(self) -> None:
        _validate_common(self)
        _validate_bounded_text(self.channel, "broker channel")

    def descriptor(self) -> dict[str, object]:
        return {**_base_descriptor("broker-round-trip", self), "channel": self.channel}


@dataclass(frozen=True, kw_only=True)
class ObjectStorageRoundTripCheck:
    check_id: str
    provider_socket: str
    bucket: str
    key_prefix: str = "control-plane-kit/verification"
    policy: VerificationPolicy = field(default_factory=VerificationPolicy)

    def __post_init__(self) -> None:
        _validate_common(self)
        if not fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", self.bucket):
            raise VerificationContractError("object-storage bucket is invalid")
        _validate_bounded_text(self.key_prefix, "object-storage key prefix")
        if self.key_prefix.startswith("/") or ".." in self.key_prefix.split("/"):
            raise VerificationContractError("object-storage key prefix is unsafe")

    def descriptor(self) -> dict[str, object]:
        return {
            **_base_descriptor("object-storage-round-trip", self),
            "bucket": self.bucket,
            "key_prefix": self.key_prefix,
        }


@dataclass(frozen=True, kw_only=True)
class SmtpAcceptanceCheck:
    check_id: str
    provider_socket: str
    recipient_reference: str
    policy: VerificationPolicy = field(default_factory=VerificationPolicy)

    def __post_init__(self) -> None:
        _validate_common(self)
        _validate_identity(self.recipient_reference, "SMTP recipient reference")

    def descriptor(self) -> dict[str, object]:
        return {
            **_base_descriptor("smtp-acceptance", self),
            "recipient_reference": self.recipient_reference,
        }


VerificationCheck: TypeAlias = (
    HttpCheck
    | DnsResolveCheck
    | PostgresQueryCheck
    | RedisCheck
    | BrokerRoundTripCheck
    | ObjectStorageRoundTripCheck
    | SmtpAcceptanceCheck
)


@dataclass(frozen=True)
class VerificationContract:
    checks: tuple[VerificationCheck, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.checks, tuple) or not all(
            isinstance(
                value,
                (
                    HttpCheck,
                    DnsResolveCheck,
                    PostgresQueryCheck,
                    RedisCheck,
                    BrokerRoundTripCheck,
                    ObjectStorageRoundTripCheck,
                    SmtpAcceptanceCheck,
                ),
            )
            for value in self.checks
        ):
            raise TypeError("verification checks must be closed VerificationCheck values")
        if len(self.checks) > MAX_VERIFICATION_CHECKS:
            raise VerificationContractError("verification contract exceeds its check bound")
        identities = tuple(value.check_id for value in self.checks)
        if len(set(identities)) != len(identities):
            raise VerificationContractError("verification check identities must be unique")

    def descriptor(self) -> dict[str, object]:
        return {"checks": [value.descriptor() for value in self.checks]}

    @classmethod
    def from_descriptor(cls, value: object) -> "VerificationContract":
        descriptor = _mapping(value, "verification contract")
        _require_keys(descriptor, {"checks"}, "verification contract")
        checks = descriptor["checks"]
        if not isinstance(checks, list):
            raise VerificationContractError("verification checks must be a list")
        return cls(tuple(verification_check_from_descriptor(item) for item in checks))


def expected_protocols(check: VerificationCheck) -> frozenset[Protocol]:
    """Return the exact provider protocols accepted by one check variant."""

    match check:
        case HttpCheck():
            return frozenset((Protocol.HTTP,))
        case DnsResolveCheck():
            return frozenset((Protocol.DNS_TCP, Protocol.DNS_UDP))
        case PostgresQueryCheck():
            return frozenset((Protocol.POSTGRES,))
        case RedisCheck():
            return frozenset((Protocol.REDIS,))
        case BrokerRoundTripCheck():
            return frozenset((Protocol.NATS, Protocol.AMQP, Protocol.KAFKA))
        case ObjectStorageRoundTripCheck():
            return frozenset((Protocol.S3,))
        case SmtpAcceptanceCheck():
            return frozenset((Protocol.SMTP,))


def verification_capability(check: VerificationCheck) -> VerificationCapability:
    """Return the closed interpreter capability required by one check."""

    match check:
        case HttpCheck():
            return VerificationCapability.HTTP
        case DnsResolveCheck():
            return VerificationCapability.DNS
        case PostgresQueryCheck():
            return VerificationCapability.POSTGRES
        case RedisCheck():
            return VerificationCapability.REDIS
        case BrokerRoundTripCheck():
            return VerificationCapability.BROKER
        case ObjectStorageRoundTripCheck():
            return VerificationCapability.OBJECT_STORAGE
        case SmtpAcceptanceCheck():
            return VerificationCapability.SMTP


@dataclass(frozen=True, order=True)
class VerificationIdentity:
    node_id: str
    graph_id: str
    check_id: str

    def __post_init__(self) -> None:
        _validate_identity(self.node_id, "verification node")
        _validate_graph_identity(self.graph_id)
        _validate_identity(self.check_id, "verification check")

    def descriptor(self) -> dict[str, object]:
        return {
            "node_id": self.node_id,
            "graph_id": self.graph_id,
            "check_id": self.check_id,
        }


@dataclass(frozen=True)
class HttpVerificationEvidence:
    status_code: int
    response_bytes: int

    def __post_init__(self) -> None:
        if type(self.status_code) is not int or not 100 <= self.status_code <= 599:
            raise VerificationContractError("HTTP verification status is invalid")
        _validate_evidence_size(self.response_bytes)

    def descriptor(self) -> dict[str, object]:
        return {
            "kind": "http",
            "status_code": self.status_code,
            "response_bytes": self.response_bytes,
        }


@dataclass(frozen=True)
class RedisVerificationEvidence:
    response_bytes: int

    def __post_init__(self) -> None:
        _validate_evidence_size(self.response_bytes)

    def descriptor(self) -> dict[str, object]:
        return {"kind": "redis", "response_bytes": self.response_bytes}


VerificationEvidence: TypeAlias = HttpVerificationEvidence | RedisVerificationEvidence


@dataclass(frozen=True)
class VerificationCompleted:
    identity: VerificationIdentity
    capability: VerificationCapability
    outcome: VerificationOutcome
    attempts: int
    evidence: VerificationEvidence | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.identity, VerificationIdentity):
            raise TypeError("verification completion identity must be typed")
        if not isinstance(self.capability, VerificationCapability):
            raise TypeError("verification completion capability must be typed")
        if not isinstance(self.outcome, VerificationOutcome):
            raise TypeError("verification completion outcome must be typed")
        if type(self.attempts) is not int or not 1 <= self.attempts <= 10:
            raise VerificationContractError("verification attempts must be between 1 and 10")
        if self.evidence is not None and not isinstance(
            self.evidence,
            (HttpVerificationEvidence, RedisVerificationEvidence),
        ):
            raise TypeError("verification completion evidence must be typed")

    def descriptor(self) -> dict[str, object]:
        return {
            "type": "verification-completed",
            "identity": self.identity.descriptor(),
            "capability": self.capability.value,
            "outcome": self.outcome.value,
            "attempts": self.attempts,
            "evidence": None if self.evidence is None else self.evidence.descriptor(),
        }


@dataclass(frozen=True)
class VerificationUnsupported:
    identity: VerificationIdentity
    capability: VerificationCapability

    def __post_init__(self) -> None:
        if not isinstance(self.identity, VerificationIdentity):
            raise TypeError("unsupported verification identity must be typed")
        if not isinstance(self.capability, VerificationCapability):
            raise TypeError("unsupported verification capability must be typed")

    def descriptor(self) -> dict[str, object]:
        return {
            "type": "verification-unsupported",
            "identity": self.identity.descriptor(),
            "capability": self.capability.value,
        }


VerificationResult: TypeAlias = VerificationCompleted | VerificationUnsupported


def verification_check_from_descriptor(value: object) -> VerificationCheck:
    descriptor = _mapping(value, "verification check")
    kind = _text(descriptor, "kind")
    common = {
        "check_id": _text(descriptor, "check_id"),
        "provider_socket": _text(descriptor, "provider_socket"),
        "policy": VerificationPolicy.from_descriptor(descriptor.get("policy")),
    }
    try:
        match kind:
            case "http":
                _require_keys(
                    descriptor,
                    {"kind", "check_id", "provider_socket", "policy", "path", "expected_statuses"},
                    "HTTP verification check",
                )
                statuses = descriptor["expected_statuses"]
                if not isinstance(statuses, list):
                    raise VerificationContractError("HTTP expected statuses must be a list")
                return HttpCheck(
                    **common,
                    path=_text(descriptor, "path"),
                    expected_statuses=tuple(_integer_value(item) for item in statuses),
                )
            case "dns-resolve":
                _require_keys(descriptor, {"kind", "check_id", "provider_socket", "policy", "query_name", "record_type"}, "DNS verification check")
                return DnsResolveCheck(
                    **common,
                    query_name=_text(descriptor, "query_name"),
                    record_type=DnsRecordType(_text(descriptor, "record_type")),
                )
            case "postgres-query":
                _require_keys(descriptor, {"kind", "check_id", "provider_socket", "policy", "operation"}, "Postgres verification check")
                return PostgresQueryCheck(
                    **common,
                    operation=PostgresVerificationOperation(_text(descriptor, "operation")),
                )
            case "redis":
                _require_keys(descriptor, {"kind", "check_id", "provider_socket", "policy", "operation"}, "Redis verification check")
                return RedisCheck(
                    **common,
                    operation=RedisVerificationOperation(_text(descriptor, "operation")),
                )
            case "broker-round-trip":
                _require_keys(descriptor, {"kind", "check_id", "provider_socket", "policy", "channel"}, "broker verification check")
                return BrokerRoundTripCheck(**common, channel=_text(descriptor, "channel"))
            case "object-storage-round-trip":
                _require_keys(descriptor, {"kind", "check_id", "provider_socket", "policy", "bucket", "key_prefix"}, "object-storage verification check")
                return ObjectStorageRoundTripCheck(
                    **common,
                    bucket=_text(descriptor, "bucket"),
                    key_prefix=_text(descriptor, "key_prefix"),
                )
            case "smtp-acceptance":
                _require_keys(descriptor, {"kind", "check_id", "provider_socket", "policy", "recipient_reference"}, "SMTP verification check")
                return SmtpAcceptanceCheck(
                    **common,
                    recipient_reference=_text(descriptor, "recipient_reference"),
                )
            case _:
                raise VerificationContractError(
                    f"unknown verification check variant {kind!r}"
                )
    except (TypeError, ValueError) as error:
        if isinstance(error, VerificationContractError):
            raise
        raise VerificationContractError("verification check descriptor is malformed") from error


def _base_descriptor(kind: str, check: VerificationCheck) -> dict[str, object]:
    return {
        "kind": kind,
        "check_id": check.check_id,
        "provider_socket": check.provider_socket,
        "policy": check.policy.descriptor(),
    }


def _validate_common(check: VerificationCheck) -> None:
    _validate_identity(check.check_id, "check")
    _validate_identity(check.provider_socket, "provider socket")
    _validate_policy(check.policy)


def _validate_policy(value: VerificationPolicy) -> None:
    if not isinstance(value, VerificationPolicy):
        raise TypeError("verification policy must be VerificationPolicy")


def _validate_identity(value: str, name: str) -> None:
    if not isinstance(value, str) or not fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", value):
        raise VerificationContractError(f"{name} identity is invalid")


def _validate_graph_identity(value: str) -> None:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value.encode("utf-8")) > MAX_VERIFICATION_TEXT
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise VerificationContractError("verification graph identity is invalid")


def _validate_bounded_text(value: str, name: str) -> None:
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("utf-8")) > MAX_VERIFICATION_TEXT
        or "\x00" in value
    ):
        raise VerificationContractError(f"{name} is empty or exceeds its bound")


def _validate_evidence_size(value: int) -> None:
    if type(value) is not int or not 0 <= value <= 65_536:
        raise VerificationContractError(
            "verification evidence size must be between 0 and 65536 bytes"
        )


def _validate_http_path(value: str) -> None:
    if not isinstance(value, str) or len(value.encode("utf-8")) > MAX_VERIFICATION_TEXT:
        raise VerificationContractError("HTTP verification path exceeds its bound")
    parsed = urlsplit(value)
    if (
        not value.startswith("/")
        or value.startswith("//")
        or parsed.scheme
        or parsed.netloc
        or parsed.query
        or parsed.fragment
    ):
        raise VerificationContractError(
            "HTTP verification path must be a bounded relative absolute path"
        )


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise VerificationContractError(f"{name} descriptor must be a string-keyed mapping")
    return value


def _require_keys(value: Mapping[str, object], expected: set[str], name: str) -> None:
    if set(value) != expected:
        raise VerificationContractError(f"{name} descriptor has unknown or missing fields")


def _text(value: Mapping[str, object], key: str) -> str:
    result = value.get(key)
    if not isinstance(result, str):
        raise VerificationContractError(f"verification field {key!r} must be text")
    return result


def _integer(value: Mapping[str, object], key: str) -> int:
    return _integer_value(value.get(key))


def _integer_value(value: object) -> int:
    if type(value) is not int:
        raise VerificationContractError("verification field must be an integer")
    return value


def _number(value: Mapping[str, object], key: str) -> float:
    result = value.get(key)
    if not isinstance(result, (int, float)) or isinstance(result, bool):
        raise VerificationContractError(f"verification field {key!r} must be numeric")
    return float(result)
