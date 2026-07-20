"""Closed service-discovery values and their exact durable codec."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
import re
from typing import Mapping, TypeAlias
from urllib.parse import urlsplit

from control_plane_kit.topology.graph import (
    Endpoint,
    LiteralAddress,
)
from control_plane_kit.core.types import EndpointScope, Protocol


_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class DiscoveryRegistrationMode(StrEnum):
    """Who is authorized to maintain one registration."""

    CONTROL_PLANE = "control-plane"
    SELF = "self"


class DiscoveryScope(StrEnum):
    """Closed powers understood by the discovery application service."""

    RESOLVE = "discovery:resolve"
    MANAGE = "discovery:manage"
    REGISTER_SELF = "discovery:register-self"


class DiscoveryRegistrationStatus(StrEnum):
    """Closed current-projection status for one registration identity."""

    ACTIVE = "active"
    DEREGISTERED = "deregistered"
    EXPIRED = "expired"


class DiscoveryOutcome(StrEnum):
    """Closed result variants returned by the discovery command service."""

    REGISTERED = "registered"
    HEARTBEAT = "heartbeat"
    DEREGISTERED = "deregistered"
    RESOLVED = "resolved"
    EXPIRED = "expired"


@dataclass(frozen=True, slots=True)
class DiscoveryIdentity:
    workspace_id: str
    service_id: str
    instance_id: str

    def __post_init__(self) -> None:
        _require_identifier("workspace_id", self.workspace_id)
        _require_identifier("service_id", self.service_id)
        _require_identifier("instance_id", self.instance_id)

    def descriptor(self) -> dict[str, str]:
        return {
            "workspace_id": self.workspace_id,
            "service_id": self.service_id,
            "instance_id": self.instance_id,
        }


@dataclass(frozen=True, slots=True)
class DiscoveryLease:
    issued_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        issued = _aware("issued_at", self.issued_at)
        expires = _aware("expires_at", self.expires_at)
        if expires <= issued:
            raise ValueError("discovery lease must expire after it is issued")

    def descriptor(self) -> dict[str, str]:
        return {
            "issued_at": _timestamp(self.issued_at),
            "expires_at": _timestamp(self.expires_at),
        }


@dataclass(frozen=True, slots=True)
class DiscoveryRegistration:
    identity: DiscoveryIdentity
    endpoint: Endpoint
    mode: DiscoveryRegistrationMode
    lease: DiscoveryLease

    def __post_init__(self) -> None:
        if not isinstance(self.identity, DiscoveryIdentity):
            raise TypeError("discovery registration identity must be typed")
        if not isinstance(self.endpoint, Endpoint):
            raise TypeError("discovery registration endpoint must be typed")
        if not isinstance(self.endpoint.address, LiteralAddress):
            raise ValueError("discovery registration requires a literal endpoint address")
        if len(self.endpoint.url.encode()) > 2_048 or any(
            character in self.endpoint.url for character in "\r\n\0"
        ):
            raise ValueError("discovery endpoint address must be bounded and single-line")
        parsed = urlsplit(self.endpoint.url)
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("discovery endpoint address must not contain credentials")
        if parsed.hostname is None or parsed.port is None:
            raise ValueError("discovery endpoint address must identify a network host")
        if parsed.query or parsed.fragment:
            raise ValueError("discovery endpoint address must not contain query or fragment")
        if parsed.scheme.lower() not in self.endpoint.protocol.endpoint_schemes():
            raise ValueError(
                "discovery endpoint address scheme is incompatible with its protocol"
            )
        if self.endpoint.scope is EndpointScope.LOCAL:
            raise ValueError("process-local endpoints cannot be registered for discovery")
        if not isinstance(self.mode, DiscoveryRegistrationMode):
            raise TypeError("discovery registration mode must be typed")
        if not isinstance(self.lease, DiscoveryLease):
            raise TypeError("discovery registration lease must be typed")

    def descriptor(self) -> dict[str, object]:
        return {
            "identity": self.identity.descriptor(),
            "endpoint": self.endpoint.descriptor(),
            "mode": self.mode.value,
            "lease": self.lease.descriptor(),
        }


@dataclass(frozen=True, slots=True)
class DiscoveryRegistrationRecord:
    """Current lease projection for one discoverable instance."""

    registration: DiscoveryRegistration
    status: DiscoveryRegistrationStatus
    revision: int
    updated_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.registration, DiscoveryRegistration):
            raise TypeError("discovery record registration must be typed")
        if not isinstance(self.status, DiscoveryRegistrationStatus):
            raise TypeError("discovery record status must be typed")
        if type(self.revision) is not int or self.revision < 1:
            raise ValueError("discovery record revision must be positive")
        _aware("updated_at", self.updated_at)

    def descriptor(self) -> dict[str, object]:
        return {
            "registration": self.registration.descriptor(),
            "status": self.status.value,
            "revision": self.revision,
            "updated_at": _timestamp(self.updated_at),
        }


@dataclass(frozen=True, slots=True)
class DiscoveryResult:
    outcome: DiscoveryOutcome
    registrations: tuple[DiscoveryRegistrationRecord, ...] = ()
    affected_count: int = 0
    replayed: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, DiscoveryOutcome):
            raise TypeError("discovery result outcome must be typed")
        if not isinstance(self.registrations, tuple) or any(
            not isinstance(value, DiscoveryRegistrationRecord)
            for value in self.registrations
        ):
            raise TypeError("discovery result registrations must be typed")
        if type(self.affected_count) is not int or self.affected_count < 0:
            raise ValueError("discovery result affected_count must be nonnegative")
        if type(self.replayed) is not bool:
            raise TypeError("discovery result replayed must be bool")

    def descriptor(self) -> dict[str, object]:
        return {
            "outcome": self.outcome.value,
            "registrations": [value.descriptor() for value in self.registrations],
            "affected_count": self.affected_count,
        }


@dataclass(frozen=True, slots=True)
class DiscoveryAuthority:
    actor_id: str
    workspace_id: str
    scopes: frozenset[DiscoveryScope]
    subject_service_id: str | None = None
    subject_instance_id: str | None = None

    def __post_init__(self) -> None:
        _require_identifier("actor_id", self.actor_id)
        _require_identifier("workspace_id", self.workspace_id)
        if not isinstance(self.scopes, frozenset) or any(
            not isinstance(scope, DiscoveryScope) for scope in self.scopes
        ):
            raise TypeError("discovery authority scopes must be typed")
        if (self.subject_service_id is None) != (self.subject_instance_id is None):
            raise ValueError(
                "discovery self authority requires service and instance identity together"
            )
        if self.subject_service_id is not None:
            _require_identifier("subject_service_id", self.subject_service_id)
        if self.subject_instance_id is not None:
            _require_identifier("subject_instance_id", self.subject_instance_id)

    def descriptor(self) -> dict[str, object]:
        return {
            "actor_id": self.actor_id,
            "workspace_id": self.workspace_id,
            "scopes": [scope.value for scope in sorted(self.scopes, key=lambda value: value.value)],
            "subject_service_id": self.subject_service_id,
            "subject_instance_id": self.subject_instance_id,
        }


@dataclass(frozen=True, slots=True)
class RegisterDiscoveryInstance:
    command_id: str
    registration: DiscoveryRegistration

    def __post_init__(self) -> None:
        _require_identifier("command_id", self.command_id)
        if not isinstance(self.registration, DiscoveryRegistration):
            raise TypeError("register command requires a typed registration")


@dataclass(frozen=True, slots=True)
class HeartbeatDiscoveryInstance:
    command_id: str
    identity: DiscoveryIdentity
    expected_expires_at: datetime
    replacement_lease: DiscoveryLease

    def __post_init__(self) -> None:
        _command_identity(self.command_id, self.identity)
        _aware("expected_expires_at", self.expected_expires_at)
        if not isinstance(self.replacement_lease, DiscoveryLease):
            raise TypeError("heartbeat replacement lease must be typed")


@dataclass(frozen=True, slots=True)
class DeregisterDiscoveryInstance:
    command_id: str
    identity: DiscoveryIdentity
    expected_expires_at: datetime

    def __post_init__(self) -> None:
        _command_identity(self.command_id, self.identity)
        _aware("expected_expires_at", self.expected_expires_at)


@dataclass(frozen=True, slots=True)
class ResolveDiscoveryService:
    command_id: str
    workspace_id: str
    service_id: str
    observed_at: datetime
    limit: int = 100

    def __post_init__(self) -> None:
        _require_identifier("command_id", self.command_id)
        _require_identifier("workspace_id", self.workspace_id)
        _require_identifier("service_id", self.service_id)
        _aware("observed_at", self.observed_at)
        _require_limit(self.limit)


@dataclass(frozen=True, slots=True)
class ExpireDiscoveryLeases:
    command_id: str
    workspace_id: str
    observed_at: datetime
    limit: int = 100

    def __post_init__(self) -> None:
        _require_identifier("command_id", self.command_id)
        _require_identifier("workspace_id", self.workspace_id)
        _aware("observed_at", self.observed_at)
        _require_limit(self.limit)


DiscoveryCommand: TypeAlias = (
    RegisterDiscoveryInstance
    | HeartbeatDiscoveryInstance
    | DeregisterDiscoveryInstance
    | ResolveDiscoveryService
    | ExpireDiscoveryLeases
)


def discovery_command_descriptor(command: DiscoveryCommand) -> dict[str, object]:
    """Encode one command into the exact durable discovery language."""

    match command:
        case RegisterDiscoveryInstance(command_id=command_id, registration=registration):
            return {
                "variant": "register",
                "command_id": command_id,
                "registration": registration.descriptor(),
            }
        case HeartbeatDiscoveryInstance(
            command_id=command_id,
            identity=identity,
            expected_expires_at=expected,
            replacement_lease=lease,
        ):
            return {
                "variant": "heartbeat",
                "command_id": command_id,
                "identity": identity.descriptor(),
                "expected_expires_at": _timestamp(expected),
                "replacement_lease": lease.descriptor(),
            }
        case DeregisterDiscoveryInstance(
            command_id=command_id,
            identity=identity,
            expected_expires_at=expected,
        ):
            return {
                "variant": "deregister",
                "command_id": command_id,
                "identity": identity.descriptor(),
                "expected_expires_at": _timestamp(expected),
            }
        case ResolveDiscoveryService(
            command_id=command_id,
            workspace_id=workspace_id,
            service_id=service_id,
            observed_at=observed_at,
            limit=limit,
        ):
            return {
                "variant": "resolve",
                "command_id": command_id,
                "workspace_id": workspace_id,
                "service_id": service_id,
                "observed_at": _timestamp(observed_at),
                "limit": limit,
            }
        case ExpireDiscoveryLeases(
            command_id=command_id,
            workspace_id=workspace_id,
            observed_at=observed_at,
            limit=limit,
        ):
            return {
                "variant": "expire",
                "command_id": command_id,
                "workspace_id": workspace_id,
                "observed_at": _timestamp(observed_at),
                "limit": limit,
            }


def discovery_command_from_descriptor(
    value: Mapping[str, object],
) -> DiscoveryCommand:
    """Decode one exact command and reject unknown fields or variants."""

    variant = _text(value, "variant")
    match variant:
        case "register":
            _exact(value, "variant", "command_id", "registration")
            return RegisterDiscoveryInstance(
                _text(value, "command_id"),
                _registration(_mapping(value, "registration")),
            )
        case "heartbeat":
            _exact(
                value,
                "variant",
                "command_id",
                "identity",
                "expected_expires_at",
                "replacement_lease",
            )
            return HeartbeatDiscoveryInstance(
                _text(value, "command_id"),
                _identity(_mapping(value, "identity")),
                _datetime(value, "expected_expires_at"),
                _lease(_mapping(value, "replacement_lease")),
            )
        case "deregister":
            _exact(value, "variant", "command_id", "identity", "expected_expires_at")
            return DeregisterDiscoveryInstance(
                _text(value, "command_id"),
                _identity(_mapping(value, "identity")),
                _datetime(value, "expected_expires_at"),
            )
        case "resolve":
            _exact(
                value,
                "variant",
                "command_id",
                "workspace_id",
                "service_id",
                "observed_at",
                "limit",
            )
            return ResolveDiscoveryService(
                _text(value, "command_id"),
                _text(value, "workspace_id"),
                _text(value, "service_id"),
                _datetime(value, "observed_at"),
                _integer(value, "limit"),
            )
        case "expire":
            _exact(value, "variant", "command_id", "workspace_id", "observed_at", "limit")
            return ExpireDiscoveryLeases(
                _text(value, "command_id"),
                _text(value, "workspace_id"),
                _datetime(value, "observed_at"),
                _integer(value, "limit"),
            )
        case _:
            raise ValueError(f"unknown discovery command variant {variant!r}")


def discovery_registration_from_descriptor(
    value: Mapping[str, object],
) -> DiscoveryRegistration:
    """Decode one exact durable registration."""

    return _registration(value)


def discovery_authority_from_descriptor(
    value: Mapping[str, object],
) -> DiscoveryAuthority:
    """Decode one exact durable authority without accepting open scopes."""

    _exact(
        value,
        "actor_id",
        "workspace_id",
        "scopes",
        "subject_service_id",
        "subject_instance_id",
    )
    scopes_value = value.get("scopes")
    if not isinstance(scopes_value, list) or any(
        not isinstance(scope, str) for scope in scopes_value
    ):
        raise ValueError("discovery authority scopes must be an array of strings")
    try:
        scopes = frozenset(DiscoveryScope(scope) for scope in scopes_value)
    except ValueError as error:
        raise ValueError("unknown discovery authority scope") from error
    if len(scopes) != len(scopes_value):
        raise ValueError("discovery authority scopes must be unique")
    subject_service = value.get("subject_service_id")
    if subject_service is not None and not isinstance(subject_service, str):
        raise ValueError("discovery subject_service_id must be text or null")
    subject_instance = value.get("subject_instance_id")
    if subject_instance is not None and not isinstance(subject_instance, str):
        raise ValueError("discovery subject_instance_id must be text or null")
    return DiscoveryAuthority(
        _text(value, "actor_id"),
        _text(value, "workspace_id"),
        scopes,
        subject_service_id=subject_service,
        subject_instance_id=subject_instance,
    )


def discovery_result_from_descriptor(value: Mapping[str, object]) -> DiscoveryResult:
    """Decode one exact immutable command result snapshot."""

    _exact(value, "outcome", "registrations", "affected_count")
    try:
        outcome = DiscoveryOutcome(_text(value, "outcome"))
    except ValueError as error:
        raise ValueError("unknown discovery result outcome") from error
    registrations_value = value.get("registrations")
    if not isinstance(registrations_value, list):
        raise ValueError("discovery result registrations must be an array")
    registrations = tuple(
        _registration_record(item)
        if isinstance(item, Mapping)
        else _raise_registration_record()
        for item in registrations_value
    )
    return DiscoveryResult(
        outcome,
        registrations,
        _integer(value, "affected_count"),
    )


def _registration_record(value: Mapping[str, object]) -> DiscoveryRegistrationRecord:
    _exact(value, "registration", "status", "revision", "updated_at")
    try:
        status = DiscoveryRegistrationStatus(_text(value, "status"))
    except ValueError as error:
        raise ValueError("unknown discovery registration status") from error
    return DiscoveryRegistrationRecord(
        _registration(_mapping(value, "registration")),
        status,
        _integer(value, "revision"),
        _datetime(value, "updated_at"),
    )


def _raise_registration_record() -> DiscoveryRegistrationRecord:
    raise ValueError("discovery result registration must be an object")


def _registration(value: Mapping[str, object]) -> DiscoveryRegistration:
    _exact(value, "identity", "endpoint", "mode", "lease")
    try:
        mode = DiscoveryRegistrationMode(_text(value, "mode"))
    except ValueError as error:
        raise ValueError("unknown discovery registration mode") from error
    return DiscoveryRegistration(
        _identity(_mapping(value, "identity")),
        _endpoint(_mapping(value, "endpoint")),
        mode,
        _lease(_mapping(value, "lease")),
    )


def _identity(value: Mapping[str, object]) -> DiscoveryIdentity:
    _exact(value, "workspace_id", "service_id", "instance_id")
    return DiscoveryIdentity(
        _text(value, "workspace_id"),
        _text(value, "service_id"),
        _text(value, "instance_id"),
    )


def _lease(value: Mapping[str, object]) -> DiscoveryLease:
    _exact(value, "issued_at", "expires_at")
    return DiscoveryLease(
        _datetime(value, "issued_at"),
        _datetime(value, "expires_at"),
    )


def _endpoint(value: Mapping[str, object]) -> Endpoint:
    _exact(value, "address", "protocol", "scope")
    address = _mapping(value, "address")
    _exact(address, "kind", "value")
    if _text(address, "kind") != "literal":
        raise ValueError("discovery endpoint address must be literal")
    try:
        scope = EndpointScope(_text(value, "scope"))
    except ValueError as error:
        raise ValueError("unknown discovery endpoint scope") from error
    return Endpoint(
        LiteralAddress(_text(address, "value")),
        Protocol.from_descriptor(_mapping(value, "protocol")),
        scope,
    )


def _command_identity(command_id: str, identity: DiscoveryIdentity) -> None:
    _require_identifier("command_id", command_id)
    if not isinstance(identity, DiscoveryIdentity):
        raise TypeError("discovery command identity must be typed")


def _require_identifier(name: str, value: object) -> None:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise ValueError(f"{name} must be a bounded discovery identifier")


def _require_limit(value: object, *, maximum: int = 100) -> None:
    if type(value) is not int or value < 1 or value > maximum:
        raise ValueError(f"discovery limit must be between 1 and {maximum}")


def _aware(name: str, value: object) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{name} must be a timezone-aware datetime")
    return value.astimezone(timezone.utc)


def _timestamp(value: datetime) -> str:
    return _aware("timestamp", value).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _datetime(value: Mapping[str, object], key: str) -> datetime:
    text = _text(value, key)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"discovery field {key!r} must be an ISO timestamp") from error
    return _aware(key, parsed)


def _mapping(value: Mapping[str, object], key: str) -> Mapping[str, object]:
    candidate = value.get(key)
    if not isinstance(candidate, Mapping):
        raise ValueError(f"discovery field {key!r} must be an object")
    return candidate


def _text(value: Mapping[str, object], key: str) -> str:
    candidate = value.get(key)
    if not isinstance(candidate, str):
        raise ValueError(f"discovery field {key!r} must be text")
    return candidate


def _integer(value: Mapping[str, object], key: str) -> int:
    candidate = value.get(key)
    if type(candidate) is not int:
        raise ValueError(f"discovery field {key!r} must be an integer")
    return candidate


def _exact(value: Mapping[str, object], *keys: str) -> None:
    if set(value) != set(keys):
        raise ValueError(f"discovery descriptor requires exactly {', '.join(keys)}")
