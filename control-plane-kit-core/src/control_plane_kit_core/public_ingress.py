"""Provider-neutral named public ingress language."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import re
from typing import Mapping


_MAX_TEXT = 512
_REFERENCE = re.compile(r"^[a-z][a-z0-9._-]{0,127}$")
_NODE_ID = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")
_SOCKET = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
_HOST_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_PUBLIC_INGRESS_REFERENCE_KEYS = frozenset({"reference_id"})
_PUBLIC_INGRESS_TARGET_KEYS = frozenset({"node_id", "provider_socket"})
_NAMED_PUBLIC_INGRESS_KEYS = frozenset(
    {
        "ingress_id",
        "authority_ref",
        "target",
        "connector_node_id",
        "hostname",
        "exposure",
        "lifecycle",
    }
)
_PUBLIC_INGRESS_OBSERVATION_KEYS = frozenset(
    {
        "ingress_id",
        "hostname",
        "url",
        "target",
        "observed_at",
        "status",
        "evidence",
    }
)


class PublicIngressContractError(ValueError):
    """Raised when named public ingress material is malformed."""


class PublicIngressExposure(StrEnum):
    """Closed public exposure protocols for named ingress."""

    HTTPS = "https"


class PublicIngressLifecycle(StrEnum):
    """Closed ownership lifecycle for a public ingress allocation."""

    EPHEMERAL = "ephemeral"
    RETAINED = "retained"
    EXTERNAL = "external"


class PublicIngressObservationStatus(StrEnum):
    """Closed status values for observed public ingress endpoints."""

    READY = "ready"
    UNREADY = "unready"
    UNKNOWN = "unknown"


@dataclass(frozen=True, order=True)
class IngressAuthorityReference:
    """Secret-free name for an admitted public ingress authority."""

    reference_id: str

    def __post_init__(self) -> None:
        _validate_reference(self.reference_id, "ingress authority reference")

    def descriptor(self) -> dict[str, object]:
        return {"reference_id": self.reference_id}


class IngressAuthorityReferenceCodec:
    """Strict codec for public ingress authority references."""

    def encode(self, reference: IngressAuthorityReference) -> dict[str, object]:
        if not isinstance(reference, IngressAuthorityReference):
            raise PublicIngressContractError(
                "encode requires IngressAuthorityReference"
            )
        return reference.descriptor()

    def decode(self, descriptor: Mapping[str, object]) -> IngressAuthorityReference:
        mapping = _mapping(descriptor, "ingress authority reference")
        _require_keys(mapping, _PUBLIC_INGRESS_REFERENCE_KEYS, "ingress authority")
        reference_id = mapping.get("reference_id")
        if not isinstance(reference_id, str):
            raise PublicIngressContractError("reference_id must be text")
        return IngressAuthorityReference(reference_id)


@dataclass(frozen=True, order=True)
class PublicIngressTarget:
    """Provider socket that should become publicly reachable."""

    node_id: str
    provider_socket: str

    def __post_init__(self) -> None:
        _validate_node_id(self.node_id)
        _validate_socket(self.provider_socket)

    def descriptor(self) -> dict[str, object]:
        return {
            "node_id": self.node_id,
            "provider_socket": self.provider_socket,
        }


class PublicIngressTargetCodec:
    """Strict codec for public ingress targets."""

    def encode(self, target: PublicIngressTarget) -> dict[str, object]:
        if not isinstance(target, PublicIngressTarget):
            raise PublicIngressContractError("encode requires PublicIngressTarget")
        return target.descriptor()

    def decode(self, descriptor: Mapping[str, object]) -> PublicIngressTarget:
        mapping = _mapping(descriptor, "public ingress target")
        _require_keys(mapping, _PUBLIC_INGRESS_TARGET_KEYS, "public ingress target")
        node_id = mapping.get("node_id")
        provider_socket = mapping.get("provider_socket")
        if not isinstance(node_id, str):
            raise PublicIngressContractError("target node_id must be text")
        if not isinstance(provider_socket, str):
            raise PublicIngressContractError("target provider_socket must be text")
        return PublicIngressTarget(node_id, provider_socket)


@dataclass(frozen=True, order=True)
class NamedPublicIngress:
    """Provider-neutral request to expose one provider socket by hostname."""

    ingress_id: str
    authority_ref: IngressAuthorityReference
    target: PublicIngressTarget
    connector_node_id: str
    hostname: str
    exposure: PublicIngressExposure = PublicIngressExposure.HTTPS
    lifecycle: PublicIngressLifecycle = PublicIngressLifecycle.EPHEMERAL

    def __post_init__(self) -> None:
        _validate_reference(self.ingress_id, "public ingress id")
        if not isinstance(self.authority_ref, IngressAuthorityReference):
            raise PublicIngressContractError(
                "public ingress requires IngressAuthorityReference"
            )
        if not isinstance(self.target, PublicIngressTarget):
            raise PublicIngressContractError(
                "public ingress target must be PublicIngressTarget"
            )
        _validate_node_id(self.connector_node_id)
        _validate_hostname(self.hostname)
        if not isinstance(self.exposure, PublicIngressExposure):
            raise PublicIngressContractError("public ingress exposure must be closed")
        if not isinstance(self.lifecycle, PublicIngressLifecycle):
            raise PublicIngressContractError("public ingress lifecycle must be closed")

    def descriptor(self) -> dict[str, object]:
        return {
            "ingress_id": self.ingress_id,
            "authority_ref": self.authority_ref.descriptor(),
            "target": self.target.descriptor(),
            "connector_node_id": self.connector_node_id,
            "hostname": self.hostname,
            "exposure": self.exposure.value,
            "lifecycle": self.lifecycle.value,
        }


PublicIngressRequest = NamedPublicIngress


class NamedPublicIngressCodec:
    """Strict codec for named public ingress requests."""

    def encode(self, ingress: NamedPublicIngress) -> dict[str, object]:
        if not isinstance(ingress, NamedPublicIngress):
            raise PublicIngressContractError("encode requires NamedPublicIngress")
        return ingress.descriptor()

    def decode(self, descriptor: Mapping[str, object]) -> NamedPublicIngress:
        mapping = _mapping(descriptor, "named public ingress")
        _require_keys(mapping, _NAMED_PUBLIC_INGRESS_KEYS, "named public ingress")
        exposure = mapping.get("exposure")
        lifecycle = mapping.get("lifecycle")
        if not isinstance(exposure, str):
            raise PublicIngressContractError("public ingress exposure must be text")
        if not isinstance(lifecycle, str):
            raise PublicIngressContractError("public ingress lifecycle must be text")
        try:
            exposure_value = PublicIngressExposure(exposure)
            lifecycle_value = PublicIngressLifecycle(lifecycle)
        except ValueError as error:
            raise PublicIngressContractError(
                "public ingress closed value is unknown"
            ) from error
        return NamedPublicIngress(
            ingress_id=_text(mapping, "ingress_id"),
            authority_ref=IngressAuthorityReferenceCodec().decode(
                _mapping(mapping.get("authority_ref"), "authority_ref")
            ),
            target=PublicIngressTargetCodec().decode(
                _mapping(mapping.get("target"), "target")
            ),
            connector_node_id=_text(mapping, "connector_node_id"),
            hostname=_text(mapping, "hostname"),
            exposure=exposure_value,
            lifecycle=lifecycle_value,
        )


@dataclass(frozen=True)
class PublicIngressObservation:
    """Bounded observation that a public ingress endpoint was checked."""

    ingress_id: str
    hostname: str
    url: str
    target: PublicIngressTarget
    observed_at: str
    status: PublicIngressObservationStatus
    evidence: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_reference(self.ingress_id, "public ingress id")
        _validate_hostname(self.hostname)
        _validate_public_url(self.url)
        if not isinstance(self.target, PublicIngressTarget):
            raise PublicIngressContractError(
                "public ingress observation target must be PublicIngressTarget"
            )
        _validate_text(self.observed_at, "public ingress observed_at")
        if not isinstance(self.status, PublicIngressObservationStatus):
            raise PublicIngressContractError(
                "public ingress observation status must be closed"
            )
        evidence = _evidence_mapping(self.evidence or {})
        object.__setattr__(self, "evidence", evidence)

    def descriptor(self) -> dict[str, object]:
        return {
            "ingress_id": self.ingress_id,
            "hostname": self.hostname,
            "url": self.url,
            "target": self.target.descriptor(),
            "observed_at": self.observed_at,
            "status": self.status.value,
            "evidence": dict(self.evidence),
        }


ObservedPublicEndpoint = PublicIngressObservation


class PublicIngressObservationCodec:
    """Strict codec for bounded public ingress observations."""

    def encode(self, observation: PublicIngressObservation) -> dict[str, object]:
        if not isinstance(observation, PublicIngressObservation):
            raise PublicIngressContractError(
                "encode requires PublicIngressObservation"
            )
        return observation.descriptor()

    def decode(self, descriptor: Mapping[str, object]) -> PublicIngressObservation:
        mapping = _mapping(descriptor, "public ingress observation")
        _require_keys(
            mapping,
            _PUBLIC_INGRESS_OBSERVATION_KEYS,
            "public ingress observation",
        )
        status = mapping.get("status")
        if not isinstance(status, str):
            raise PublicIngressContractError(
                "public ingress observation status must be text"
            )
        try:
            status_value = PublicIngressObservationStatus(status)
        except ValueError as error:
            raise PublicIngressContractError(
                "public ingress observation status is unknown"
            ) from error
        return PublicIngressObservation(
            ingress_id=_text(mapping, "ingress_id"),
            hostname=_text(mapping, "hostname"),
            url=_text(mapping, "url"),
            target=PublicIngressTargetCodec().decode(
                _mapping(mapping.get("target"), "target")
            ),
            observed_at=_text(mapping, "observed_at"),
            status=status_value,
            evidence=_mapping(mapping.get("evidence"), "evidence"),
        )


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise PublicIngressContractError(f"{label} descriptor must be a mapping")
    return value


def _require_keys(
    mapping: Mapping[str, object],
    expected: frozenset[str],
    label: str,
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
    raise PublicIngressContractError(f"invalid {label}; " + "; ".join(details))


def _text(mapping: Mapping[str, object], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str):
        raise PublicIngressContractError(f"{key} must be text")
    return value


def _validate_reference(value: str, label: str) -> None:
    if not isinstance(value, str) or not _REFERENCE.fullmatch(value):
        raise PublicIngressContractError(
            f"{label} must be a bounded lowercase identifier"
        )
    _reject_secret_text(value, label)


def _validate_node_id(value: str) -> None:
    if not isinstance(value, str) or not _NODE_ID.fullmatch(value):
        raise PublicIngressContractError("public ingress target node_id is invalid")
    _reject_secret_text(value, "public ingress target node_id")


def _validate_socket(value: str) -> None:
    if not isinstance(value, str) or not _SOCKET.fullmatch(value):
        raise PublicIngressContractError(
            "public ingress target provider_socket is invalid"
        )
    _reject_secret_text(value, "public ingress target provider_socket")


def _validate_hostname(value: str) -> None:
    if not isinstance(value, str) or not value or len(value) > 253:
        raise PublicIngressContractError("public ingress hostname is invalid")
    value = value.lower()
    if value.endswith(".") or ".." in value:
        raise PublicIngressContractError("public ingress hostname is invalid")
    labels = value.split(".")
    if len(labels) < 2 or not all(_HOST_LABEL.fullmatch(label) for label in labels):
        raise PublicIngressContractError("public ingress hostname is invalid")
    _reject_secret_text(value, "public ingress hostname")


def _validate_public_url(value: str) -> None:
    if not isinstance(value, str) or not value.startswith("https://"):
        raise PublicIngressContractError("public ingress url must be https")
    _validate_text(value, "public ingress url")
    _reject_secret_text(value, "public ingress url")


def _validate_text(value: str, label: str) -> None:
    if not isinstance(value, str) or not value or len(value) > _MAX_TEXT:
        raise PublicIngressContractError(f"{label} must be bounded text")
    _reject_secret_text(value, label)


def _evidence_mapping(value: Mapping[str, object]) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise PublicIngressContractError("public ingress evidence must be a mapping")
    if len(value) > 32:
        raise PublicIngressContractError("public ingress evidence has too many fields")
    checked: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not key or len(key) > 64:
            raise PublicIngressContractError(
                "public ingress evidence keys must be bounded text"
            )
        _reject_secret_text(key, "public ingress evidence key")
        if isinstance(item, str):
            _validate_text(item, "public ingress evidence value")
            checked[key] = item
        elif isinstance(item, (int, float, bool)) or item is None:
            checked[key] = item
        else:
            raise PublicIngressContractError(
                "public ingress evidence values must be scalar"
            )
    return checked


def _reject_secret_text(value: str, label: str) -> None:
    lowered = value.lower()
    secret_markers = (
        "secret=",
        "token=",
        "password=",
        "private-key",
        "begin-private-key",
        "-----begin",
        "cf_tunnel",
        "eyj",
    )
    if any(marker in lowered for marker in secret_markers):
        raise PublicIngressContractError(f"{label} must not contain secret material")
