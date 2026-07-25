"""Pure runtime-authority reference language."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re
from typing import Mapping

from control_plane_kit_core.secrets import SecretReference, SecretResolutionError


_MAX_TEXT = 512
_RUNTIME_AUTHORITY_REFERENCE = re.compile(r"^[a-z][a-z0-9._-]{0,127}$")
_DELIVERY_LABEL = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
_RUNTIME_AUTHORITY_REFERENCE_KEYS = frozenset({"reference_id"})
_DELIVERY_SECRET_REFERENCE_KEYS = frozenset({"label", "reference_id"})
_RUNTIME_AUTHORITY_ACCESS_DELIVERY_KEYS = frozenset(
    {"authority_ref", "delivery_kind", "secret_references"}
)


class RuntimeEffectContractError(ValueError):
    """Raised when pure runtime-effect material is malformed."""


class RuntimeAuthorityAccessDeliveryKind(StrEnum):
    """Closed ways a process can receive access material for an authority."""

    LOCAL_DOCKER_SOCKET_MOUNT = "local-docker-socket-mount"
    REMOTE_DOCKER_TLS_SECRET_FILES = "remote-docker-tls-secret-files"
    CLOUD_CREDENTIAL_SECRET_SESSION = "cloud-credential-secret-session"


@dataclass(frozen=True, order=True)
class RuntimeAuthorityReference:
    """Secret-free name for an admitted runtime authority."""

    reference_id: str

    def __post_init__(self) -> None:
        _validate_runtime_authority_reference(self.reference_id)

    def descriptor(self) -> dict[str, object]:
        return {"reference_id": self.reference_id}


class RuntimeAuthorityReferenceCodec:
    """Strict codec for runtime authority references."""

    def encode(self, reference: RuntimeAuthorityReference) -> dict[str, object]:
        if not isinstance(reference, RuntimeAuthorityReference):
            raise RuntimeEffectContractError(
                "encode requires RuntimeAuthorityReference"
            )
        return reference.descriptor()

    def decode(self, descriptor: Mapping[str, object]) -> RuntimeAuthorityReference:
        mapping = _authority_mapping(descriptor, "runtime authority reference")
        _require_authority_keys(mapping, _RUNTIME_AUTHORITY_REFERENCE_KEYS)
        reference_id = mapping.get("reference_id")
        if not isinstance(reference_id, str):
            raise RuntimeEffectContractError("reference_id must be text")
        return RuntimeAuthorityReference(reference_id)


@dataclass(frozen=True, order=True)
class RuntimeAuthorityDeliverySecretReference:
    """Secret-free label for one secret needed to deliver authority access."""

    label: str
    reference: SecretReference

    def __post_init__(self) -> None:
        _validate_delivery_label(self.label)
        reference = self.reference
        if isinstance(reference, str):
            try:
                reference = SecretReference(reference)
            except SecretResolutionError as error:
                raise RuntimeEffectContractError(
                    "delivery secret reference is malformed"
                ) from error
        if not isinstance(reference, SecretReference):
            raise RuntimeEffectContractError(
                "delivery secret reference must be SecretReference"
            )
        object.__setattr__(self, "reference", reference)

    def descriptor(self) -> dict[str, object]:
        return {
            "label": self.label,
            "reference_id": self.reference.reference_id,
        }


class RuntimeAuthorityDeliverySecretReferenceCodec:
    """Strict codec for delivery secret references."""

    def encode(
        self,
        reference: RuntimeAuthorityDeliverySecretReference,
    ) -> dict[str, object]:
        if not isinstance(reference, RuntimeAuthorityDeliverySecretReference):
            raise RuntimeEffectContractError(
                "encode requires RuntimeAuthorityDeliverySecretReference"
            )
        return reference.descriptor()

    def decode(
        self,
        descriptor: Mapping[str, object],
    ) -> RuntimeAuthorityDeliverySecretReference:
        mapping = _authority_mapping(descriptor, "delivery secret reference")
        _require_authority_keys(mapping, _DELIVERY_SECRET_REFERENCE_KEYS)
        label = mapping.get("label")
        reference_id = mapping.get("reference_id")
        if not isinstance(label, str):
            raise RuntimeEffectContractError("delivery secret label must be text")
        if not isinstance(reference_id, str):
            raise RuntimeEffectContractError(
                "delivery secret reference_id must be text"
            )
        return RuntimeAuthorityDeliverySecretReference(
            label,
            SecretReference(reference_id),
        )


@dataclass(frozen=True, order=True)
class RuntimeAuthorityAccessDelivery:
    """Pure statement that a process should receive authority access material."""

    authority_ref: RuntimeAuthorityReference
    delivery_kind: RuntimeAuthorityAccessDeliveryKind
    secret_references: tuple[RuntimeAuthorityDeliverySecretReference, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.authority_ref, RuntimeAuthorityReference):
            raise RuntimeEffectContractError(
                "authority delivery requires RuntimeAuthorityReference"
            )
        if not isinstance(self.delivery_kind, RuntimeAuthorityAccessDeliveryKind):
            raise RuntimeEffectContractError("authority delivery kind must be closed")
        secret_references = tuple(sorted(self.secret_references))
        if not all(
            isinstance(value, RuntimeAuthorityDeliverySecretReference)
            for value in secret_references
        ):
            raise RuntimeEffectContractError(
                "authority delivery secrets must be delivery secret references"
            )
        labels = tuple(value.label for value in secret_references)
        if len(set(labels)) != len(labels):
            raise RuntimeEffectContractError(
                "authority delivery secret labels must be unique"
            )
        if (
            self.delivery_kind
            is RuntimeAuthorityAccessDeliveryKind.LOCAL_DOCKER_SOCKET_MOUNT
            and secret_references
        ):
            raise RuntimeEffectContractError(
                "local Docker socket delivery must not carry secret references"
            )
        object.__setattr__(self, "secret_references", secret_references)

    def descriptor(self) -> dict[str, object]:
        return {
            "authority_ref": self.authority_ref.descriptor(),
            "delivery_kind": self.delivery_kind.value,
            "secret_references": [
                value.descriptor() for value in self.secret_references
            ],
        }


class RuntimeAuthorityAccessDeliveryCodec:
    """Strict codec for runtime authority access delivery contracts."""

    def encode(self, delivery: RuntimeAuthorityAccessDelivery) -> dict[str, object]:
        if not isinstance(delivery, RuntimeAuthorityAccessDelivery):
            raise RuntimeEffectContractError(
                "encode requires RuntimeAuthorityAccessDelivery"
            )
        return delivery.descriptor()

    def decode(self, descriptor: Mapping[str, object]) -> RuntimeAuthorityAccessDelivery:
        mapping = _authority_mapping(descriptor, "runtime authority access delivery")
        _require_authority_keys(mapping, _RUNTIME_AUTHORITY_ACCESS_DELIVERY_KEYS)
        delivery_kind = mapping.get("delivery_kind")
        if not isinstance(delivery_kind, str):
            raise RuntimeEffectContractError("delivery_kind must be text")
        try:
            kind = RuntimeAuthorityAccessDeliveryKind(delivery_kind)
        except ValueError as error:
            raise RuntimeEffectContractError(
                "runtime authority access delivery kind is unknown"
            ) from error
        return RuntimeAuthorityAccessDelivery(
            authority_ref=RuntimeAuthorityReferenceCodec().decode(
                _authority_mapping(mapping.get("authority_ref"), "authority_ref")
            ),
            delivery_kind=kind,
            secret_references=_delivery_secret_references(
                mapping.get("secret_references")
            ),
        )


def _authority_mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise RuntimeEffectContractError(f"{label} descriptor must be a mapping")
    return value


def _require_authority_keys(
    mapping: Mapping[str, object],
    expected: frozenset[str],
) -> None:
    keys = frozenset(mapping)
    if keys == expected:
        return
    extra = sorted(keys - expected)
    missing = sorted(expected - keys)
    details: list[str] = []
    if extra:
        details.append(f"unknown keys: {', '.join(extra)}")
    if missing:
        details.append(f"missing keys: {', '.join(missing)}")
    raise RuntimeEffectContractError("invalid descriptor; " + "; ".join(details))


def _delivery_secret_references(
    value: object,
) -> tuple[RuntimeAuthorityDeliverySecretReference, ...]:
    if not isinstance(value, list):
        raise RuntimeEffectContractError("secret_references must be a list")
    return tuple(
        RuntimeAuthorityDeliverySecretReferenceCodec().decode(
            _authority_mapping(item, "delivery secret reference")
        )
        for item in value
    )


def _validate_delivery_label(value: str) -> None:
    if not isinstance(value, str) or not _DELIVERY_LABEL.fullmatch(value):
        raise RuntimeEffectContractError(
            "delivery secret label must be a bounded lowercase identifier"
        )
    if _contains_secret_material(value):
        raise RuntimeEffectContractError(
            "delivery secret label must not contain secret-shaped text"
        )


def _validate_runtime_authority_reference(value: str) -> None:
    if not isinstance(value, str):
        raise RuntimeEffectContractError("runtime authority reference must be text")
    if len(value) > _MAX_TEXT:
        raise RuntimeEffectContractError("runtime authority reference is too long")
    lowered = value.lower()
    if _contains_secret_material(lowered):
        raise RuntimeEffectContractError(
            "runtime authority reference must not contain secret-shaped text"
        )
    if not _RUNTIME_AUTHORITY_REFERENCE.fullmatch(value):
        raise RuntimeEffectContractError(
            "runtime authority reference must be a bounded lowercase identifier"
        )


def _contains_secret_material(value: str) -> bool:
    lowered = value.lower()
    return any(
        marker in lowered
        for marker in (
            "password",
            "token",
            "secret",
            "private-key",
            "private_key",
            "begin-private-key",
            "dockerconfigjson",
            "/var/run/docker.sock",
            "tcp://",
            "unix://",
        )
    )
