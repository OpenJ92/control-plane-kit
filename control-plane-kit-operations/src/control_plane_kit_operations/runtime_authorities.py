"""Durable runtime authority admission language for operations."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from hashlib import sha256
from typing import Any, Mapping
from urllib.parse import urlsplit

from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.runtime_authority import (
    RuntimeAuthorityAccessDelivery,
    RuntimeAuthorityAccessDeliveryCodec,
    RuntimeAuthorityReference,
)
from control_plane_kit_core.secrets import SecretReference
from control_plane_kit_core.types import RuntimeKind


class RuntimeAuthorityRegistrationError(ValueError):
    """Raised when runtime authority registration data is malformed."""


class RuntimeAuthorityRegistrationConflict(RuntimeAuthorityRegistrationError):
    """Raised when an authority replacement requires an explicit decision."""


class RuntimeAuthorityAuthorizationDenied(RuntimeAuthorityRegistrationError):
    """Raised when an actor lacks a focused runtime authority scope."""


class RuntimeAuthorityNotFound(RuntimeAuthorityRegistrationError):
    """Raised when a runtime authority cannot be found."""


class RuntimeAuthorityKind(StrEnum):
    """Closed concrete authority variants supported by operations."""

    LOCAL_DOCKER_SOCKET = "local-docker-socket"
    REMOTE_DOCKER_TLS = "remote-docker-tls"


class RegisteredRuntimeAuthorityStatus(StrEnum):
    """Closed durable status for workspace runtime authority registration."""

    ACTIVE = "active"
    REVOKED = "revoked"


class RegisteredRuntimeAuthorityDeliveryStatus(StrEnum):
    """Closed durable status for runtime authority access delivery admission."""

    ACTIVE = "active"
    REVOKED = "revoked"


@dataclass(frozen=True)
class LocalDockerSocketAuthority:
    """Authority to use Docker access granted by the process environment."""

    def descriptor(self) -> dict[str, object]:
        return {"kind": RuntimeAuthorityKind.LOCAL_DOCKER_SOCKET.value}

    def storage_descriptor(self) -> dict[str, object]:
        return self.descriptor()


@dataclass(frozen=True)
class RemoteDockerTlsAuthority:
    """Authority to connect to a remote Docker daemon with TLS secret references."""

    endpoint: str = field(repr=False)
    ca_certificate: SecretReference = field(repr=False)
    client_certificate: SecretReference = field(repr=False)
    client_key: SecretReference = field(repr=False)

    def __post_init__(self) -> None:
        _validate_tcp_endpoint(self.endpoint)
        _require_secret_reference(self.ca_certificate, "ca_certificate")
        _require_secret_reference(self.client_certificate, "client_certificate")
        _require_secret_reference(self.client_key, "client_key")

    def descriptor(self) -> dict[str, object]:
        return {
            "kind": RuntimeAuthorityKind.REMOTE_DOCKER_TLS.value,
            "endpoint": "<redacted>",
            "credential_references": {
                "ca_certificate": self.ca_certificate.reference_id,
                "client_certificate": self.client_certificate.reference_id,
                "client_key": self.client_key.reference_id,
            },
        }

    def storage_descriptor(self) -> dict[str, object]:
        return {
            "kind": RuntimeAuthorityKind.REMOTE_DOCKER_TLS.value,
            "endpoint": self.endpoint,
            "ca_certificate": self.ca_certificate.reference_id,
            "client_certificate": self.client_certificate.reference_id,
            "client_key": self.client_key.reference_id,
        }


DockerRuntimeAuthority = LocalDockerSocketAuthority | RemoteDockerTlsAuthority


class DockerRuntimeAuthorityCodec:
    """Strict storage codec for Docker runtime authority values.

    The encoded mapping is for operations-owned durable storage and interpreter
    IO preparation. Public descriptors should use the authority's redacted
    ``descriptor`` method instead.
    """

    def encode(self, authority: DockerRuntimeAuthority) -> dict[str, object]:
        if not isinstance(
            authority,
            (LocalDockerSocketAuthority, RemoteDockerTlsAuthority),
        ):
            raise RuntimeAuthorityRegistrationError("unsupported runtime authority")
        return authority.storage_descriptor()

    def decode(self, descriptor: Mapping[str, object]) -> DockerRuntimeAuthority:
        mapping = _mapping(descriptor, "runtime authority")
        kind = mapping.get("kind")
        if kind == RuntimeAuthorityKind.LOCAL_DOCKER_SOCKET.value:
            _require_keys(
                mapping,
                frozenset({"kind"}),
                "local Docker socket authority",
            )
            return LocalDockerSocketAuthority()
        if kind == RuntimeAuthorityKind.REMOTE_DOCKER_TLS.value:
            _require_keys(
                mapping,
                frozenset(
                    {
                        "kind",
                        "endpoint",
                        "ca_certificate",
                        "client_certificate",
                        "client_key",
                    }
                ),
                "remote Docker TLS authority",
            )
            return RemoteDockerTlsAuthority(
                endpoint=_text(mapping, "endpoint"),
                ca_certificate=SecretReference(_text(mapping, "ca_certificate")),
                client_certificate=SecretReference(
                    _text(mapping, "client_certificate")
                ),
                client_key=SecretReference(_text(mapping, "client_key")),
            )
        raise RuntimeAuthorityRegistrationError("unsupported runtime authority")


@dataclass(frozen=True)
class RegisteredRuntimeAuthority:
    """A runtime authority admitted as durable workspace operational truth."""

    registration_id: str
    workspace_id: str
    authority_ref: RuntimeAuthorityReference
    runtime_kind: RuntimeKind
    authority: DockerRuntimeAuthority
    admitted_by: str
    admitted_at: str
    status: RegisteredRuntimeAuthorityStatus = RegisteredRuntimeAuthorityStatus.ACTIVE
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_identifier(self.registration_id, "registration_id")
        _validate_identifier(self.workspace_id, "workspace_id")
        _validate_identifier(self.admitted_by, "admitted_by")
        _validate_identifier(self.admitted_at, "admitted_at")
        if not isinstance(self.authority_ref, RuntimeAuthorityReference):
            raise RuntimeAuthorityRegistrationError(
                "registered runtime authority requires RuntimeAuthorityReference"
            )
        if not isinstance(self.runtime_kind, RuntimeKind):
            raise RuntimeAuthorityRegistrationError(
                "registered runtime authority requires RuntimeKind"
            )
        if self.runtime_kind is not RuntimeKind.DOCKER:
            raise RuntimeAuthorityRegistrationError(
                "runtime authority kind is unsupported for runtime"
            )
        if not isinstance(
            self.authority,
            (LocalDockerSocketAuthority, RemoteDockerTlsAuthority),
        ):
            raise RuntimeAuthorityRegistrationError(
                "registered runtime authority is unsupported"
            )
        if not isinstance(self.status, RegisteredRuntimeAuthorityStatus):
            raise RuntimeAuthorityRegistrationError(
                "registered runtime authority status is unsupported"
            )
        if not isinstance(self.metadata, Mapping):
            raise RuntimeAuthorityRegistrationError(
                "registered runtime authority metadata must be mapping"
            )

    @property
    def authority_kind(self) -> RuntimeAuthorityKind:
        if isinstance(self.authority, LocalDockerSocketAuthority):
            return RuntimeAuthorityKind.LOCAL_DOCKER_SOCKET
        if isinstance(self.authority, RemoteDockerTlsAuthority):
            return RuntimeAuthorityKind.REMOTE_DOCKER_TLS
        raise RuntimeAuthorityRegistrationError("unsupported runtime authority")

    def descriptor(self) -> dict[str, object]:
        return {
            "registration_id": self.registration_id,
            "workspace_id": self.workspace_id,
            "authority_ref": self.authority_ref.reference_id,
            "runtime_kind": self.runtime_kind.value,
            "authority_kind": self.authority_kind.value,
            "authority": self.authority.descriptor(),
            "admitted_by": self.admitted_by,
            "admitted_at": self.admitted_at,
            "status": self.status.value,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_authority(
        cls,
        *,
        workspace_id: str,
        authority_ref: RuntimeAuthorityReference,
        runtime_kind: RuntimeKind,
        authority: DockerRuntimeAuthority,
        admitted_by: str,
        admitted_at: str,
    ) -> "RegisteredRuntimeAuthority":
        candidate = cls(
            registration_id=runtime_authority_registration_id_for(
                workspace_id,
                authority_ref,
                runtime_kind,
                authority,
            ),
            workspace_id=workspace_id,
            authority_ref=authority_ref,
            runtime_kind=runtime_kind,
            authority=authority,
            admitted_by=admitted_by,
            admitted_at=admitted_at,
        )
        return candidate


@dataclass(frozen=True)
class RegisteredRuntimeAuthorityDelivery:
    """Workspace/process admission for receiving runtime authority access."""

    delivery_id: str
    workspace_id: str
    delivery: RuntimeAuthorityAccessDelivery
    admitted_by: str
    admitted_at: str
    status: RegisteredRuntimeAuthorityDeliveryStatus = (
        RegisteredRuntimeAuthorityDeliveryStatus.ACTIVE
    )
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_identifier(self.delivery_id, "delivery_id")
        _validate_identifier(self.workspace_id, "workspace_id")
        _validate_identifier(self.admitted_by, "admitted_by")
        _validate_identifier(self.admitted_at, "admitted_at")
        if not isinstance(self.delivery, RuntimeAuthorityAccessDelivery):
            raise RuntimeAuthorityRegistrationError(
                "registered runtime authority delivery requires RuntimeAuthorityAccessDelivery"
            )
        if not isinstance(self.status, RegisteredRuntimeAuthorityDeliveryStatus):
            raise RuntimeAuthorityRegistrationError(
                "registered runtime authority delivery status is unsupported"
            )
        if not isinstance(self.metadata, Mapping):
            raise RuntimeAuthorityRegistrationError(
                "registered runtime authority delivery metadata must be mapping"
            )

    @property
    def authority_ref(self) -> RuntimeAuthorityReference:
        return self.delivery.authority_ref

    def descriptor(self) -> dict[str, object]:
        return {
            "delivery_id": self.delivery_id,
            "workspace_id": self.workspace_id,
            "authority_ref": self.authority_ref.reference_id,
            "delivery_kind": self.delivery.delivery_kind.value,
            "delivery": self.delivery.descriptor(),
            "admitted_by": self.admitted_by,
            "admitted_at": self.admitted_at,
            "status": self.status.value,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_delivery(
        cls,
        *,
        workspace_id: str,
        delivery: RuntimeAuthorityAccessDelivery,
        admitted_by: str,
        admitted_at: str,
    ) -> "RegisteredRuntimeAuthorityDelivery":
        return cls(
            delivery_id=runtime_authority_delivery_id_for(
                workspace_id,
                delivery,
            ),
            workspace_id=workspace_id,
            delivery=delivery,
            admitted_by=admitted_by,
            admitted_at=admitted_at,
        )


@dataclass(frozen=True)
class RegisterRuntimeAuthorityCommand:
    """Application command to admit one runtime authority for a workspace."""

    workspace_id: str
    authority_ref: RuntimeAuthorityReference
    runtime_kind: RuntimeKind
    authority: DockerRuntimeAuthority
    admitted_by: str
    admitted_at: str
    actor_scopes: tuple[PolicyScope, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "actor_scopes", _scopes(self.actor_scopes))
        RegisteredRuntimeAuthority.from_authority(
            workspace_id=self.workspace_id,
            authority_ref=self.authority_ref,
            runtime_kind=self.runtime_kind,
            authority=self.authority,
            admitted_by=self.admitted_by,
            admitted_at=self.admitted_at,
        )


@dataclass(frozen=True)
class RegisterRuntimeAuthorityDeliveryCommand:
    """Application command to admit access delivery for one runtime authority."""

    workspace_id: str
    delivery: RuntimeAuthorityAccessDelivery
    admitted_by: str
    admitted_at: str
    actor_scopes: tuple[PolicyScope, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "actor_scopes", _scopes(self.actor_scopes))
        RegisteredRuntimeAuthorityDelivery.from_delivery(
            workspace_id=self.workspace_id,
            delivery=self.delivery,
            admitted_by=self.admitted_by,
            admitted_at=self.admitted_at,
        )


@dataclass(frozen=True)
class RevokeRuntimeAuthorityCommand:
    """Application command to revoke one workspace runtime authority."""

    workspace_id: str
    authority_ref: RuntimeAuthorityReference
    actor_scopes: tuple[PolicyScope, ...]

    def __post_init__(self) -> None:
        _validate_identifier(self.workspace_id, "workspace_id")
        if not isinstance(self.authority_ref, RuntimeAuthorityReference):
            raise RuntimeAuthorityRegistrationError(
                "revoke requires RuntimeAuthorityReference"
            )
        object.__setattr__(self, "actor_scopes", _scopes(self.actor_scopes))


@dataclass(frozen=True)
class RevokeRuntimeAuthorityDeliveryCommand:
    """Application command to revoke one runtime authority access delivery."""

    workspace_id: str
    authority_ref: RuntimeAuthorityReference
    actor_scopes: tuple[PolicyScope, ...]

    def __post_init__(self) -> None:
        _validate_identifier(self.workspace_id, "workspace_id")
        if not isinstance(self.authority_ref, RuntimeAuthorityReference):
            raise RuntimeAuthorityRegistrationError(
                "delivery revoke requires RuntimeAuthorityReference"
            )
        object.__setattr__(self, "actor_scopes", _scopes(self.actor_scopes))


class RuntimeAuthorityRegistrationService:
    """Application service owning runtime authority transaction boundaries."""

    def __init__(self, unit_of_work_factory: Any) -> None:
        self._unit_of_work_factory = unit_of_work_factory

    def register(
        self,
        command: RegisterRuntimeAuthorityCommand,
    ) -> RegisteredRuntimeAuthority:
        if not isinstance(command, RegisterRuntimeAuthorityCommand):
            raise RuntimeAuthorityRegistrationError(
                "register requires RegisterRuntimeAuthorityCommand"
            )
        if PolicyScope.RUNTIME_AUTHORITY_REGISTER not in command.actor_scopes:
            raise RuntimeAuthorityAuthorizationDenied(
                "runtime authority registration requires runtime-authority:register"
            )
        with self._unit_of_work_factory() as unit_of_work:
            registered = unit_of_work.stores.runtime_authorities.register(
                workspace_id=command.workspace_id,
                authority_ref=command.authority_ref,
                runtime_kind=command.runtime_kind,
                authority=command.authority,
                admitted_by=command.admitted_by,
                admitted_at=command.admitted_at,
            )
            unit_of_work.commit()
            return registered

    def register_delivery(
        self,
        command: RegisterRuntimeAuthorityDeliveryCommand,
    ) -> RegisteredRuntimeAuthorityDelivery:
        if not isinstance(command, RegisterRuntimeAuthorityDeliveryCommand):
            raise RuntimeAuthorityRegistrationError(
                "register_delivery requires RegisterRuntimeAuthorityDeliveryCommand"
            )
        if (
            PolicyScope.RUNTIME_AUTHORITY_DELIVERY_REGISTER
            not in command.actor_scopes
        ):
            raise RuntimeAuthorityAuthorizationDenied(
                "runtime authority delivery registration requires runtime-authority-delivery:register"
            )
        with self._unit_of_work_factory() as unit_of_work:
            registered = unit_of_work.stores.runtime_authority_deliveries.register(
                workspace_id=command.workspace_id,
                delivery=command.delivery,
                admitted_by=command.admitted_by,
                admitted_at=command.admitted_at,
            )
            unit_of_work.commit()
            return registered

    def revoke(
        self,
        command: RevokeRuntimeAuthorityCommand,
    ) -> RegisteredRuntimeAuthority:
        if not isinstance(command, RevokeRuntimeAuthorityCommand):
            raise RuntimeAuthorityRegistrationError(
                "revoke requires RevokeRuntimeAuthorityCommand"
            )
        if PolicyScope.RUNTIME_AUTHORITY_REVOKE not in command.actor_scopes:
            raise RuntimeAuthorityAuthorizationDenied(
                "runtime authority revocation requires runtime-authority:revoke"
            )
        with self._unit_of_work_factory() as unit_of_work:
            registered = unit_of_work.stores.runtime_authorities.revoke(
                command.workspace_id,
                command.authority_ref,
            )
            unit_of_work.commit()
            return registered

    def revoke_delivery(
        self,
        command: RevokeRuntimeAuthorityDeliveryCommand,
    ) -> RegisteredRuntimeAuthorityDelivery:
        if not isinstance(command, RevokeRuntimeAuthorityDeliveryCommand):
            raise RuntimeAuthorityRegistrationError(
                "revoke_delivery requires RevokeRuntimeAuthorityDeliveryCommand"
            )
        if PolicyScope.RUNTIME_AUTHORITY_DELIVERY_REVOKE not in command.actor_scopes:
            raise RuntimeAuthorityAuthorizationDenied(
                "runtime authority delivery revocation requires runtime-authority-delivery:revoke"
            )
        with self._unit_of_work_factory() as unit_of_work:
            registered = unit_of_work.stores.runtime_authority_deliveries.revoke(
                command.workspace_id,
                command.authority_ref,
            )
            unit_of_work.commit()
            return registered


def runtime_authority_registration_id_for(
    workspace_id: str,
    authority_ref: RuntimeAuthorityReference,
    runtime_kind: RuntimeKind,
    authority: DockerRuntimeAuthority,
) -> str:
    """Return deterministic registration identity for one authority admission."""

    _validate_identifier(workspace_id, "workspace_id")
    if not isinstance(authority_ref, RuntimeAuthorityReference):
        raise RuntimeAuthorityRegistrationError(
            "runtime authority id requires RuntimeAuthorityReference"
        )
    if not isinstance(runtime_kind, RuntimeKind):
        raise RuntimeAuthorityRegistrationError(
            "runtime authority id requires RuntimeKind"
        )
    encoded = DockerRuntimeAuthorityCodec().encode(authority)
    digest = sha256(
        repr(
            (
                workspace_id,
                authority_ref.reference_id,
                runtime_kind.value,
                tuple(sorted(encoded.items())),
            )
        ).encode("utf-8")
    ).hexdigest()
    return f"rauth_{digest}"


def runtime_authority_delivery_id_for(
    workspace_id: str,
    delivery: RuntimeAuthorityAccessDelivery,
) -> str:
    """Return deterministic identity for one authority delivery admission."""

    _validate_identifier(workspace_id, "workspace_id")
    if not isinstance(delivery, RuntimeAuthorityAccessDelivery):
        raise RuntimeAuthorityRegistrationError(
            "runtime authority delivery id requires RuntimeAuthorityAccessDelivery"
        )
    encoded = RuntimeAuthorityAccessDeliveryCodec().encode(delivery)
    digest = sha256(repr((workspace_id, encoded)).encode("utf-8")).hexdigest()
    return f"radel_{digest}"


def _validate_tcp_endpoint(endpoint: str) -> None:
    if not isinstance(endpoint, str) or not endpoint:
        raise RuntimeAuthorityRegistrationError("remote Docker TLS endpoint is required")
    if len(endpoint) > 512:
        raise RuntimeAuthorityRegistrationError("remote Docker TLS endpoint is too long")
    parsed = urlsplit(endpoint)
    if parsed.scheme != "tcp":
        raise RuntimeAuthorityRegistrationError(
            "remote Docker TLS authority requires a tcp endpoint"
        )
    if parsed.username is not None or parsed.password is not None:
        raise RuntimeAuthorityRegistrationError(
            "remote Docker TLS endpoint must not contain credentials"
        )
    lowered = endpoint.lower()
    if any(
        marker in lowered
        for marker in (
            "password",
            "token",
            "secret",
            "private_key",
            "private-key",
            "api_key",
        )
    ):
        raise RuntimeAuthorityRegistrationError(
            "remote Docker TLS endpoint must not contain secret-shaped text"
        )
    if not parsed.hostname or parsed.port is None:
        raise RuntimeAuthorityRegistrationError(
            "remote Docker TLS endpoint requires host and port"
        )
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise RuntimeAuthorityRegistrationError(
            "remote Docker TLS endpoint must not contain path, query, or fragment"
        )


def _require_secret_reference(value: object, field: str) -> None:
    if not isinstance(value, SecretReference):
        raise RuntimeAuthorityRegistrationError(f"{field} requires SecretReference")


def _validate_identifier(value: str, field: str) -> None:
    if not isinstance(value, str):
        raise RuntimeAuthorityRegistrationError(f"{field} must be a string")
    if not value or len(value) > 512:
        raise RuntimeAuthorityRegistrationError(f"{field} must be nonempty and bounded")
    if any(ord(character) < 32 for character in value):
        raise RuntimeAuthorityRegistrationError(
            f"{field} must not contain control characters"
        )


def _scopes(value: tuple[PolicyScope, ...]) -> tuple[PolicyScope, ...]:
    if not isinstance(value, tuple):
        raise RuntimeAuthorityRegistrationError(
            "actor_scopes must be a tuple of PolicyScope"
        )
    if not all(isinstance(scope, PolicyScope) for scope in value):
        raise RuntimeAuthorityRegistrationError(
            "actor_scopes must contain only PolicyScope"
        )
    return value


def _mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise RuntimeAuthorityRegistrationError(f"{field} must be a mapping")
    return value


def _require_keys(
    mapping: Mapping[str, object],
    expected: frozenset[str],
    field: str,
) -> None:
    keys = frozenset(mapping)
    if keys != expected:
        extra = sorted(keys - expected)
        missing = sorted(expected - keys)
        details: list[str] = []
        if extra:
            details.append(f"unknown keys: {', '.join(extra)}")
        if missing:
            details.append(f"missing keys: {', '.join(missing)}")
        raise RuntimeAuthorityRegistrationError(
            f"invalid {field}; " + "; ".join(details)
        )


def _text(mapping: Mapping[str, object], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str):
        raise RuntimeAuthorityRegistrationError(f"{key} must be a string")
    return value
