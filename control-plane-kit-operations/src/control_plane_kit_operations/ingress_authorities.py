"""Durable named public ingress authority admission for operations."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from hashlib import sha256
import re
from typing import Any, Mapping

from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.public_ingress import IngressAuthorityReference
from control_plane_kit_core.secrets import SecretReference


_IDENTIFIER = re.compile(r"^[a-z][a-z0-9._-]{0,127}$")
_HOST_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_HOST_PATTERN_LABEL = re.compile(r"^[a-z0-9*](?:[a-z0-9-*]{0,61}[a-z0-9*])?$")
_SECRET_MARKERS = (
    "secret",
    "token",
    "password",
    "private_key",
    "private-key",
    "api_key",
    "apikey",
    "credential",
)


class IngressAuthorityRegistrationError(ValueError):
    """Raised when ingress authority registration data is malformed."""


class IngressAuthorityRegistrationConflict(IngressAuthorityRegistrationError):
    """Raised when authority replacement requires an explicit decision."""


class IngressAuthorityAuthorizationDenied(IngressAuthorityRegistrationError):
    """Raised when an actor lacks a focused ingress authority scope."""


class IngressAuthorityNotFound(IngressAuthorityRegistrationError):
    """Raised when an ingress authority cannot be found."""


class IngressAuthorityProviderKind(StrEnum):
    """Closed provider kinds supported by operations."""

    CLOUDFLARE = "cloudflare"


class RegisteredIngressAuthorityStatus(StrEnum):
    """Closed durable status for workspace ingress authority registration."""

    ACTIVE = "active"
    REVOKED = "revoked"


@dataclass(frozen=True)
class CloudflareZoneIngressAuthority:
    """Authority to allocate named public ingress inside one Cloudflare zone."""

    account_id: str
    zone_id: str
    zone_name: str
    api_token_ref: SecretReference = field(repr=False)
    allowed_hostname_pattern: str

    def __post_init__(self) -> None:
        _validate_identifier(self.account_id, "Cloudflare account_id")
        _validate_identifier(self.zone_id, "Cloudflare zone_id")
        _validate_zone_name(self.zone_name)
        _require_secret_reference(self.api_token_ref, "api_token_ref")
        _validate_hostname_pattern(
            self.allowed_hostname_pattern,
            zone_name=self.zone_name,
        )

    def descriptor(self) -> dict[str, object]:
        return {
            "provider_kind": IngressAuthorityProviderKind.CLOUDFLARE.value,
            "account_id": self.account_id,
            "zone_id": self.zone_id,
            "zone_name": self.zone_name,
            "api_token_ref": self.api_token_ref.reference_id,
            "allowed_hostname_pattern": self.allowed_hostname_pattern,
        }

    def storage_descriptor(self) -> dict[str, object]:
        return self.descriptor()

    def allows_hostname(self, hostname: str) -> bool:
        try:
            _validate_hostname(hostname)
        except IngressAuthorityRegistrationError:
            return False
        pattern = re.escape(self.allowed_hostname_pattern.lower()).replace(
            r"\*",
            r"[a-z0-9-]+",
        )
        return re.fullmatch(pattern, hostname.lower()) is not None


IngressAuthority = CloudflareZoneIngressAuthority


class CloudflareZoneIngressAuthorityCodec:
    """Strict storage codec for Cloudflare ingress authorities."""

    def encode(self, authority: IngressAuthority) -> dict[str, object]:
        if not isinstance(authority, CloudflareZoneIngressAuthority):
            raise IngressAuthorityRegistrationError("unsupported ingress authority")
        return authority.storage_descriptor()

    def decode(self, descriptor: Mapping[str, object]) -> IngressAuthority:
        mapping = _mapping(descriptor, "ingress authority")
        _require_keys(
            mapping,
            frozenset(
                {
                    "provider_kind",
                    "account_id",
                    "zone_id",
                    "zone_name",
                    "api_token_ref",
                    "allowed_hostname_pattern",
                }
            ),
            "Cloudflare ingress authority",
        )
        provider_kind = _text(mapping, "provider_kind")
        if provider_kind != IngressAuthorityProviderKind.CLOUDFLARE.value:
            raise IngressAuthorityRegistrationError(
                "unsupported ingress authority provider"
            )
        return CloudflareZoneIngressAuthority(
            account_id=_text(mapping, "account_id"),
            zone_id=_text(mapping, "zone_id"),
            zone_name=_text(mapping, "zone_name"),
            api_token_ref=SecretReference(_text(mapping, "api_token_ref")),
            allowed_hostname_pattern=_text(mapping, "allowed_hostname_pattern"),
        )


@dataclass(frozen=True)
class RegisteredIngressAuthority:
    """An ingress authority admitted as workspace operational truth."""

    registration_id: str
    workspace_id: str
    authority_ref: IngressAuthorityReference
    authority: IngressAuthority
    admitted_by: str
    admitted_at: str
    status: RegisteredIngressAuthorityStatus = RegisteredIngressAuthorityStatus.ACTIVE
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_identifier(self.registration_id, "registration_id")
        _validate_identifier(self.workspace_id, "workspace_id")
        _validate_identifier(self.admitted_by, "admitted_by")
        _validate_identifier(self.admitted_at, "admitted_at")
        if not isinstance(self.authority_ref, IngressAuthorityReference):
            raise IngressAuthorityRegistrationError(
                "registered ingress authority requires IngressAuthorityReference"
            )
        if not isinstance(self.authority, CloudflareZoneIngressAuthority):
            raise IngressAuthorityRegistrationError(
                "registered ingress authority is unsupported"
            )
        if not isinstance(self.status, RegisteredIngressAuthorityStatus):
            raise IngressAuthorityRegistrationError(
                "registered ingress authority status is unsupported"
            )
        if not isinstance(self.metadata, Mapping):
            raise IngressAuthorityRegistrationError(
                "registered ingress authority metadata must be mapping"
            )

    @property
    def provider_kind(self) -> IngressAuthorityProviderKind:
        return IngressAuthorityProviderKind.CLOUDFLARE

    def descriptor(self) -> dict[str, object]:
        return {
            "registration_id": self.registration_id,
            "workspace_id": self.workspace_id,
            "authority_ref": self.authority_ref.reference_id,
            "provider_kind": self.provider_kind.value,
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
        authority_ref: IngressAuthorityReference,
        authority: IngressAuthority,
        admitted_by: str,
        admitted_at: str,
    ) -> "RegisteredIngressAuthority":
        return cls(
            registration_id=ingress_authority_registration_id_for(
                workspace_id,
                authority_ref,
                authority,
            ),
            workspace_id=workspace_id,
            authority_ref=authority_ref,
            authority=authority,
            admitted_by=admitted_by,
            admitted_at=admitted_at,
        )


@dataclass(frozen=True)
class RegisterIngressAuthorityCommand:
    """Application command to admit one named ingress authority."""

    workspace_id: str
    authority_ref: IngressAuthorityReference
    authority: IngressAuthority
    admitted_by: str
    admitted_at: str
    actor_scopes: tuple[PolicyScope, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "actor_scopes", _scopes(self.actor_scopes))
        RegisteredIngressAuthority.from_authority(
            workspace_id=self.workspace_id,
            authority_ref=self.authority_ref,
            authority=self.authority,
            admitted_by=self.admitted_by,
            admitted_at=self.admitted_at,
        )


@dataclass(frozen=True)
class RevokeIngressAuthorityCommand:
    """Application command to revoke one workspace ingress authority."""

    workspace_id: str
    authority_ref: IngressAuthorityReference
    actor_scopes: tuple[PolicyScope, ...]

    def __post_init__(self) -> None:
        _validate_identifier(self.workspace_id, "workspace_id")
        if not isinstance(self.authority_ref, IngressAuthorityReference):
            raise IngressAuthorityRegistrationError(
                "revoke requires IngressAuthorityReference"
            )
        object.__setattr__(self, "actor_scopes", _scopes(self.actor_scopes))


class IngressAuthorityRegistrationService:
    """Application service owning ingress authority transaction boundaries."""

    def __init__(self, unit_of_work_factory: Any) -> None:
        self._unit_of_work_factory = unit_of_work_factory

    def register(
        self,
        command: RegisterIngressAuthorityCommand,
    ) -> RegisteredIngressAuthority:
        if not isinstance(command, RegisterIngressAuthorityCommand):
            raise IngressAuthorityRegistrationError(
                "register requires RegisterIngressAuthorityCommand"
            )
        if PolicyScope.INGRESS_AUTHORITY_REGISTER not in command.actor_scopes:
            raise IngressAuthorityAuthorizationDenied(
                "ingress authority registration requires ingress-authority:register"
            )
        with self._unit_of_work_factory() as unit_of_work:
            registered = unit_of_work.stores.ingress_authorities.register(
                workspace_id=command.workspace_id,
                authority_ref=command.authority_ref,
                authority=command.authority,
                admitted_by=command.admitted_by,
                admitted_at=command.admitted_at,
            )
            unit_of_work.commit()
            return registered

    def revoke(
        self,
        command: RevokeIngressAuthorityCommand,
    ) -> RegisteredIngressAuthority:
        if not isinstance(command, RevokeIngressAuthorityCommand):
            raise IngressAuthorityRegistrationError(
                "revoke requires RevokeIngressAuthorityCommand"
            )
        if PolicyScope.INGRESS_AUTHORITY_REVOKE not in command.actor_scopes:
            raise IngressAuthorityAuthorizationDenied(
                "ingress authority revocation requires ingress-authority:revoke"
            )
        with self._unit_of_work_factory() as unit_of_work:
            registered = unit_of_work.stores.ingress_authorities.revoke(
                command.workspace_id,
                command.authority_ref,
            )
            unit_of_work.commit()
            return registered


def ingress_authority_registration_id_for(
    workspace_id: str,
    authority_ref: IngressAuthorityReference,
    authority: IngressAuthority,
) -> str:
    """Return deterministic identity for one ingress authority admission."""

    _validate_identifier(workspace_id, "workspace_id")
    if not isinstance(authority_ref, IngressAuthorityReference):
        raise IngressAuthorityRegistrationError(
            "ingress authority id requires IngressAuthorityReference"
        )
    encoded = CloudflareZoneIngressAuthorityCodec().encode(authority)
    digest = sha256(
        repr((workspace_id, authority_ref.reference_id, encoded)).encode("utf-8")
    ).hexdigest()
    return f"iauth_{digest}"


def _credential_references(authority: RegisteredIngressAuthority) -> dict[str, object]:
    return {"api_token_ref": authority.authority.api_token_ref.reference_id}


def _require_secret_reference(value: object, field: str) -> None:
    if not isinstance(value, SecretReference):
        raise IngressAuthorityRegistrationError(f"{field} requires SecretReference")


def _validate_identifier(value: str, field: str) -> None:
    if not isinstance(value, str) or not value or len(value) > 512:
        raise IngressAuthorityRegistrationError(f"{field} must be nonempty and bounded")
    if any(ord(character) < 32 for character in value):
        raise IngressAuthorityRegistrationError(
            f"{field} must not contain control characters"
        )


def _validate_zone_name(value: str) -> None:
    _validate_hostname(value)
    if value != value.lower():
        raise IngressAuthorityRegistrationError("zone name must be lowercase")


def _validate_hostname(value: str) -> None:
    if not isinstance(value, str) or not value or len(value) > 253:
        raise IngressAuthorityRegistrationError("hostname must be nonempty and bounded")
    if value.endswith(".") or value.startswith("."):
        raise IngressAuthorityRegistrationError("hostname must not have empty labels")
    labels = value.split(".")
    if len(labels) < 2 or not all(_HOST_LABEL.fullmatch(label) for label in labels):
        raise IngressAuthorityRegistrationError("hostname is malformed")


def _validate_hostname_pattern(pattern: str, *, zone_name: str) -> None:
    if not isinstance(pattern, str) or not pattern:
        raise IngressAuthorityRegistrationError(
            "allowed hostname pattern must be nonempty"
        )
    lowered = pattern.lower()
    if pattern != lowered:
        raise IngressAuthorityRegistrationError(
            "allowed hostname pattern must be lowercase"
        )
    if any(marker in lowered for marker in _SECRET_MARKERS):
        raise IngressAuthorityRegistrationError(
            "allowed hostname pattern must not contain secret-shaped text"
        )
    if lowered.count("*") != 1:
        raise IngressAuthorityRegistrationError(
            "allowed hostname pattern requires exactly one wildcard"
        )
    suffix = f".{zone_name}"
    if not lowered.endswith(suffix):
        raise IngressAuthorityRegistrationError(
            "allowed hostname pattern must be inside the configured zone"
        )
    labels = lowered.split(".")
    if len(labels) != len(zone_name.split(".")) + 1:
        raise IngressAuthorityRegistrationError(
            "allowed hostname pattern must authorize one hostname label in the zone"
        )
    if labels[0] == "*":
        raise IngressAuthorityRegistrationError(
            "allowed hostname pattern must not authorize the whole zone"
        )
    if not all(_HOST_PATTERN_LABEL.fullmatch(label) for label in labels):
        raise IngressAuthorityRegistrationError(
            "allowed hostname pattern is malformed"
        )


def _scopes(value: tuple[PolicyScope, ...]) -> tuple[PolicyScope, ...]:
    if not isinstance(value, tuple):
        raise IngressAuthorityRegistrationError(
            "actor_scopes must be a tuple of PolicyScope"
        )
    if not all(isinstance(scope, PolicyScope) for scope in value):
        raise IngressAuthorityRegistrationError(
            "actor_scopes must contain only PolicyScope"
        )
    return value


def _mapping(value: object, field: str = "mapping") -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise IngressAuthorityRegistrationError(f"{field} must be a mapping")
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
        raise IngressAuthorityRegistrationError(
            f"invalid {field}; " + "; ".join(details)
        )


def _text(mapping: Mapping[str, object], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str):
        raise IngressAuthorityRegistrationError(f"{key} must be a string")
    return value
